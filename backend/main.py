"""API HTTP de KekeTranslate (FastAPI).

Flujo de uso:

    POST /api/jobs            -> sube el audio y encola el trabajo
    GET  /api/jobs            -> lista los trabajos recientes
    GET  /api/jobs/{id}       -> consulta el estado y el resultado
    PATCH /api/jobs/{id}/titulo -> pone nombre a la clase
    POST /api/auth/registro   -> crea una cuenta y abre sesion
    POST /api/auth/entrar     -> abre sesion con correo y contrasena
    POST /api/auth/google     -> abre sesion con una cuenta de Google
    GET  /api/compartido/{token}/... -> lo unico accesible sin cuenta
    GET  /api/jobs/{id}/notes -> descarga los apuntes en Markdown
    GET  /api/jobs/{id}/transcript -> descarga la transcripcion
    DELETE /api/jobs/{id}     -> borra el trabajo y sus ficheros
"""

from __future__ import annotations

import base64
import json
import logging
import uuid
from urllib.parse import urlencode
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import (
    BackgroundTasks,
    Body,
    Depends,
    FastAPI,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import JSONResponse, PlainTextResponse

from .annotator import clave_del_anotador, modelo_del_anotador
from .biblioteca import Biblioteca
from .config import Settings, get_settings
from .models import (
    Grupo,
    Job,
    JobStatus,
    JobSummary,
    Material,
    Nota,
    Permiso,
    Tema,
    TipoMaterial,
    Usuario,
)
from .pdf import PdfSinTexto, extraer_texto
from .pipeline import reanotar_job, run_job
from .store import JobStore
from .tls import usar_certificados_del_sistema
from .usuarios import ErrorDeCuenta, Usuarios

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("keketranslate")

# Extensiones aceptadas. La lista cubre los formatos habituales de grabacion de
# clases; los proveedores de transcripcion extraen el audio de los videos.
ALLOWED_EXTENSIONS = {
    ".mp3", ".m4a", ".wav", ".flac", ".ogg", ".opus", ".aac", ".wma",
    ".mp4", ".mov", ".mkv", ".webm", ".avi",
}

# Tamano de los bloques con los que se escribe la subida en disco. Nunca se
# carga el fichero completo en memoria: una clase de 4 h puede pesar varios GB.
UPLOAD_CHUNK_BYTES = 4 * 1024 * 1024

_store: JobStore | None = None
_biblioteca: Biblioteca | None = None
_usuarios: Usuarios | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicializa el almacen de trabajos al arrancar el servidor."""
    global _store, _biblioteca, _usuarios
    # Antes de nada: sin esto, un antivirus que inspeccione el HTTPS hace
    # fallar todas las llamadas a las APIs con CERTIFICATE_VERIFY_FAILED.
    usar_certificados_del_sistema()

    settings = get_settings()
    _store = JobStore(settings.db_path)
    _biblioteca = Biblioteca(settings.db_path)
    _usuarios = Usuarios(settings.db_path)
    logger.info(
        "KekeTranslate listo | transcripcion=%s | anotador=%s (%s)",
        settings.transcription_provider,
        settings.annotator_provider,
        modelo_del_anotador(settings),
    )
    yield


app = FastAPI(
    title="KekeTranslate",
    description=(
        "Transcripcion y anotacion inteligente de clases largas (2-4 horas). "
        "Convierte una grabacion en apuntes estructurados estilo Notion."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


def get_store() -> JobStore:
    """Dependencia que expone el almacen de trabajos."""
    if _store is None:  # pragma: no cover - solo si se usa fuera del lifespan
        raise RuntimeError("El almacen de trabajos no esta inicializado")
    return _store


def get_biblioteca() -> Biblioteca:
    """Dependencia que expone los grupos, temas, material y notas."""
    if _biblioteca is None:  # pragma: no cover - solo fuera del lifespan
        raise RuntimeError("La biblioteca no esta inicializada")
    return _biblioteca


def get_usuarios() -> Usuarios:
    """Dependencia que expone las cuentas y las sesiones."""
    if _usuarios is None:  # pragma: no cover - solo fuera del lifespan
        raise RuntimeError("Las cuentas no estan inicializadas")
    return _usuarios


# Lo unico que se puede pedir sin haber entrado. Todo lo demas bajo /api queda
# cerrado por el middleware de mas abajo.
RUTAS_ABIERTAS = ("/api/health", "/api/auth/", "/api/compartido/")


def _testigo_de(request: Request) -> str:
    """Saca el testigo de sesion de la cabecera `Authorization`."""
    cabecera = request.headers.get("authorization", "")
    tipo, _, valor = cabecera.partition(" ")
    return valor.strip() if tipo.lower() == "bearer" else ""


@app.middleware("http")
async def exigir_sesion(request: Request, call_next):
    """Cierra la API entera salvo lo que esta explicitamente abierto.

    La proteccion no se pone endpoint por endpoint a proposito. Con treinta
    endpoints, olvidarse de uno es cuestion de tiempo, y un olvido asi no se
    nota: el endpoint sigue funcionando, solo que para cualquiera. Aqui el
    olvido se nota enseguida, porque lo que se olvida deja de responder.

    Este es el agujero que cierra: hasta ahora el testigo de compartir protegia
    el *enlace*, no la API, y quien alcanzara el backend podia leer los apuntes
    de cualquiera.
    """
    ruta = request.url.path
    if ruta.startswith("/api/") and not ruta.startswith(RUTAS_ABIERTAS):
        usuarios = _usuarios
        usuario = (
            usuarios.usuario_de_sesion(_testigo_de(request)) if usuarios else None
        )
        if usuario is None:
            return JSONResponse(
                status_code=401,
                content={"detail": "Necesitas entrar en tu cuenta para esto."},
            )
        request.state.usuario = usuario
    return await call_next(request)


def usuario_actual(request: Request) -> Usuario:
    """La persona que hace la peticion, ya resuelta por el middleware."""
    usuario = getattr(request.state, "usuario", None)
    if usuario is None:  # pragma: no cover - el middleware lo impide antes
        raise HTTPException(status_code=401, detail="Necesitas entrar en tu cuenta.")
    return usuario


@app.get("/api/health")
async def health(settings: Settings = Depends(get_settings)) -> dict:
    """Comprueba que el servicio esta vivo y correctamente configurado."""
    return {
        "status": "ok",
        "transcription_provider": settings.transcription_provider,
        "annotator_provider": settings.annotator_provider,
        "annotator_model": modelo_del_anotador(settings),
        "diarization_enabled": settings.enable_diarization,
        "max_upload_mb": settings.max_upload_mb,
        "max_material_mb": settings.max_material_mb,
        # Se informa de si faltan claves sin exponer su valor.
        "transcription_key_configured": bool(_provider_key(settings)),
        "annotator_key_configured": bool(clave_del_anotador(settings)),
    }


# ---------------------------------------------------------------------------
# Cuentas
# ---------------------------------------------------------------------------

# Donde se cambia un codigo de Google por los datos de la persona. Se habla con
# Google directamente por HTTPS, no a traves del navegador.
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"


def usuario_por_testigo(
    request: Request, usuarios: Usuarios = Depends(get_usuarios)
) -> Usuario:
    """Resuelve la sesion mirando la cabecera.

    Las rutas de `/api/auth/` estan abiertas para poder entrar sin haber
    entrado, asi que las tres que si necesitan identidad la piden aqui en vez
    de heredarla del middleware.
    """
    usuario = usuarios.usuario_de_sesion(_testigo_de(request))
    if usuario is None:
        raise HTTPException(status_code=401, detail="Necesitas entrar en tu cuenta.")
    return usuario


def _entrar(usuarios: Usuarios, usuario: Usuario) -> dict:
    return {"token": usuarios.abrir_sesion(usuario.id), "usuario": usuario}


def _adoptar_lo_que_ya_habia(
    usuario: Usuario, store: JobStore, biblioteca: Biblioteca
) -> None:
    """La primera cuenta se queda con lo que existia antes de haber cuentas.

    Una clase transcrita cuesta dinero y media hora de espera. Dejarla huerfana
    e invisible al introducir las cuentas seria tirar trabajo real por un
    cambio de arquitectura.
    """
    clases = store.adoptar_huerfanos(usuario.id)
    grupos = biblioteca.adoptar_huerfanos(usuario.id)
    if clases or grupos:
        logger.info(
            "La cuenta %s adopta %d clases y %d grupos sin dueno",
            usuario.email, clases, grupos,
        )


@app.post("/api/auth/registro", status_code=201)
async def registrar(
    email: str = Body(...),
    password: str = Body(...),
    nombre: str = Body(""),
    usuarios: Usuarios = Depends(get_usuarios),
    store: JobStore = Depends(get_store),
    biblioteca: Biblioteca = Depends(get_biblioteca),
) -> dict:
    """Crea una cuenta con correo y contrasena, y la deja dentro."""
    primera = not usuarios.hay_cuentas()
    try:
        usuario = usuarios.crear(email, password=password, nombre=nombre)
    except ErrorDeCuenta as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if primera:
        _adoptar_lo_que_ya_habia(usuario, store, biblioteca)
    return _entrar(usuarios, usuario)


@app.post("/api/auth/entrar")
async def entrar(
    email: str = Body(...),
    password: str = Body(...),
    usuarios: Usuarios = Depends(get_usuarios),
) -> dict:
    usuario = usuarios.verificar(email, password)
    if usuario is None:
        # Un solo mensaje para los dos casos. Distinguirlos permitiria
        # averiguar quien tiene cuenta probando correos.
        raise HTTPException(
            status_code=401, detail="El correo o la contraseña no son correctos."
        )
    return _entrar(usuarios, usuario)


@app.post("/api/auth/salir", status_code=204, response_model=None)
async def salir(request: Request, usuarios: Usuarios = Depends(get_usuarios)) -> None:
    """Cierra esta sesion. El testigo deja de valer en el acto."""
    usuarios.cerrar_sesion(_testigo_de(request))


@app.get("/api/auth/yo", response_model=Usuario)
async def quien_soy(usuario: Usuario = Depends(usuario_por_testigo)) -> Usuario:
    return usuario


@app.post("/api/auth/contrasena", status_code=204, response_model=None)
async def poner_contrasena(
    password: str = Body(..., embed=True),
    usuario: Usuario = Depends(usuario_por_testigo),
    usuarios: Usuarios = Depends(get_usuarios),
) -> None:
    """Pone o cambia la contrasena.

    Sirve tambien para quien entro con Google y quiere poder entrar sin el.
    Cierra el resto de sesiones: si se cambia la contrasena porque alguien mas
    la sabia, dejar sus sesiones vivas no arregla nada.
    """
    try:
        usuarios.cambiar_contrasena(usuario.id, password)
    except ErrorDeCuenta as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    usuarios.cerrar_todas(usuario.id)


@app.get("/api/auth/google")
async def configuracion_de_google(settings: Settings = Depends(get_settings)) -> dict:
    """Dice si se puede entrar con Google y con que identificador.

    El `client_id` es publico por diseno: viaja en la URL a la que se manda al
    navegador. El secreto no sale nunca de aqui.
    """
    return {
        "activo": bool(settings.google_client_id and settings.google_client_secret),
        "client_id": settings.google_client_id,
        "url_de_autorizacion": GOOGLE_AUTH_URL,
    }


@app.post("/api/auth/google/inicio")
async def empezar_con_google(
    redirect_uri: str = Body(..., embed=True),
    settings: Settings = Depends(get_settings),
    usuarios: Usuarios = Depends(get_usuarios),
) -> dict:
    """Devuelve la URL de Google a la que mandar el navegador.

    El `state` lo crea y lo guarda el backend, y solo vale una vez: asi no se
    acepta un codigo que venga de un flujo que no empezo aqui.
    """
    if not (settings.google_client_id and settings.google_client_secret):
        raise HTTPException(
            status_code=503,
            detail="Entrar con Google no está configurado en este servidor.",
        )
    estado = usuarios.nuevo_estado()
    consulta = urlencode(
        {
            "client_id": settings.google_client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": estado,
            "prompt": "select_account",
        }
    )
    return {"url": f"{GOOGLE_AUTH_URL}?{consulta}", "state": estado}


@app.post("/api/auth/google")
async def entrar_con_google(
    code: str = Body(...),
    redirect_uri: str = Body(...),
    state: str = Body(""),
    settings: Settings = Depends(get_settings),
    usuarios: Usuarios = Depends(get_usuarios),
    store: JobStore = Depends(get_store),
    biblioteca: Biblioteca = Depends(get_biblioteca),
) -> dict:
    """Cambia el codigo que devolvio Google por una sesion nuestra."""
    if not (settings.google_client_id and settings.google_client_secret):
        raise HTTPException(
            status_code=503,
            detail=(
                "Entrar con Google no está configurado en este servidor. "
                "Usa el correo y la contraseña."
            ),
        )

    if not usuarios.consumir_estado(state):
        raise HTTPException(
            status_code=400,
            detail=(
                "Ese intento de entrar con Google ya no vale. Vuelve a pulsar "
                "el botón para empezar de nuevo."
            ),
        )

    datos = await _canjear_codigo_de_google(code, redirect_uri, settings)
    sub, correo, nombre = datos["sub"], datos["email"], datos["nombre"]

    usuario = usuarios.por_google(sub)
    if usuario is None:
        # Mismo correo, otra forma de entrar: se unen en la cuenta que ya
        # existe en vez de partir los apuntes en dos cuentas distintas.
        usuario = usuarios.por_email(correo)
        if usuario is not None:
            usuarios.vincular_google(usuario.id, sub)
            usuario = usuarios.por_id(usuario.id)
        else:
            primera = not usuarios.hay_cuentas()
            try:
                usuario = usuarios.crear(correo, nombre=nombre, google_sub=sub)
            except ErrorDeCuenta as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            if primera:
                _adoptar_lo_que_ya_habia(usuario, store, biblioteca)

    return _entrar(usuarios, usuario)


async def _canjear_codigo_de_google(
    code: str, redirect_uri: str, settings: Settings
) -> dict:
    """Pide a Google los datos de quien acaba de autorizar.

    El `id_token` que vuelve no se verifica con la clave publica de Google, y
    es correcto no hacerlo: no llego por el navegador sino de una conexion TLS
    directa con Google a cambio de nuestro secreto de cliente. Es el propio
    OpenID Connect el que permite saltarse la verificacion en este caso
    concreto, el del flujo de codigo con cliente confidencial.
    """
    async with httpx.AsyncClient(timeout=20.0) as cliente:
        try:
            respuesta = await cliente.post(
                GOOGLE_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": settings.google_client_id,
                    "client_secret": settings.google_client_secret,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"No se pudo hablar con Google para comprobar tu cuenta: {exc}",
            ) from exc

    if respuesta.status_code >= 400:
        raise HTTPException(
            status_code=401,
            detail=(
                "Google rechazó el acceso. Vuelve a intentarlo desde el botón. "
                f"Dijo: {respuesta.text[:300]}"
            ),
        )

    id_token = respuesta.json().get("id_token", "")
    datos = _leer_id_token(id_token)
    if not datos.get("sub") or not datos.get("email"):
        raise HTTPException(
            status_code=502,
            detail="Google no devolvió el correo de la cuenta; no se puede continuar.",
        )
    return {
        "sub": datos["sub"],
        "email": datos["email"],
        "nombre": datos.get("name", ""),
    }


def _leer_id_token(id_token: str) -> dict:
    """Saca el contenido del JWT sin verificar la firma. Ver la nota de arriba."""
    partes = id_token.split(".")
    if len(partes) != 3:
        raise HTTPException(
            status_code=502, detail="Google devolvió una credencial ilegible."
        )
    relleno = "=" * (-len(partes[1]) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(partes[1] + relleno))
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=502, detail="Google devolvió una credencial ilegible."
        ) from exc


@app.post("/api/jobs", response_model=Job, status_code=201)
async def create_job(
    background_tasks: BackgroundTasks,
    file: UploadFile,
    grupo_id: str | None = None,
    tema_id: str | None = None,
    settings: Settings = Depends(get_settings),
    store: JobStore = Depends(get_store),
    biblioteca: Biblioteca = Depends(get_biblioteca),
    usuario: Usuario = Depends(usuario_actual),
) -> Job:
    """Sube una grabacion y encola su transcripcion y anotacion.

    Con `grupo_id` la clase queda archivada en ese grupo, y el material de la
    materia (programa, guias) entra en el prompt del anotador.
    """
    filename = Path(file.filename or "grabacion").name
    extension = Path(filename).suffix.lower()

    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=(
                f"No se puede usar un fichero {extension or 'sin extensión'}. "
                f"Formatos admitidos: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
            ),
        )

    if grupo_id:
        _require_grupo(grupo_id, biblioteca, usuario)

    job_id = uuid.uuid4().hex[:12]
    destination = settings.uploads_dir / f"{job_id}_{filename}"

    size = await _save_upload(file, destination, settings.max_upload_bytes)

    job = store.create(
        Job(
            id=job_id,
            filename=filename,
            status=JobStatus.PENDING,
            file_size_bytes=size,
            provider=settings.transcription_provider,
            usuario_id=usuario.id,
            grupo_id=grupo_id,
            tema_id=tema_id,
        )
    )

    # El procesado ocurre fuera del ciclo peticion/respuesta: la transcripcion
    # de una clase larga tarda mucho mas de lo que aguanta una conexion HTTP.
    background_tasks.add_task(run_job, job_id, settings, store, biblioteca)
    logger.info("Trabajo %s encolado (%s, %.1f MB)", job_id, filename, size / 1e6)
    return job


@app.post("/api/jobs/{job_id}/reanotar", response_model=Job)
async def reanotar(
    job_id: str,
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings),
    store: JobStore = Depends(get_store),
    biblioteca: Biblioteca = Depends(get_biblioteca),
    usuario: Usuario = Depends(usuario_actual),
) -> Job:
    """Reintenta solo la generacion de apuntes, reutilizando la transcripcion.

    Existe porque el anotador falla por causas ajenas al usuario (el modelo se
    satura o agota la cuota gratuita) y la transcripcion, que es la parte que
    se paga, ya esta hecha. Sin esto habria que volver a subir la clase entera.
    """
    job = _require_job(job_id, store, usuario)

    if not (job.transcript_diarized or job.transcript_text):
        raise HTTPException(
            status_code=409,
            detail=(
                "Esta clase no llegó a transcribirse, así que no hay nada "
                "que reaprovechar: tienes que subir el audio de nuevo."
            ),
        )

    if job.status in (JobStatus.UPLOADING, JobStatus.TRANSCRIBING, JobStatus.ANNOTATING):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Esta clase ya se está procesando ({job.progress_label.lower()}). "
                "Espera a que termine."
            ),
        )

    background_tasks.add_task(reanotar_job, job_id, settings, store, biblioteca)
    logger.info("Trabajo %s reencolado para reanotar", job_id)
    return store.update(job_id, status=JobStatus.ANNOTATING, error=None)


@app.get("/api/jobs", response_model=list[JobSummary])
async def list_jobs(
    limit: int = 50,
    grupo_id: str | None = None,
    store: JobStore = Depends(get_store),
    usuario: Usuario = Depends(usuario_actual),
) -> list[JobSummary]:
    """Lista los trabajos mas recientes, opcionalmente los de un grupo."""
    trabajos = store.list(limit=limit, usuario_id=usuario.id)
    if grupo_id is None:
        return trabajos
    return [t for t in trabajos if t.grupo_id == grupo_id]


@app.patch("/api/jobs/{job_id}/ubicacion", response_model=Job)
async def archivar_job(
    job_id: str,
    grupo_id: str | None = None,
    tema_id: str | None = None,
    store: JobStore = Depends(get_store),
    biblioteca: Biblioteca = Depends(get_biblioteca),
    usuario: Usuario = Depends(usuario_actual),
) -> Job:
    """Archiva una clase en un grupo y tema, o la saca de donde estaba.

    Sin `grupo_id` la clase vuelve a quedar suelta. Cambiar de sitio no rehace
    los apuntes: si se quiere que el material del nuevo grupo influya, hay que
    pedir el reintento a mano.
    """
    _require_job(job_id, store, usuario)

    if grupo_id:
        _require_grupo(grupo_id, biblioteca, usuario)
    if tema_id:
        _require_tema(tema_id, biblioteca, usuario)

    return store.update(job_id, grupo_id=grupo_id, tema_id=tema_id if grupo_id else None)


@app.patch("/api/jobs/{job_id}/titulo", response_model=Job)
async def renombrar_job(
    job_id: str,
    titulo: str = Body(..., embed=True),
    store: JobStore = Depends(get_store),
    usuario: Usuario = Depends(usuario_actual),
) -> Job:
    """Pone nombre a una clase, o se lo quita si llega vacio.

    Sin titulo la clase se muestra por el nombre del fichero, que es lo que
    habia antes: renombrar nunca borra ese dato, solo lo tapa.
    """
    _require_job(job_id, store, usuario)
    limpio = titulo.strip()
    if len(limpio) > 120:
        raise HTTPException(
            status_code=422,
            detail="El nombre de la clase no puede pasar de 120 caracteres.",
        )
    return store.update(job_id, titulo=limpio or None)


@app.put("/api/jobs/{job_id}/notes", response_model=Job)
async def editar_notes(
    job_id: str,
    contenido: str = Body(..., embed=True),
    store: JobStore = Depends(get_store),
    usuario: Usuario = Depends(usuario_actual),
) -> Job:
    """Guarda la version corregida a mano de los apuntes.

    Se guarda aparte de lo que genero la IA: asi se puede volver al original y,
    sobre todo, rehacer los apuntes no borra las correcciones propias.
    """
    _require_job(job_id, store, usuario)
    return store.update(job_id, notes_editadas=contenido)


@app.delete("/api/jobs/{job_id}/notes", response_model=Job)
async def descartar_edicion(
    job_id: str,
    store: JobStore = Depends(get_store),
    usuario: Usuario = Depends(usuario_actual),
) -> Job:
    """Descarta las correcciones propias y vuelve a los apuntes de la IA."""
    _require_job(job_id, store, usuario)
    return store.update(job_id, notes_editadas=None)


@app.get("/api/jobs/{job_id}", response_model=Job)
async def get_job(
    job_id: str,
    store: JobStore = Depends(get_store),
    usuario: Usuario = Depends(usuario_actual),
) -> Job:
    """Estado completo de una clase, con transcripcion y apuntes."""
    return _require_job(job_id, store, usuario)


@app.get("/api/jobs/{job_id}/notes", response_class=PlainTextResponse)
async def get_notes(
    job_id: str,
    store: JobStore = Depends(get_store),
    usuario: Usuario = Depends(usuario_actual),
) -> str:
    """Devuelve los apuntes en Markdown."""
    job = _require_job(job_id, store, usuario)
    if not job.notes_markdown:
        raise HTTPException(
            status_code=409,
            detail=_motivo_no_disponible(job, "Los apuntes todavía no están listos"),
        )
    return job.notes_markdown


@app.get("/api/jobs/{job_id}/transcript", response_class=PlainTextResponse)
async def get_transcript(
    job_id: str,
    store: JobStore = Depends(get_store),
    usuario: Usuario = Depends(usuario_actual),
) -> str:
    """Devuelve la transcripcion con oradores y marcas de tiempo."""
    job = _require_job(job_id, store, usuario)
    transcript = job.transcript_diarized or job.transcript_text
    if not transcript:
        raise HTTPException(
            status_code=409,
            detail=_motivo_no_disponible(job, "La transcripción todavía no está lista"),
        )
    return transcript


@app.delete("/api/jobs/{job_id}", status_code=204, response_model=None)
async def delete_job(
    job_id: str,
    settings: Settings = Depends(get_settings),
    store: JobStore = Depends(get_store),
    usuario: Usuario = Depends(usuario_actual),
) -> None:
    """Borra un trabajo junto con su audio, transcripcion y apuntes."""
    job = _require_job(job_id, store, usuario)

    (settings.uploads_dir / f"{job_id}_{job.filename}").unlink(missing_ok=True)
    (settings.results_dir / f"{job_id}_apuntes.md").unlink(missing_ok=True)
    (settings.results_dir / f"{job_id}_transcripcion.txt").unlink(missing_ok=True)
    store.delete(job_id)


# ---------------------------------------------------------------------------
# Biblioteca: grupos
# ---------------------------------------------------------------------------


@app.post("/api/grupos", response_model=Grupo, status_code=201)
async def crear_grupo(
    nombre: str = Body(...),
    materia: str = Body(...),
    biblioteca: Biblioteca = Depends(get_biblioteca),
    usuario: Usuario = Depends(usuario_actual),
) -> Grupo:
    """Crea un grupo. Nace privado: compartirlo es un paso aparte."""
    if not nombre.strip() or not materia.strip():
        raise HTTPException(
            status_code=400,
            detail="Escribe un nombre y una materia para crear el grupo.",
        )
    return biblioteca.crear_grupo(nombre, materia, usuario.id)


@app.get("/api/grupos", response_model=list[Grupo])
async def listar_grupos(
    biblioteca: Biblioteca = Depends(get_biblioteca),
    usuario: Usuario = Depends(usuario_actual),
) -> list[Grupo]:
    return biblioteca.listar_grupos(usuario.id)


@app.get("/api/grupos/{grupo_id}", response_model=Grupo)
async def ver_grupo(
    grupo_id: str,
    biblioteca: Biblioteca = Depends(get_biblioteca),
    usuario: Usuario = Depends(usuario_actual),
) -> Grupo:
    return _require_grupo(grupo_id, biblioteca, usuario)


@app.patch("/api/grupos/{grupo_id}", response_model=Grupo)
async def renombrar_grupo(
    grupo_id: str,
    nombre: str = Body(...),
    materia: str = Body(...),
    biblioteca: Biblioteca = Depends(get_biblioteca),
    usuario: Usuario = Depends(usuario_actual),
) -> Grupo:
    _require_grupo(grupo_id, biblioteca, usuario)
    return biblioteca.renombrar_grupo(grupo_id, nombre, materia)


@app.delete("/api/grupos/{grupo_id}", status_code=204, response_model=None)
async def borrar_grupo(
    grupo_id: str,
    settings: Settings = Depends(get_settings),
    biblioteca: Biblioteca = Depends(get_biblioteca),
    store: JobStore = Depends(get_store),
    usuario: Usuario = Depends(usuario_actual),
) -> None:
    """Borra el grupo y su contenido. Las clases transcritas se conservan.

    Una clase cuesta dinero y una espera larga; que se borre por reorganizar
    carpetas seria desproporcionado. Se quedan sin archivar.
    """
    _require_grupo(grupo_id, biblioteca, usuario)

    for material in biblioteca.listar_materiales(grupo_id):
        _ruta_material(settings, material).unlink(missing_ok=True)

    for trabajo in store.list(limit=1000, usuario_id=usuario.id):
        if trabajo.grupo_id == grupo_id:
            store.update(trabajo.id, grupo_id=None, tema_id=None)

    biblioteca.borrar_grupo(grupo_id)


@app.post("/api/grupos/{grupo_id}/compartir", response_model=Grupo)
async def compartir_grupo(
    grupo_id: str,
    permiso: Permiso = Permiso.LECTURA,
    biblioteca: Biblioteca = Depends(get_biblioteca),
    usuario: Usuario = Depends(usuario_actual),
) -> Grupo:
    """Genera el enlace y decide si quien entre puede escribir o solo leer."""
    _require_grupo(grupo_id, biblioteca, usuario)
    return biblioteca.compartir(grupo_id, permiso)


@app.delete("/api/grupos/{grupo_id}/compartir", response_model=Grupo)
async def dejar_de_compartir(
    grupo_id: str,
    biblioteca: Biblioteca = Depends(get_biblioteca),
    usuario: Usuario = Depends(usuario_actual),
) -> Grupo:
    """Invalida el enlace: quien lo tuviera deja de tener acceso."""
    _require_grupo(grupo_id, biblioteca, usuario)
    return biblioteca.dejar_de_compartir(grupo_id)


def _grupo_del_enlace(token: str, biblioteca: Biblioteca) -> Grupo:
    """Resuelve un enlace compartido, o lanza un 404 explicando por que.

    Es la unica puerta abierta de la API sin cuenta, y solo abre el grupo al
    que apunta: ni las demas clases de esa persona, ni sus otros grupos.
    """
    grupo = biblioteca.grupo_por_token(token)
    if grupo is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "Este enlace ya no funciona. Puede que quien lo creó haya "
                "dejado de compartir el grupo. Pídele uno nuevo."
            ),
        )
    return grupo


def _con_permiso_de_escritura(token: str, biblioteca: Biblioteca) -> Grupo:
    grupo = _grupo_del_enlace(token, biblioteca)
    if grupo.share_permiso is not Permiso.ESCRITURA:
        raise HTTPException(
            status_code=403,
            detail="Este enlace es de solo lectura. No puedes escribir aquí.",
        )
    return grupo


@app.get("/api/compartido/{token}", response_model=Grupo)
async def resolver_enlace(
    token: str, biblioteca: Biblioteca = Depends(get_biblioteca)
) -> Grupo:
    """Resuelve un enlace compartido al grupo que apunta."""
    grupo = _grupo_del_enlace(token, biblioteca)
    # Quien llega por el enlace no tiene por que saber quien es el dueno.
    return grupo.model_copy(update={"usuario_id": None})


@app.get("/api/compartido/{token}/temas", response_model=list[Tema])
async def temas_compartidos(
    token: str, biblioteca: Biblioteca = Depends(get_biblioteca)
) -> list[Tema]:
    return biblioteca.listar_temas(_grupo_del_enlace(token, biblioteca).id)


@app.get("/api/compartido/{token}/materiales", response_model=list[Material])
async def materiales_compartidos(
    token: str, biblioteca: Biblioteca = Depends(get_biblioteca)
) -> list[Material]:
    return biblioteca.listar_materiales(_grupo_del_enlace(token, biblioteca).id)


@app.get("/api/compartido/{token}/notas", response_model=list[Nota])
async def notas_compartidas(
    token: str, biblioteca: Biblioteca = Depends(get_biblioteca)
) -> list[Nota]:
    return biblioteca.listar_notas(_grupo_del_enlace(token, biblioteca).id)


@app.post("/api/compartido/{token}/notas", response_model=Nota, status_code=201)
async def crear_nota_compartida(
    token: str,
    titulo: str = Body(...),
    contenido: str = Body(""),
    tema_id: str | None = Body(None),
    biblioteca: Biblioteca = Depends(get_biblioteca),
) -> Nota:
    grupo = _con_permiso_de_escritura(token, biblioteca)
    return biblioteca.crear_nota(grupo.id, titulo, contenido, tema_id=tema_id)


@app.put("/api/compartido/{token}/notas/{nota_id}", response_model=Nota)
async def editar_nota_compartida(
    token: str,
    nota_id: str,
    titulo: str | None = Body(None),
    contenido: str | None = Body(None),
    biblioteca: Biblioteca = Depends(get_biblioteca),
) -> Nota:
    grupo = _con_permiso_de_escritura(token, biblioteca)
    nota = biblioteca.nota(nota_id)
    # La nota tiene que ser de *este* grupo. Sin esta comprobacion, un enlace
    # de escritura a un grupo cualquiera valdria para editar las notas de todos
    # los demas, que es el mismo agujero de antes por otra puerta.
    if nota is None or nota.grupo_id != grupo.id:
        raise HTTPException(
            status_code=404,
            detail="Esa nota no existe. Puede que se haya borrado.",
        )
    return biblioteca.actualizar_nota(nota_id, titulo=titulo, contenido=contenido)


@app.get("/api/compartido/{token}/clases", response_model=list[JobSummary])
async def clases_compartidas(
    token: str,
    biblioteca: Biblioteca = Depends(get_biblioteca),
    store: JobStore = Depends(get_store),
) -> list[JobSummary]:
    """Las clases archivadas en el grupo compartido, y solo esas."""
    grupo = _grupo_del_enlace(token, biblioteca)
    return [
        c
        for c in store.list(limit=1000, usuario_id=grupo.usuario_id)
        if c.grupo_id == grupo.id
    ]


@app.get("/api/compartido/{token}/clases/{job_id}", response_model=Job)
async def clase_compartida(
    token: str,
    job_id: str,
    biblioteca: Biblioteca = Depends(get_biblioteca),
    store: JobStore = Depends(get_store),
) -> Job:
    grupo = _grupo_del_enlace(token, biblioteca)
    job = store.get(job_id)
    if job is None or job.grupo_id != grupo.id:
        raise HTTPException(
            status_code=404,
            detail="Esa clase no existe. Puede que se haya borrado.",
        )
    return job


# ---------------------------------------------------------------------------
# Biblioteca: temas
# ---------------------------------------------------------------------------


@app.post("/api/grupos/{grupo_id}/temas", response_model=Tema, status_code=201)
async def crear_tema(
    grupo_id: str,
    nombre: str = Body(..., embed=True),
    biblioteca: Biblioteca = Depends(get_biblioteca),
    usuario: Usuario = Depends(usuario_actual),
) -> Tema:
    _require_grupo(grupo_id, biblioteca, usuario)
    if not nombre.strip():
        raise HTTPException(
            status_code=400, detail="Escribe un nombre para el tema."
        )
    return biblioteca.crear_tema(grupo_id, nombre)


@app.get("/api/grupos/{grupo_id}/temas", response_model=list[Tema])
async def listar_temas(
    grupo_id: str,
    biblioteca: Biblioteca = Depends(get_biblioteca),
    usuario: Usuario = Depends(usuario_actual),
) -> list[Tema]:
    _require_grupo(grupo_id, biblioteca, usuario)
    return biblioteca.listar_temas(grupo_id)


@app.delete("/api/temas/{tema_id}", status_code=204, response_model=None)
async def borrar_tema(
    tema_id: str,
    biblioteca: Biblioteca = Depends(get_biblioteca),
    store: JobStore = Depends(get_store),
    usuario: Usuario = Depends(usuario_actual),
) -> None:
    """Borra la seccion. Su material, notas y clases quedan en el grupo."""
    _require_tema(tema_id, biblioteca, usuario)

    for trabajo in store.list(limit=1000, usuario_id=usuario.id):
        if trabajo.tema_id == tema_id:
            store.update(trabajo.id, tema_id=None)

    biblioteca.borrar_tema(tema_id)


# ---------------------------------------------------------------------------
# Biblioteca: material (PDF)
# ---------------------------------------------------------------------------


@app.post(
    "/api/grupos/{grupo_id}/materiales", response_model=Material, status_code=201
)
async def subir_material(
    grupo_id: str,
    file: UploadFile,
    tema_id: str | None = Form(None),
    tipo: TipoMaterial = Form(TipoMaterial.MATERIAL),
    settings: Settings = Depends(get_settings),
    biblioteca: Biblioteca = Depends(get_biblioteca),
    usuario: Usuario = Depends(usuario_actual),
) -> Material:
    """Adjunta un PDF al grupo y extrae su texto para que la IA pueda leerlo."""
    _require_grupo(grupo_id, biblioteca, usuario)

    filename = Path(file.filename or "documento.pdf").name
    if Path(filename).suffix.lower() != ".pdf":
        raise HTTPException(
            status_code=415,
            detail=(
                "El material de la materia tiene que ser un PDF. "
                "Por ahora no se admiten otros formatos."
            ),
        )

    material_id = uuid.uuid4().hex[:12]
    destino = settings.materiales_dir / f"{material_id}_{filename}"
    await _save_upload(file, destino, settings.max_material_bytes)

    try:
        texto, paginas = extraer_texto(destino)
    except PdfSinTexto as exc:
        # El fichero se descarta: guardarlo sin poder leerlo solo generaria la
        # falsa impresion de que la IA lo esta teniendo en cuenta.
        destino.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    material = biblioteca.guardar_material(
        grupo_id, filename, texto, tema_id=tema_id, tipo=tipo, paginas=paginas
    )
    # El fichero se renombra al id definitivo que asigno la biblioteca.
    destino.rename(settings.materiales_dir / f"{material.id}_{filename}")
    logger.info(
        "Material %s anadido al grupo %s (%d paginas, %d caracteres)",
        material.id, grupo_id, paginas, len(texto),
    )
    return material


@app.get("/api/grupos/{grupo_id}/materiales", response_model=list[Material])
async def listar_materiales(
    grupo_id: str,
    tema_id: str | None = None,
    biblioteca: Biblioteca = Depends(get_biblioteca),
    usuario: Usuario = Depends(usuario_actual),
) -> list[Material]:
    _require_grupo(grupo_id, biblioteca, usuario)
    return biblioteca.listar_materiales(grupo_id, tema_id=tema_id)


@app.delete("/api/materiales/{material_id}", status_code=204, response_model=None)
async def borrar_material(
    material_id: str,
    settings: Settings = Depends(get_settings),
    biblioteca: Biblioteca = Depends(get_biblioteca),
    usuario: Usuario = Depends(usuario_actual),
) -> None:
    material = _require_material(material_id, biblioteca, usuario)
    _ruta_material(settings, material).unlink(missing_ok=True)
    biblioteca.borrar_material(material_id)


# ---------------------------------------------------------------------------
# Biblioteca: notas propias
# ---------------------------------------------------------------------------


@app.post("/api/grupos/{grupo_id}/notas", response_model=Nota, status_code=201)
async def crear_nota(
    grupo_id: str,
    titulo: str = Body(...),
    contenido: str = Body(""),
    tema_id: str | None = Body(None),
    biblioteca: Biblioteca = Depends(get_biblioteca),
    usuario: Usuario = Depends(usuario_actual),
) -> Nota:
    _require_grupo(grupo_id, biblioteca, usuario)
    if not titulo.strip():
        raise HTTPException(
            status_code=400, detail="Escribe un título para la nota."
        )
    return biblioteca.crear_nota(grupo_id, titulo, contenido, tema_id=tema_id)


@app.get("/api/grupos/{grupo_id}/notas", response_model=list[Nota])
async def listar_notas(
    grupo_id: str,
    tema_id: str | None = None,
    biblioteca: Biblioteca = Depends(get_biblioteca),
    usuario: Usuario = Depends(usuario_actual),
) -> list[Nota]:
    _require_grupo(grupo_id, biblioteca, usuario)
    return biblioteca.listar_notas(grupo_id, tema_id=tema_id)


@app.put("/api/notas/{nota_id}", response_model=Nota)
async def editar_nota(
    nota_id: str,
    titulo: str | None = Body(None),
    contenido: str | None = Body(None),
    biblioteca: Biblioteca = Depends(get_biblioteca),
    usuario: Usuario = Depends(usuario_actual),
) -> Nota:
    _require_nota(nota_id, biblioteca, usuario)
    nota = biblioteca.actualizar_nota(nota_id, titulo=titulo, contenido=contenido)
    if nota is None:
        raise HTTPException(
            status_code=404,
            detail="Esa nota no existe. Puede que se haya borrado.",
        )
    return nota


@app.delete("/api/notas/{nota_id}", status_code=204, response_model=None)
async def borrar_nota(
    nota_id: str,
    biblioteca: Biblioteca = Depends(get_biblioteca),
    usuario: Usuario = Depends(usuario_actual),
) -> None:
    _require_nota(nota_id, biblioteca, usuario)
    if not biblioteca.borrar_nota(nota_id):
        raise HTTPException(
            status_code=404,
            detail="Esa nota no existe. Puede que se haya borrado.",
        )


# ---------------------------------------------------------------------------
# Auxiliares
# ---------------------------------------------------------------------------


def _require_grupo(grupo_id: str, biblioteca: Biblioteca, usuario: Usuario) -> Grupo:
    """Recupera un grupo propio, o lanza un 404.

    Un grupo de otra persona da 404 y no 403 a proposito. Un 403 confirmaria
    que ese identificador existe, y con eso se puede averiguar cuantos grupos
    tiene alguien y cuando crea uno nuevo. Para quien pregunta por algo que no
    es suyo, no existe.
    """
    grupo = biblioteca.grupo(grupo_id)
    if grupo is None or grupo.usuario_id != usuario.id:
        raise HTTPException(
            status_code=404,
            detail="Ese grupo no existe. Puede que se haya borrado.",
        )
    return grupo


def _require_tema(tema_id: str, biblioteca: Biblioteca, usuario: Usuario) -> Tema:
    """Un tema es del dueno de su grupo."""
    tema = biblioteca.tema(tema_id)
    if tema is None:
        raise HTTPException(
            status_code=404,
            detail="Ese tema no existe. Puede que se haya borrado.",
        )
    _require_grupo(tema.grupo_id, biblioteca, usuario)
    return tema


def _require_material(
    material_id: str, biblioteca: Biblioteca, usuario: Usuario
) -> Material:
    material = biblioteca.material(material_id)
    if material is None:
        raise HTTPException(
            status_code=404,
            detail="Ese material no existe. Puede que se haya borrado.",
        )
    _require_grupo(material.grupo_id, biblioteca, usuario)
    return material


def _require_nota(nota_id: str, biblioteca: Biblioteca, usuario: Usuario) -> Nota:
    nota = biblioteca.nota(nota_id)
    if nota is None:
        raise HTTPException(
            status_code=404,
            detail="Esa nota no existe. Puede que se haya borrado.",
        )
    _require_grupo(nota.grupo_id, biblioteca, usuario)
    return nota


def _ruta_material(settings: Settings, material: Material) -> Path:
    return settings.materiales_dir / f"{material.id}_{material.filename}"


def _motivo_no_disponible(job: Job, en_espera: str) -> str:
    """Explica por que un resultado no esta disponible todavia.

    Un trabajo fallido no esta "en camino": decirle al usuario que espere lo
    deja mirando una pantalla que nunca va a cambiar, asi que se le devuelve
    el error real.
    """
    if job.status is JobStatus.FAILED:
        return job.error or "La clase falló y no se guardó el motivo."
    return f"{en_espera}: {job.progress_label.lower()}."


def _require_job(job_id: str, store: JobStore, usuario: Usuario) -> Job:
    """Recupera una clase propia, o lanza un 404. Ver `_require_grupo`."""
    job = store.get(job_id)
    if job is None or job.usuario_id != usuario.id:
        raise HTTPException(
            status_code=404,
            detail="Esa clase no existe. Puede que se haya borrado.",
        )
    return job


def _provider_key(settings: Settings) -> str:
    """Devuelve la clave del proveedor de transcripcion activo."""
    return {
        "assemblyai": settings.assemblyai_api_key,
        "deepgram": settings.deepgram_api_key,
        "openai": settings.openai_api_key,
    }.get(settings.transcription_provider, "")


async def _save_upload(file: UploadFile, destination: Path, max_bytes: int) -> int:
    """Guarda la subida en disco por bloques y devuelve su tamano.

    Si se supera el limite, se aborta y se borra el fichero parcial en lugar de
    llenar el disco con una subida que de todos modos seria rechazada.
    """
    size = 0
    try:
        with destination.open("wb") as handle:
            while chunk := await file.read(UPLOAD_CHUNK_BYTES):
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            "La grabación pesa más de "
                            f"{max_bytes // (1024 * 1024)} MB, que es el máximo. "
                            "Si es una clase muy larga, súbela en dos partes."
                        ),
                    )
                handle.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise

    if size == 0:
        destination.unlink(missing_ok=True)
        raise HTTPException(
            status_code=400,
            detail=(
                "El fichero llegó vacío. Comprueba que la grabación se "
                "guardó bien y vuelve a subirla."
            ),
        )

    return size
