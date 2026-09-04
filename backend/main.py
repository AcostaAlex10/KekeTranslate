"""API HTTP de KekeTranslate (FastAPI).

Flujo de uso:

    POST /api/jobs            -> sube el audio y encola el trabajo
    GET  /api/jobs            -> lista los trabajos recientes
    GET  /api/jobs/{id}       -> consulta el estado y el resultado
    PATCH /api/jobs/{id}/titulo -> pone nombre a la clase
    GET  /api/jobs/{id}/notes -> descarga los apuntes en Markdown
    GET  /api/jobs/{id}/transcript -> descarga la transcripcion
    DELETE /api/jobs/{id}     -> borra el trabajo y sus ficheros
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import (
    BackgroundTasks,
    Body,
    Depends,
    FastAPI,
    Form,
    HTTPException,
    UploadFile,
)
from fastapi.responses import PlainTextResponse

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
)
from .pdf import PdfSinTexto, extraer_texto
from .pipeline import reanotar_job, run_job
from .store import JobStore
from .tls import usar_certificados_del_sistema

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicializa el almacen de trabajos al arrancar el servidor."""
    global _store, _biblioteca
    # Antes de nada: sin esto, un antivirus que inspeccione el HTTPS hace
    # fallar todas las llamadas a las APIs con CERTIFICATE_VERIFY_FAILED.
    usar_certificados_del_sistema()

    settings = get_settings()
    _store = JobStore(settings.db_path)
    _biblioteca = Biblioteca(settings.db_path)
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


@app.post("/api/jobs", response_model=Job, status_code=201)
async def create_job(
    background_tasks: BackgroundTasks,
    file: UploadFile,
    grupo_id: str | None = None,
    tema_id: str | None = None,
    settings: Settings = Depends(get_settings),
    store: JobStore = Depends(get_store),
    biblioteca: Biblioteca = Depends(get_biblioteca),
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

    if grupo_id and biblioteca.grupo(grupo_id) is None:
        raise HTTPException(
            status_code=404,
            detail="Ese grupo no existe. Puede que se haya borrado.",
        )

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
) -> Job:
    """Reintenta solo la generacion de apuntes, reutilizando la transcripcion.

    Existe porque el anotador falla por causas ajenas al usuario (el modelo se
    satura o agota la cuota gratuita) y la transcripcion, que es la parte que
    se paga, ya esta hecha. Sin esto habria que volver a subir la clase entera.
    """
    job = store.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail="Esa clase no existe. Puede que se haya borrado.",
        )

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
) -> list[JobSummary]:
    """Lista los trabajos mas recientes, opcionalmente los de un grupo."""
    trabajos = store.list(limit=limit)
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
) -> Job:
    """Archiva una clase en un grupo y tema, o la saca de donde estaba.

    Sin `grupo_id` la clase vuelve a quedar suelta. Cambiar de sitio no rehace
    los apuntes: si se quiere que el material del nuevo grupo influya, hay que
    pedir el reintento a mano.
    """
    _require_job(job_id, store)

    if grupo_id and biblioteca.grupo(grupo_id) is None:
        raise HTTPException(
            status_code=404,
            detail="Ese grupo no existe. Puede que se haya borrado.",
        )
    if tema_id and biblioteca.tema(tema_id) is None:
        raise HTTPException(
            status_code=404,
            detail="Ese tema no existe. Puede que se haya borrado.",
        )

    return store.update(job_id, grupo_id=grupo_id, tema_id=tema_id if grupo_id else None)


@app.patch("/api/jobs/{job_id}/titulo", response_model=Job)
async def renombrar_job(
    job_id: str,
    titulo: str = Body(..., embed=True),
    store: JobStore = Depends(get_store),
) -> Job:
    """Pone nombre a una clase, o se lo quita si llega vacio.

    Sin titulo la clase se muestra por el nombre del fichero, que es lo que
    habia antes: renombrar nunca borra ese dato, solo lo tapa.
    """
    _require_job(job_id, store)
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
) -> Job:
    """Guarda la version corregida a mano de los apuntes.

    Se guarda aparte de lo que genero la IA: asi se puede volver al original y,
    sobre todo, rehacer los apuntes no borra las correcciones propias.
    """
    _require_job(job_id, store)
    return store.update(job_id, notes_editadas=contenido)


@app.delete("/api/jobs/{job_id}/notes", response_model=Job)
async def descartar_edicion(
    job_id: str, store: JobStore = Depends(get_store)
) -> Job:
    """Descarta las correcciones propias y vuelve a los apuntes de la IA."""
    _require_job(job_id, store)
    return store.update(job_id, notes_editadas=None)


@app.get("/api/jobs/{job_id}", response_model=Job)
async def get_job(job_id: str, store: JobStore = Depends(get_store)) -> Job:
    """Devuelve el estado completo de un trabajo, con transcripcion y apuntes."""
    job = store.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail="Esa clase no existe. Puede que se haya borrado.",
        )
    return job


@app.get("/api/jobs/{job_id}/notes", response_class=PlainTextResponse)
async def get_notes(job_id: str, store: JobStore = Depends(get_store)) -> str:
    """Devuelve los apuntes en Markdown."""
    job = _require_job(job_id, store)
    if not job.notes_markdown:
        raise HTTPException(
            status_code=409,
            detail=_motivo_no_disponible(job, "Los apuntes todavía no están listos"),
        )
    return job.notes_markdown


@app.get("/api/jobs/{job_id}/transcript", response_class=PlainTextResponse)
async def get_transcript(job_id: str, store: JobStore = Depends(get_store)) -> str:
    """Devuelve la transcripcion con oradores y marcas de tiempo."""
    job = _require_job(job_id, store)
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
) -> None:
    """Borra un trabajo junto con su audio, transcripcion y apuntes."""
    job = _require_job(job_id, store)

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
) -> Grupo:
    """Crea un grupo. Nace privado: compartirlo es un paso aparte."""
    if not nombre.strip() or not materia.strip():
        raise HTTPException(
            status_code=400,
            detail="Escribe un nombre y una materia para crear el grupo.",
        )
    return biblioteca.crear_grupo(nombre, materia)


@app.get("/api/grupos", response_model=list[Grupo])
async def listar_grupos(biblioteca: Biblioteca = Depends(get_biblioteca)) -> list[Grupo]:
    return biblioteca.listar_grupos()


@app.get("/api/grupos/{grupo_id}", response_model=Grupo)
async def ver_grupo(
    grupo_id: str, biblioteca: Biblioteca = Depends(get_biblioteca)
) -> Grupo:
    return _require_grupo(grupo_id, biblioteca)


@app.patch("/api/grupos/{grupo_id}", response_model=Grupo)
async def renombrar_grupo(
    grupo_id: str,
    nombre: str = Body(...),
    materia: str = Body(...),
    biblioteca: Biblioteca = Depends(get_biblioteca),
) -> Grupo:
    _require_grupo(grupo_id, biblioteca)
    return biblioteca.renombrar_grupo(grupo_id, nombre, materia)


@app.delete("/api/grupos/{grupo_id}", status_code=204, response_model=None)
async def borrar_grupo(
    grupo_id: str,
    settings: Settings = Depends(get_settings),
    biblioteca: Biblioteca = Depends(get_biblioteca),
    store: JobStore = Depends(get_store),
) -> None:
    """Borra el grupo y su contenido. Las clases transcritas se conservan.

    Una clase cuesta dinero y una espera larga; que se borre por reorganizar
    carpetas seria desproporcionado. Se quedan sin archivar.
    """
    _require_grupo(grupo_id, biblioteca)

    for material in biblioteca.listar_materiales(grupo_id):
        _ruta_material(settings, material).unlink(missing_ok=True)

    for trabajo in store.list(limit=1000):
        if trabajo.grupo_id == grupo_id:
            store.update(trabajo.id, grupo_id=None, tema_id=None)

    biblioteca.borrar_grupo(grupo_id)


@app.post("/api/grupos/{grupo_id}/compartir", response_model=Grupo)
async def compartir_grupo(
    grupo_id: str,
    permiso: Permiso = Permiso.LECTURA,
    biblioteca: Biblioteca = Depends(get_biblioteca),
) -> Grupo:
    """Genera el enlace y decide si quien entre puede escribir o solo leer."""
    _require_grupo(grupo_id, biblioteca)
    return biblioteca.compartir(grupo_id, permiso)


@app.delete("/api/grupos/{grupo_id}/compartir", response_model=Grupo)
async def dejar_de_compartir(
    grupo_id: str, biblioteca: Biblioteca = Depends(get_biblioteca)
) -> Grupo:
    """Invalida el enlace: quien lo tuviera deja de tener acceso."""
    _require_grupo(grupo_id, biblioteca)
    return biblioteca.dejar_de_compartir(grupo_id)


@app.get("/api/compartido/{token}", response_model=Grupo)
async def resolver_enlace(
    token: str, biblioteca: Biblioteca = Depends(get_biblioteca)
) -> Grupo:
    """Resuelve un enlace compartido al grupo que apunta."""
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


# ---------------------------------------------------------------------------
# Biblioteca: temas
# ---------------------------------------------------------------------------


@app.post("/api/grupos/{grupo_id}/temas", response_model=Tema, status_code=201)
async def crear_tema(
    grupo_id: str,
    nombre: str = Body(..., embed=True),
    biblioteca: Biblioteca = Depends(get_biblioteca),
) -> Tema:
    _require_grupo(grupo_id, biblioteca)
    if not nombre.strip():
        raise HTTPException(
            status_code=400, detail="Escribe un nombre para el tema."
        )
    return biblioteca.crear_tema(grupo_id, nombre)


@app.get("/api/grupos/{grupo_id}/temas", response_model=list[Tema])
async def listar_temas(
    grupo_id: str, biblioteca: Biblioteca = Depends(get_biblioteca)
) -> list[Tema]:
    _require_grupo(grupo_id, biblioteca)
    return biblioteca.listar_temas(grupo_id)


@app.delete("/api/temas/{tema_id}", status_code=204, response_model=None)
async def borrar_tema(
    tema_id: str,
    biblioteca: Biblioteca = Depends(get_biblioteca),
    store: JobStore = Depends(get_store),
) -> None:
    """Borra la seccion. Su material, notas y clases quedan en el grupo."""
    if biblioteca.tema(tema_id) is None:
        raise HTTPException(
            status_code=404,
            detail="Ese tema no existe. Puede que se haya borrado.",
        )

    for trabajo in store.list(limit=1000):
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
) -> Material:
    """Adjunta un PDF al grupo y extrae su texto para que la IA pueda leerlo."""
    _require_grupo(grupo_id, biblioteca)

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
) -> list[Material]:
    _require_grupo(grupo_id, biblioteca)
    return biblioteca.listar_materiales(grupo_id, tema_id=tema_id)


@app.delete("/api/materiales/{material_id}", status_code=204, response_model=None)
async def borrar_material(
    material_id: str,
    settings: Settings = Depends(get_settings),
    biblioteca: Biblioteca = Depends(get_biblioteca),
) -> None:
    material = biblioteca.material(material_id)
    if material is None:
        raise HTTPException(
        status_code=404,
        detail="Ese documento no existe. Puede que se haya borrado.",
    )
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
) -> Nota:
    _require_grupo(grupo_id, biblioteca)
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
) -> list[Nota]:
    _require_grupo(grupo_id, biblioteca)
    return biblioteca.listar_notas(grupo_id, tema_id=tema_id)


@app.put("/api/notas/{nota_id}", response_model=Nota)
async def editar_nota(
    nota_id: str,
    titulo: str | None = Body(None),
    contenido: str | None = Body(None),
    biblioteca: Biblioteca = Depends(get_biblioteca),
) -> Nota:
    nota = biblioteca.actualizar_nota(nota_id, titulo=titulo, contenido=contenido)
    if nota is None:
        raise HTTPException(
            status_code=404,
            detail="Esa nota no existe. Puede que se haya borrado.",
        )
    return nota


@app.delete("/api/notas/{nota_id}", status_code=204, response_model=None)
async def borrar_nota(
    nota_id: str, biblioteca: Biblioteca = Depends(get_biblioteca)
) -> None:
    if not biblioteca.borrar_nota(nota_id):
        raise HTTPException(
            status_code=404,
            detail="Esa nota no existe. Puede que se haya borrado.",
        )


# ---------------------------------------------------------------------------
# Auxiliares
# ---------------------------------------------------------------------------


def _require_grupo(grupo_id: str, biblioteca: Biblioteca) -> Grupo:
    grupo = biblioteca.grupo(grupo_id)
    if grupo is None:
        raise HTTPException(
            status_code=404,
            detail="Ese grupo no existe. Puede que se haya borrado.",
        )
    return grupo


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


def _require_job(job_id: str, store: JobStore) -> Job:
    """Recupera un trabajo o lanza un 404."""
    job = store.get(job_id)
    if job is None:
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
