"""Interfaz de KekeTranslate en Streamlit.

Habla con el backend de FastAPI por HTTP, asi que ambos procesos pueden vivir
en maquinas distintas. La URL se configura con `BACKEND_URL`.
"""

from __future__ import annotations

import json
import os
import unicodedata
from datetime import datetime

import httpx
import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv

load_dotenv()

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000").rstrip("/")

# La subida de una clase de varias horas puede tardar minutos: el timeout de
# escritura se desactiva para no cortar la transferencia a mitad.
UPLOAD_TIMEOUT = httpx.Timeout(connect=30.0, read=600.0, write=None, pool=30.0)

ESTADOS_EN_CURSO = {"pending", "uploading", "transcribing", "annotating"}

# Cuanto se reutiliza una lectura del backend. Corto a proposito: lo justo para
# que un clic no relance la pantalla entera, pero no tanto como para que un
# trabajo en curso parezca detenido.
SEGUNDOS_DE_CACHE = 8

# Cada cuanto se repinta "Mis clases" mientras haya una clase procesandose.
# Cadencia comoda: el procesado dura entre 10 y 30 minutos, asi que sondear mas
# a menudo no adelantaria nada y solo cargaria al backend.
SEGUNDOS_ENTRE_REFRESCOS = 15

# `centered` y no `wide`: con `wide` el contenido se estira hasta el borde y los
# apuntes se leian a unos 190 caracteres por linea en una pantalla de 1920, muy
# por encima de los 65-75 en los que el ojo encuentra el renglon siguiente sin
# perderse. Y los apuntes son justo aquello para lo que existe el producto: leer
# una clase de cuatro horas. `centered` acota el ancho por si solo, sin depender
# de las clases internas del DOM de Streamlit.
# El icono de la pestana del navegador sigue siendo un emoji: Streamlit solo
# acepta emoji o una imagen ahi, y mantener un fichero propio para 16 pixeles no
# se justifica.
st.set_page_config(page_title="KekeTranslate", page_icon="🎓", layout="centered")


def declarar_idioma(codigo: str = "es") -> None:
    """Marca el documento como escrito en espanol.

    Streamlit deja `<html lang="en">` fijo y no ofrece ningun ajuste para
    cambiarlo, asi que un lector de pantalla pronuncia todo el contenido con
    fonemas ingleses: "Transcripción" suena a ingles mal leido. Se corrige desde
    un componente de altura cero que toca el documento padre.

    Cuando el producto sea multiidioma, este es el sitio donde el codigo tendra
    que venir del idioma elegido en vez de estar fijo.
    """
    components.html(
        f"<script>window.parent.document.documentElement.lang = '{codigo}';</script>",
        height=0,
    )


declarar_idioma()


def corregir_contraste_del_selector() -> None:
    """Devuelve legibilidad a la etiqueta del selector de vista.

    Streamlit pinta la opcion elegida de `segmented_control` con `primaryColor`
    como color de **letra**, y ese mismo token tiene que ser oscuro para que el
    texto blanco del boton primario se lea encima. Las dos exigencias son
    incompatibles: esta calculado que con este lienzo no existe ningun color que
    cumpla ambas, y el mejor compromiso posible se queda en 4.29:1. La etiqueta
    elegida acababa en 3.41:1, por debajo del minimo, y encima menos legible que
    las no elegidas.

    Lo mismo le pasa a `st.tabs`, que es lo que agrupa el contenido de un grupo:
    "Clases" activa estaba en 3.41:1 mientras las tres inactivas estaban en
    15.01:1, o sea que la elegida era la mas dificil de leer.

    Se corrige solo el color del texto. La opcion elegida sigue distinguiendose
    por su borde y su relleno —la pestana, por su subrayado—, asi que la
    seleccion nunca depende del color de la letra. El enganche es un `data-testid`, que es el punto de
    extension estable de Streamlit y no una clase generada; si algun dia
    desaparece, lo peor que ocurre es volver al estado de hoy.
    """
    st.markdown(
        """
        <style>
        [data-testid="stBaseButton-segmented_controlActive"] p,
        [data-testid="stTab"][aria-selected="true"] p {
            color: #E6E8EF;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


corregir_contraste_del_selector()


def alinear_las_filas_de_lista() -> None:
    """Alinea a la izquierda el nombre de las filas que abren una clase.

    Streamlit centra la etiqueta de todos sus botones. En un boton de accion eso
    esta bien; en una lista de veinte clases rompe lo unico que hace legible una
    lista, que es el borde izquierdo por el que baja el ojo: cada nombre
    empezaba en una sangria distinta segun su longitud.

    El enganche es `st-key-<clave>`, la clase que Streamlit deriva de la clave
    que uno mismo le pone al widget. No es una clase generada ni un hash: cambia
    solo si cambio yo la clave. Y si algun dia deja de existir, lo peor que
    ocurre es volver a los nombres centrados de hoy.

    La segunda regla da aire encima de los encabezados de materia. Un encabezado
    pertenece a lo que viene detras, no a lo que queda arriba, y Streamlit los
    deja con margen cero: cada materia empezaba pegada a la ultima clase de la
    anterior y los dos bloques se leian como uno solo.
    """
    filas = ", ".join(
        f'[class*="st-key-{prefijo}"] button'
        for prefijo in ("abrir_", "ir_a_clase_", "sh_abrir_", "volver_al_indice")
    )
    st.markdown(
        f"""
        <style>
        {filas} {{
            justify-content: flex-start;
            text-align: left;
        }}
        [data-testid="stMarkdownContainer"] h6 {{
            margin-top: 1.75rem;
            margin-bottom: 0.15rem;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


alinear_las_filas_de_lista()


def hacer_visibles_los_estados() -> None:
    """Devuelve contraste al raton encima y al foco de teclado.

    Los dos estados usaban `primaryColor`, que sobre este lienzo casi negro se
    apaga. Medido: la fila con el raton encima caia a 3.41:1 —o sea que
    senalarla la hacia *menos* legible que las de al lado— y el anillo de foco
    quedaba en 1.75:1, por debajo del 3:1 que pide una marca no textual.

    El raton encima pasa a marcarse con un fondo tenue, que es lo que hace
    cualquier lista, y deja el texto donde estaba. El foco se dibuja con el
    color del texto, que es lo unico que se ve seguro sobre este fondo.

    `:focus-visible` no es una clase interna de Streamlit sino CSS estandar, y
    solo se activa cuando se navega con el teclado: quien usa el raton no ve
    ningun anillo.
    """
    # Tanto el raton encima como el foco tinen la etiqueta de `primaryColor`.
    filas = ", ".join(
        f'[class*="st-key-{prefijo}"] button:{estado}'
        for prefijo in ("abrir_", "ir_a_clase_", "sh_abrir_", "volver_al_indice")
        for estado in ("hover", "focus", "focus-visible", "active")
    )
    st.markdown(
        f"""
        <style>
        {filas} {{
            color: #E6E8EF;
            background-color: rgba(230, 232, 239, 0.06);
        }}
        button:focus-visible,
        [role="tab"]:focus-visible,
        [role="radio"]:focus-visible,
        input:focus-visible,
        textarea:focus-visible,
        select:focus-visible,
        summary:focus-visible {{
            outline: 2px solid #E6E8EF !important;
            outline-offset: 2px;
            box-shadow: none !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


hacer_visibles_los_estados()


# ---------------------------------------------------------------------------
# Cliente del backend
# ---------------------------------------------------------------------------


@st.cache_resource
def cliente() -> httpx.Client:
    """Cliente HTTP reutilizado entre recargas.

    Streamlit vuelve a ejecutar el script entero ante cualquier interaccion, y
    una pantalla llena hace mas de diez peticiones. Abriendo una conexion nueva
    cada vez, ese coste se paga entero en cada clic.
    """
    return httpx.Client(base_url=BACKEND_URL, timeout=30.0)


def testigo() -> str:
    """El testigo de sesion de quien esta usando la app ahora mismo."""
    return st.session_state.get("sesion") or ""


def _cabeceras(testigo_de_sesion: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {testigo_de_sesion}"} if testigo_de_sesion else {}


@st.cache_data(ttl=SEGUNDOS_DE_CACHE, show_spinner="Cargando…")
def _leer(path: str, params: tuple[tuple[str, str], ...], testigo_de_sesion: str):
    """Lectura cacheada del backend.

    Sin esto, cambiar de grupo o desplegar una clase relanzaba **todas** las
    peticiones de la pantalla, incluidas las de los apuntes completos de cada
    clase, que son las mas pesadas. La cache se vacia sola en unos segundos y a
    mano en cuanto se escribe algo, asi que no llega a mostrar datos viejos.

    El testigo entra como parametro y no como cabecera fija por una razon que no
    es de estilo: `st.cache_data` es unica para todo el servidor, no una por
    persona. Si la clave de cache no incluyera quien pregunta, la segunda
    persona en pedir "/api/jobs" recibiria la lista de la primera.
    """
    respuesta = cliente().get(
        path, params=dict(params), headers=_cabeceras(testigo_de_sesion)
    )
    respuesta.raise_for_status()
    return respuesta.json()


def api_get(path: str, **params):
    """GET contra el backend. Devuelve `None` si no se pudo leer.

    Se distinguen dos fallos que antes se confundian, porque `HTTPStatusError`
    hereda de `HTTPError`: que el servidor no conteste, y que contest e con un
    error. Al mezclarlos, un 404 perfectamente respondido se anunciaba como
    "no hay conexion, comprueba que esta arrancado" —falso, y encima con la URL
    interna del backend a la vista—. Quien abria un enlace compartido caducado
    veia esa caja roja sobre el mensaje correcto, y concluia que la app estaba
    rota cuando solo estaba diciendo que el enlace ya no vale.
    """
    try:
        return _leer(path, tuple(sorted(params.items())), testigo())
    except httpx.HTTPStatusError as exc:
        # El backend contesto y explico por que. Ese texto es el bueno; quien
        # llama decide si mostrarlo o dar su propio mensaje.
        detalle = ""
        try:
            detalle = exc.response.json().get("detail", "")
        except ValueError:
            detalle = exc.response.text
        st.session_state["ultimo_error_api"] = detalle
        return None
    except httpx.HTTPError as exc:
        st.error(
            "No hay conexión con el servidor de KekeTranslate. "
            "Comprueba que está arrancado y vuelve a cargar la página.\n\n"
            f"Detalle técnico: {exc}"
        )
        return None


def refrescar() -> None:
    """Descarta lo cacheado. Se llama tras escribir y al pulsar *Actualizar*."""
    _leer.clear()


def api_llamar(metodo: str, path: str, **kwargs):
    """Llamada al backend que devuelve `None` y muestra el motivo si falla.

    Los errores del backend traen un `detail` pensado para leerse; se prefiere
    ese texto al codigo HTTP, que no le dice nada a quien usa la app.
    """
    try:
        kwargs.setdefault("headers", {}).update(_cabeceras(testigo()))
        respuesta = cliente().request(metodo, path, timeout=60.0, **kwargs)
    except httpx.HTTPError as exc:
        st.error(
            "No hay conexión con el servidor. No se guardó ningún cambio; "
            "vuelve a intentarlo.\n\n"
            f"Detalle técnico: {exc}"
        )
        return None

    if respuesta.status_code >= 400:
        try:
            st.error(respuesta.json().get("detail", respuesta.text))
        except ValueError:
            st.error(respuesta.text)
        return None

    # Se acaba de cambiar algo: lo cacheado ya no vale.
    refrescar()

    if respuesta.status_code == 204 or not respuesta.content:
        return {}
    return respuesta.json()


def upload_file(
    uploaded,
    nombre: str | None = None,
    grupo_id: str | None = None,
    tema_id: str | None = None,
) -> dict | None:
    """Sube la grabacion y devuelve el trabajo creado.

    `nombre` permite bautizar lo que se acaba de grabar: el microfono no
    entrega un nombre de fichero, y el backend valida el formato por la
    extension, asi que sin el la subida se rechazaria con un 415.

    Con `grupo_id` la clase queda archivada en ese grupo desde el principio, de
    modo que el material de la materia entra ya en los primeros apuntes.
    """
    nombre = nombre or getattr(uploaded, "name", None) or "grabacion.wav"
    tipo = getattr(uploaded, "type", None) or "application/octet-stream"
    files = {"file": (nombre, uploaded, tipo)}
    params = {k: v for k, v in {"grupo_id": grupo_id, "tema_id": tema_id}.items() if v}
    try:
        response = httpx.post(
            f"{BACKEND_URL}/api/jobs",
            files=files,
            params=params,
            headers=_cabeceras(testigo()),
            timeout=UPLOAD_TIMEOUT,
        )
        if response.status_code >= 400:
            st.error(response.json().get("detail", response.text))
            return None
        return response.json()
    except httpx.HTTPError as exc:
        st.error(
            "Se cortó la subida. El fichero no llegó completo, así que "
            "no se procesó nada. Vuelve a intentarlo.\n\n"
            f"Detalle técnico: {exc}"
        )
        return None


def _mostrar_transcripcion_rescatada(job_id: str, filename: str) -> None:
    """Rescata lo que si sobrevivio a una clase fallida.

    Casi todos los fallos ocurren al redactar los apuntes, no al transcribir, y
    el primer principio del producto dice que la transcripcion nunca se tira.
    Pero hasta ahora no habia forma de leerla ni descargarla desde una clase
    fallida: el activo mas caro quedaba invisible justo cuando mas tranquiliza
    saber que sigue ahi. Se afirma que existe en vez de insinuarlo con un "si".
    """
    detalle = api_get(f"/api/jobs/{job_id}")
    transcripcion = (detalle or {}).get("transcript_diarized") or ""

    if not transcripcion:
        st.caption(
            "Esta clase no llegó a transcribirse, así que hay que subir el "
            "audio otra vez."
        )
        return

    st.success(
        "Tu transcripción está guardada y no se perdió. Los apuntes se pueden "
        "rehacer sin volver a subir la clase."
    )

    # Un interruptor y no un `expander`: esto ya vive dentro del desplegable de
    # la clase, y Streamlit revienta la pantalla entera al anidar dos. Ademas
    # es el mismo gesto que abre la edicion de los apuntes, asi que se aprende
    # una sola vez.
    if st.toggle("Ver la transcripción", key=f"ver_tx_{job_id}"):
        st.text_area(
            "Transcripción con oradores y marcas de tiempo",
            value=transcripcion,
            height=320,
            key=f"tx_fallida_{job_id}",
            label_visibility="collapsed",
            disabled=True,
        )

    st.download_button(
        "Descargar transcripción (.txt)",
        icon=":material/download:",
        data=transcripcion,
        file_name=f"{filename}_transcripcion.txt",
        mime="text/plain",
        key=f"dl_tx_fallida_{job_id}",
    )

    _ofrecer_reintento(job_id)


def _ofrecer_reintento(job_id: str) -> None:
    """Boton para rehacer los apuntes de un trabajo que fallo al anotar."""
    if not st.button(
        "Reintentar apuntes",
        key=f"reintentar-{job_id}",
        icon=":material/refresh:",
    ):
        return

    try:
        respuesta = httpx.post(
            f"{BACKEND_URL}/api/jobs/{job_id}/reanotar",
            headers=_cabeceras(testigo()),
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        st.error(
            "No hay conexión con el servidor. La transcripción sigue guardada; "
            "vuelve a intentarlo.\n\n"
            f"Detalle técnico: {exc}"
        )
        return

    if respuesta.status_code >= 400:
        st.error(respuesta.json().get("detail", respuesta.text))
        return

    st.success("Volviendo a escribir los apuntes. Tarda menos de un minuto.")
    st.rerun()


def limite_efectivo_mb() -> int:
    """Tope real de subida, en MB.

    Hay dos limites y manda el mas bajo: el del backend (`MAX_UPLOAD_MB`) y el
    de Streamlit (`server.maxUploadSize`), que corta la subida en el navegador
    antes de que llegue a la API. Mostrar solo el del backend hacia que la
    pantalla anunciara 5 GB junto a un selector que rechazaba a los 200 MB.
    """
    tope_backend = (salud or {}).get("max_upload_mb", 0)
    tope_streamlit = int(st.get_option("server.maxUploadSize") or 0)
    topes = [t for t in (tope_backend, tope_streamlit) if t > 0]
    return min(topes) if topes else 0


# Iconos de Material Symbols, que Streamlit trae dentro. Antes eran emojis, y un
# emoji no es un sistema de iconos: cada uno viene de una familia distinta, con
# su trazo y su color propios, y un lector de pantalla lee "check mark" o "cross
# mark" en lugar del estado.
# Solo los dos estados finales llevan color, y son los de siempre en esta app:
# verde lo que salio bien, rojo lo que fallo. Lo que esta en curso se queda en
# el color del texto, porque no es ni una cosa ni la otra y tenirlo de ambar lo
# convertiria en un aviso que no es. El color nunca va solo: la fila dice el
# estado en palabras al lado de la fecha.
ICONOS_DE_ESTADO = {
    "pending": ":material/schedule:",
    "uploading": ":material/upload:",
    "transcribing": ":material/mic:",
    "annotating": ":material/neurology:",
    "completed": ":green[:material/check_circle:]",
    "failed": ":red[:material/error:]",
}

# El estado en palabras. El icono solo no basta: se lee mal en voz alta y obliga
# a recordar que significa cada dibujo.
PALABRA_DE_ESTADO = {
    "pending": "En cola",
    "uploading": "Subiendo",
    "transcribing": "Transcribiendo",
    "annotating": "Escribiendo apuntes",
    "completed": "Lista",
    "failed": "Falló",
}

ETIQUETAS_EN_CURSO = {
    "pending": "En cola",
    "uploading": "Subiendo el audio",
    "transcribing": "Transcribiendo la clase",
    "annotating": "Escribiendo los apuntes",
}

def sin_acentos(texto: str) -> str:
    """Aplana el texto para buscar: nadie escribe los acentos al buscar."""
    descompuesto = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in descompuesto if not unicodedata.combining(c))


def nombre_de(clase: dict) -> str:
    """Como se llama una clase en pantalla: su titulo, o el del fichero."""
    return (clase.get("titulo") or "").strip() or clase["filename"]


def nombre_para_fichero(texto: str) -> str:
    """Deja un nombre que cualquier sistema de ficheros acepte."""
    limpio = "".join(c if (c.isalnum() or c in " -_.") else "_" for c in texto)
    return limpio.strip().replace(" ", "_") or "clase"


def fecha_corta(marca_iso: str) -> str:
    """Fecha para el indice: dia y mes, con el ano solo si no es el actual."""
    momento = datetime.fromisoformat(marca_iso)
    if momento.year == datetime.now(momento.tzinfo).year:
        return momento.strftime("%d/%m")
    return momento.strftime("%d/%m/%y")


def formatear_duracion(segundos: float | None) -> str:
    """Convierte segundos en un texto del tipo `3 h 12 min`."""
    if not segundos:
        return "—"
    total_minutos = int(segundos // 60)
    horas, minutos = divmod(total_minutos, 60)
    return f"{horas} h {minutos:02d} min" if horas else f"{minutos} min"


def confirmar_borrado(
    clave: str, etiqueta: str, advertencia: str, *, contenedor=None
) -> bool:
    """Boton destructivo en dos tiempos. Devuelve `True` solo al confirmar.

    Las cuatro acciones que borran cosas se ejecutaban al primer clic, sin
    deshacer, y con las consecuencias escritas **debajo** del boton: quien lo
    pulsaba leia lo que acababa de perder. Aqui el aviso va antes y hace falta
    un segundo clic deliberado, con la accion nombrada en el boton en vez de un
    "Si" o un "Aceptar".

    No usa un dialogo modal a proposito: estas acciones aparecen dentro de
    expanders y de solapas, donde Streamlit no admite anidar segun que cosas.
    """
    destino = contenedor if contenedor is not None else st
    pedida = st.session_state.get(f"confirmar_{clave}", False)

    if not pedida:
        if destino.button(etiqueta, key=f"pedir_{clave}"):
            st.session_state[f"confirmar_{clave}"] = True
            st.rerun()
        return False

    st.warning(advertencia)
    columna_si, columna_no = st.columns(2)
    if columna_si.button(etiqueta, key=f"confirmar_si_{clave}", type="primary"):
        st.session_state.pop(f"confirmar_{clave}", None)
        return True
    if columna_no.button("Cancelar", key=f"confirmar_no_{clave}"):
        st.session_state.pop(f"confirmar_{clave}", None)
        st.rerun()
    return False


def columna_de_lectura():
    """Devuelve un contenedor acotado para la prosa larga.

    Los apuntes de una clase de cuatro horas son varias pantallas seguidas de
    texto corrido, y el ojo pierde el renglon siguiente cuando la linea pasa de
    unos 75 caracteres. El resto de la interfaz —formularios, listas, la
    transcripcion— no lo necesita y se queda a ancho completo, asi que el
    bloque estrecho se lee como lo que es: la zona de lectura.
    """
    # La proporcion salio de medir, no de estimar: con 9:1 el parrafo caia en
    # 82 caracteres por linea y con 4:1 en 73, dentro del rango.
    lectura, _ = st.columns([4, 1])
    return lectura


def transcurrido_desde(marca_iso: str) -> str:
    """Tiempo que lleva una clase procesandose, en lenguaje natural.

    Es la unica senal honesta de que algo se mueve mientras se espera: sube
    sola y no promete un porcentaje que nadie puede calcular.
    """
    try:
        inicio = datetime.fromisoformat(marca_iso)
    except ValueError:
        return "hace un momento"

    ahora = datetime.now(inicio.tzinfo) if inicio.tzinfo else datetime.now()
    minutos = int((ahora - inicio).total_seconds() // 60)

    if minutos < 1:
        return "recién empezada"
    if minutos == 1:
        return "hace 1 minuto"
    if minutos < 60:
        return f"hace {minutos} minutos"

    horas, restantes = divmod(minutos, 60)
    if restantes == 0:
        return f"hace {horas} h"
    return f"hace {horas} h {restantes:02d} min"


# ---------------------------------------------------------------------------
# Barra lateral: configuracion activa
# ---------------------------------------------------------------------------

st.sidebar.title("KekeTranslate")
st.sidebar.caption("Transcripción y apuntes automáticos de clases largas.")

salud = api_get("/api/health")

# La configuracion activa se consulta de vez en cuando; la navegacion, siempre.
# Por eso lo primero es navegar y el detalle tecnico queda plegado debajo: en la
# pantalla del movil, la barra lateral entera cabe justa.
faltan_claves = bool(salud) and not (
    salud["transcription_key_configured"] and salud.get("annotator_key_configured", True)
)



# ---------------------------------------------------------------------------
# Vista de un enlace compartido
# ---------------------------------------------------------------------------
#
# Aviso importante mientras no haya usuarios: el token protege el *enlace*, no
# la API. Quien pueda llegar al backend por su cuenta puede consultar cualquier
# grupo, porque todavia no hay nada que autentique las peticiones. Por eso la
# app se sirve solo en la red local. El login, que es lo que cierra este hueco,
# esta en el plan.


def vista_compartida(grupo: dict, token: str) -> None:
    """Muestra un grupo abierto desde su enlace, sin el resto de la app.

    Quien llega por aqui no es el dueno: no ve sus otras clases ni puede subir
    grabaciones. Solo el grupo, y solo escribe si el autor lo permitio.
    """
    puede_escribir = grupo.get("share_permiso") == "escritura"

    st.title(f"{grupo['materia']}")
    st.caption(f"Grupo compartido contigo · {grupo['nombre']}")
    if puede_escribir:
        st.success("Puedes leer todo y escribir notas en este grupo.")
    else:
        st.info("Puedes leerlo todo, pero no modificar nada.")

    # Todo lo de aqui va por `/api/compartido/{token}/...`: el enlace abre su
    # grupo y nada mas. Las rutas normales exigen sesion.
    temas = api_get(f"/api/compartido/{token}/temas") or []
    nombres_tema = {t["id"]: t["nombre"] for t in temas}

    pestanas = st.tabs(["Clases", "Material", "Notas"])

    with pestanas[0]:
        # El vacio se decide sobre lo que de verdad se va a mostrar. Antes se
        # comprobaba la lista sin filtrar mientras el bucle descartaba lo que no
        # estaba listo, asi que un grupo cuyas clases fallaron se abria en
        # blanco: ni apuntes, ni aviso, ni explicacion. Es la primera pantalla
        # que ve alguien que llega por el enlace compartido.
        todas = api_get(f"/api/compartido/{token}/clases") or []
        clases = [c for c in todas if c["status"] == "completed"]
        pendientes = len(todas) - len(clases)

        if not clases:
            if pendientes:
                st.info(
                    f"Este grupo tiene {pendientes} "
                    f"{'clase' if pendientes == 1 else 'clases'} sin apuntes "
                    "todavía. Vuelve a mirar más tarde."
                )
            else:
                st.info("Este grupo todavía no tiene clases.")

        # Se elige una clase y solo esa se pide. Antes cada clase era un
        # desplegable, y Streamlit ejecuta su cuerpo este abierto o cerrado:
        # abrir un grupo compartido de quince clases descargaba los quince
        # juegos de apuntes enteros antes de que el visitante hiciera nada.
        if clases:
            if st.session_state.get("clase_compartida") not in {c["id"] for c in clases}:
                st.session_state["clase_compartida"] = clases[0]["id"]

            for clase in clases:
                elegida = st.session_state["clase_compartida"] == clase["id"]
                ambito = nombres_tema.get(clase["tema_id"], "sin tema")
                fila, fila_meta = st.columns([5, 2], vertical_alignment="center")
                if fila.button(
                    nombre_de(clase),
                    key=f"sh_abrir_{clase['id']}",
                    type="primary" if elegida else "tertiary",
                    use_container_width=True,
                ):
                    st.session_state["clase_compartida"] = clase["id"]
                    st.rerun()
                fila_meta.caption(
                    f"{fecha_corta(clase['created_at'])} · {ambito}"
                )

            st.divider()
            abierta = next(
                c for c in clases if c["id"] == st.session_state["clase_compartida"]
            )
            detalle = api_get(f"/api/compartido/{token}/clases/{abierta['id']}")
            if detalle:
                apuntes = (
                    detalle.get("notes_editadas")
                    or detalle.get("notes_markdown")
                    or ""
                )
                columna_de_lectura().markdown(apuntes)
                st.download_button(
                    "Descargar apuntes (.md)",
                    data=apuntes,
                    file_name=f"{nombre_para_fichero(nombre_de(abierta))}_apuntes.md",
                    mime="text/markdown",
                    key=f"sh_dl_{abierta['id']}",
                )

    with pestanas[1]:
        materiales = api_get(f"/api/compartido/{token}/materiales") or []
        if not materiales:
            st.info("Este grupo no tiene material adjunto.")
        for material in materiales:
            ambito = nombres_tema.get(material["tema_id"], "toda la materia")
            with st.expander(
                f"{material['filename']} — {material['tipo']} · {ambito}"
            ):
                st.caption(f"{material['paginas']} páginas")
                st.text(" ".join((material["texto"] or "").split())[:300] + "...")

    with pestanas[2]:
        if puede_escribir:
            with st.form("nota_compartida", clear_on_submit=True):
                titulo = st.text_input("Título")
                contenido = st.text_area("Contenido", height=140)
                opciones = {"Sin tema": None}
                opciones.update({t["nombre"]: t["id"] for t in temas})
                tema = opciones[st.selectbox("Tema", list(opciones))]
                if st.form_submit_button("Añadir nota"):
                    if not titulo.strip():
                        st.warning("Ponle un título a la nota antes de añadirla.")
                    elif api_llamar(
                        "POST",
                        f"/api/compartido/{token}/notas",
                        json={
                            "titulo": titulo,
                            "contenido": contenido,
                            "tema_id": tema,
                        },
                    ) is not None:
                        st.rerun()

        notas = api_get(f"/api/compartido/{token}/notas") or []
        if not notas:
            st.info("Este grupo todavía no tiene notas.")
        for nota in notas:
            ambito = nombres_tema.get(nota["tema_id"], "")
            cabecera = nota["titulo"] + (f" — {ambito}" if ambito else "")
            with st.expander(cabecera):
                if puede_escribir:
                    texto = st.text_area(
                        "Contenido",
                        value=nota["contenido"],
                        key=f"sh_nota_{nota['id']}",
                        height=160,
                        label_visibility="collapsed",
                    )
                    if st.button("Guardar", key=f"sh_save_{nota['id']}"):
                        if api_llamar(
                            "PUT",
                            f"/api/compartido/{token}/notas/{nota['id']}",
                            json={"contenido": texto},
                        ) is not None:
                            st.rerun()
                else:
                    st.markdown(nota["contenido"] or "_Sin contenido._")


_token = st.query_params.get("grupo")
if _token:
    _grupo_compartido = api_get(f"/api/compartido/{_token}")
    if not _grupo_compartido:
        st.error(
            "Este enlace ya no funciona. Puede que quien lo creó haya dejado "
            "de compartir el grupo. Pídele uno nuevo."
        )
        st.stop()
    vista_compartida(_grupo_compartido, _token)
    st.stop()


# ---------------------------------------------------------------------------
# Entrar
# ---------------------------------------------------------------------------


def guardar_sesion(respuesta: dict) -> None:
    st.session_state["sesion"] = respuesta["token"]
    st.session_state["usuario"] = respuesta["usuario"]
    refrescar()


def cerrar_sesion() -> None:
    """Cierra la sesion aqui y en el servidor, y vuelve a la pantalla de entrar.

    El `st.rerun()` no es un detalle. El boton de salir esta arriba del todo,
    asi que sin el la pasada que atiende el clic sigue dibujando la app entera
    con la sesion ya cerrada: los apuntes desaparecian, las listas salian
    vacias y el boton de salir seguia ahi, como si el clic no hubiera hecho
    nada.
    """
    api_llamar("POST", "/api/auth/salir")
    for clave in ("sesion", "usuario", "clase_abierta"):
        st.session_state.pop(clave, None)
    refrescar()
    st.rerun()


def _volver_de_google() -> None:
    """Termina de entrar cuando Google devuelve el navegador aquí.

    El código llega en la URL, así que se limpia en cuanto se canjea: no tiene
    por qué quedarse en el historial ni viajar en un enlace copiado.
    """
    codigo = st.query_params.get("code")
    if not codigo or st.session_state.get("sesion"):
        return

    respuesta = api_llamar(
        "POST",
        "/api/auth/google",
        json={
            "code": codigo,
            "redirect_uri": enlace_base(),
            "state": st.query_params.get("state", ""),
        },
    )
    st.query_params.clear()
    if respuesta:
        guardar_sesion(respuesta)
        st.rerun()


def _formulario_de_entrada() -> None:
    with st.form("entrar"):
        email = st.text_input("Correo", placeholder="tu.correo@ejemplo.com")
        password = st.text_input("Contraseña", type="password")
        if st.form_submit_button("Entrar", type="primary"):
            respuesta = api_llamar(
                "POST", "/api/auth/entrar", json={"email": email, "password": password}
            )
            if respuesta:
                guardar_sesion(respuesta)
                st.rerun()


def _formulario_de_alta() -> None:
    with st.form("crear_cuenta"):
        nombre = st.text_input("Cómo te llamas", placeholder="Alex")
        email = st.text_input("Correo", placeholder="tu.correo@ejemplo.com")
        password = st.text_input(
            "Contraseña",
            type="password",
            help=(
                "Al menos 10 caracteres. Una frase que recuerdes protege más "
                "que un jeroglífico corto."
            ),
        )
        if st.form_submit_button("Crear la cuenta", type="primary"):
            respuesta = api_llamar(
                "POST",
                "/api/auth/registro",
                json={"email": email, "password": password, "nombre": nombre},
            )
            if respuesta:
                guardar_sesion(respuesta)
                st.rerun()


def _boton_de_google() -> None:
    """Ofrece entrar con Google, si este servidor lo tiene configurado."""
    configuracion = api_get("/api/auth/google")
    if not (configuracion and configuracion.get("activo")):
        return

    st.divider()
    if st.button("Entrar con Google", use_container_width=True):
        inicio = api_llamar(
            "POST", "/api/auth/google/inicio", json={"redirect_uri": enlace_base()}
        )
        if inicio:
            # Se navega desde el documento padre: el componente vive en un
            # iframe y cambiar su propia URL no movería la página.
            components.html(
                "<script>window.parent.location.href = "
                f"{json.dumps(inicio['url'])};</script>",
                height=0,
            )


def pantalla_de_entrada() -> None:
    """Lo único que se ve sin cuenta, además de un enlace compartido."""
    st.title("KekeTranslate")
    st.caption(
        "Tus clases grabadas, convertidas en apuntes. Privados salvo que "
        "decidas compartirlos."
    )

    if not salud:
        st.error(
            "No hay conexión con el servidor de KekeTranslate, así que no se "
            "puede entrar todavía. Arráncalo y vuelve a cargar la página."
        )
        st.code("python run.py", language="bash")
        return

    columna, _ = st.columns([3, 2])
    with columna:
        modo = st.radio(
            "Qué quieres hacer",
            ["Entrar", "Crear una cuenta"],
            key="modo_de_entrada",
            horizontal=True,
            label_visibility="collapsed",
        )
        if modo == "Entrar":
            _formulario_de_entrada()
        else:
            _formulario_de_alta()
        _boton_de_google()

    st.caption(
        "La sesión dura mientras esta pestaña siga abierta. Al recargar la "
        "página hay que volver a entrar."
    )


_volver_de_google()

if not st.session_state.get("sesion"):
    pantalla_de_entrada()
    st.stop()

# A partir de aquí hay cuenta. Se comprueba contra el servidor, no contra lo
# guardado en la sesión: el testigo puede haber caducado o haberse revocado
# desde otro dispositivo, y en ese caso hay que volver a la pantalla de entrar
# en vez de dejar la app medio rota lanzando 401 por todas partes.
_yo = api_get("/api/auth/yo")
if not _yo:
    st.session_state.pop("sesion", None)
    st.session_state.pop("usuario", None)
    st.warning("Tu sesión ha caducado. Vuelve a entrar.")
    pantalla_de_entrada()
    st.stop()

st.session_state["usuario"] = _yo


# ---------------------------------------------------------------------------
# Pestanas
# ---------------------------------------------------------------------------

# La navegacion no usa `st.tabs` por dos razones que se notaron al usar la app:
# `st.tabs` vuelve a la primera pestana en cada recarga —y cualquier clic
# provoca una—, asi que cambiar de grupo te sacaba de la seccion; y ademas
# Streamlit ejecuta el contenido de **todas** las pestanas en cada pasada, de
# modo que estando en Grupos se pagaba igual el dibujado de los apuntes de cada
# clase. Un control con estado guarda su valor entre recargas y permite ejecutar
# solo la seccion visible.
#
# Y vive en el cuerpo, no en la barra lateral, porque en el movil —que es el
# dispositivo del escenario "durante la clase"— Streamlit colapsa la barra y
# deja la navegacion entera detras de un boton de 32 px sin nombre accesible.
# Quien abria la app en el telefono no tenia ninguna pista de que existieran
# otras secciones.
# Las opciones son identidades, no etiquetas: lo que se guarda en la sesion es
# "clases", y el texto con su icono se decide al dibujar. Antes la opcion *era*
# el texto, asi que cambiar una palabra invalidaba el estado guardado y rompia
# los tests, que no tenian mas remedio que repetir la cadena exacta.
SECCIONES = ["nueva", "clases", "grupos"]

ETIQUETA_DE_SECCION = {
    "nueva": ":material/upload: Nueva clase",
    "clases": ":material/library_books: Mis clases",
    "grupos": ":material/folder: Grupos",
}

seccion = st.radio(
    "Sección",
    SECCIONES,
    format_func=ETIQUETA_DE_SECCION.get,
    key="seccion",
    horizontal=True,
    label_visibility="collapsed",
)

# Lo que impide usar la app se avisa en el cuerpo, no en la barra lateral: en
# el movil la barra arranca colapsada, asi que un "Backend no disponible"
# escondido ahi dejaba al usuario delante de una pantalla que no funciona sin
# ninguna explicacion. La configuracion, que solo se consulta de vez en cuando,
# si puede quedarse plegada en la barra.
if not salud:
    st.error(
        "No hay conexión con el servidor de KekeTranslate, así que la app no "
        "puede hacer nada todavía. Arráncalo y vuelve a cargar la página."
    )
    st.code("python run.py", language="bash")
elif faltan_claves:
    if not salud["transcription_key_configured"]:
        st.error(
            f"Falta la clave de `{salud['transcription_provider']}` en el "
            ".env, o la que hay no está completa: sin ella no se puede "
            "transcribir ninguna clase."
        )
    if not salud.get("annotator_key_configured", True):
        st.error(
            f"Falta la clave de `{salud.get('annotator_provider', 'anotador')}` "
            "en el .env, o la que hay no está completa: las clases se "
            "transcribirán, pero no habrá apuntes."
        )

# El nombre y no el correo: Streamlit convierte un correo en un enlace `mailto`
# dentro de su Markdown, y ahi no hay nada que pulsar. El correo se ve al pasar
# por encima, que es cuando de verdad hace falta —para saber con que cuenta se
# esta— sin ocupar sitio en una barra que en el movil cabe justa.
_yo_visible = st.session_state["usuario"]
st.sidebar.caption(
    f":material/person: {_yo_visible['nombre'] or _yo_visible['email']}",
    help=_yo_visible["email"],
)
if st.sidebar.button("Salir", key="salir", use_container_width=True):
    cerrar_sesion()
    st.rerun()

if salud:
    st.sidebar.caption(":material/check_circle: Servidor conectado")

    with st.sidebar.expander("Configuración activa"):
        st.markdown(
            f"""
            **Transcripción:** `{salud['transcription_provider']}`
            **Apuntes:** `{salud['annotator_model']}`
            **Separa oradores:** {'sí' if salud['diarization_enabled'] else 'no'}
            **Subida máxima:** {limite_efectivo_mb() / 1024:.1f} GB
            """
        )


def cargar_grupos() -> list[dict]:
    return api_get("/api/grupos") or []


def elegir_destino(clave: str) -> tuple[str | None, str | None]:
    """Selector de grupo y tema donde archivar la clase.

    Devuelve `(None, None)` si se deja sin archivar, que es una opcion valida:
    una clase suelta funciona igual, solo que sin el contexto de la materia.
    """
    grupos = cargar_grupos()
    if not grupos:
        st.caption(
            "Todavía no tienes grupos. Crea uno desde **Grupos**, arriba, "
            "para que la IA lea el programa de la materia."
        )
        return None, None

    etiquetas = {"— Sin archivar —": None}
    etiquetas.update({f"{g['materia']} · {g['nombre']}": g["id"] for g in grupos})

    columna_grupo, columna_tema = st.columns(2)
    with columna_grupo:
        elegido = st.selectbox("Archivar en", list(etiquetas), key=f"grupo_{clave}")
    grupo_id = etiquetas[elegido]

    tema_id = None
    if grupo_id:
        temas = api_get(f"/api/grupos/{grupo_id}/temas") or []
        if temas:
            opciones = {"— Sin tema —": None}
            opciones.update({t["nombre"]: t["id"] for t in temas})
            with columna_tema:
                tema_id = opciones[
                    st.selectbox("Tema", list(opciones), key=f"tema_{clave}")
                ]
        else:
            with columna_tema:
                st.caption("Este grupo todavía no tiene temas.")

    return grupo_id, tema_id


def mostrar_ubicacion(detalle: dict) -> None:
    """Muestra donde esta archivada la clase y permite cambiarla de sitio."""
    grupos = {g["id"]: g for g in cargar_grupos()}

    if not grupos:
        return

    # Ni popover ni expander: esto ya vive dentro del desplegable de la clase,
    # y Streamlit no admite anidarlos.
    etiquetas = {"— Sin archivar —": None}
    etiquetas.update(
        {f"{g['materia']} · {g['nombre']}": g["id"] for g in grupos.values()}
    )
    nombres = list(etiquetas)
    indice = next(
        (i for i, n in enumerate(nombres) if etiquetas[n] == detalle.get("grupo_id")),
        0,
    )

    columnas = st.columns([3, 1])
    elegido = columnas[0].selectbox(
        "Cambiar de grupo", nombres, index=indice, key=f"mover_{detalle['id']}"
    )
    if etiquetas[elegido] != detalle.get("grupo_id"):
        if columnas[1].button("Mover", key=f"guardar_mover_{detalle['id']}"):
            parametros = {}
            if etiquetas[elegido]:
                parametros["grupo_id"] = etiquetas[elegido]
            if api_llamar(
                "PATCH", f"/api/jobs/{detalle['id']}/ubicacion", params=parametros
            ) is not None:
                st.rerun()
        st.caption(
            "Mover la clase no rehace los apuntes. Para que el material del "
            "nuevo grupo entre en ellos, usa *Rehacer con la IA*."
        )


def mostrar_apuntes(detalle: dict, filename: str) -> None:
    """Apuntes de la clase, con la version propia por encima de la de la IA.

    Las correcciones a mano se guardan aparte, asi que rehacer los apuntes con
    la IA no se las lleva por delante y siempre se puede volver al original.
    """
    job_id = detalle["id"]
    generados = detalle.get("notes_markdown") or ""
    editados = detalle.get("notes_editadas")
    apuntes = editados if editados is not None else generados

    if editados is not None:
        st.info("Estás viendo tu versión corregida, no la que escribió la IA.")

    editando = st.toggle("Editar los apuntes", key=f"editar_{job_id}")

    if editando:
        texto = st.text_area(
            "Apuntes en Markdown",
            value=apuntes,
            height=500,
            key=f"editor_{job_id}",
            label_visibility="collapsed",
        )
        columnas = st.columns(3)
        if columnas[0].button(
            "Guardar",
            key=f"guardar_{job_id}",
            type="primary",
            icon=":material/save:",
        ):
            if api_llamar(
                "PUT", f"/api/jobs/{job_id}/notes", json={"contenido": texto}
            ) is not None:
                st.rerun()
        if editados is not None and columnas[1].button(
            "Volver al original",
            key=f"revertir_{job_id}",
            icon=":material/undo:",
        ):
            if api_llamar("DELETE", f"/api/jobs/{job_id}/notes") is not None:
                st.rerun()
    else:
        columna_de_lectura().markdown(apuntes)

    columna_descarga, columna_rehacer = st.columns(2)
    columna_descarga.download_button(
        "Descargar apuntes (.md)",
        icon=":material/download:",
        data=apuntes,
        file_name=f"{filename}_apuntes.md",
        mime="text/markdown",
        key=f"dl_notes_{job_id}",
    )
    # Rehacer descarta los apuntes que ya habia, asi que pide confirmacion como
    # los borrados. Y termina en `st.rerun()`: sin el, la fila seguia mostrando
    # la clase como terminada mientras el modelo ya estaba reescribiendola.
    with columna_rehacer:
        if confirmar_borrado(
            f"rehacer_{job_id}",
            "Rehacer con la IA",
            "Se van a descartar los apuntes actuales y la IA los escribirá otra "
            "vez. La transcripción no se toca. Tus correcciones a mano tampoco: "
            "se guardan aparte.",
        ):
            if api_llamar("POST", f"/api/jobs/{job_id}/reanotar") is not None:
                st.rerun()


def encolar(
    fuente,
    nombre: str | None = None,
    grupo_id: str | None = None,
    tema_id: str | None = None,
) -> None:
    """Sube una grabacion y deja el trabajo como activo."""
    with st.spinner("Subiendo la grabación…"):
        trabajo = upload_file(fuente, nombre=nombre, grupo_id=grupo_id, tema_id=tema_id)
    if trabajo:
        st.session_state["trabajo_activo"] = trabajo["id"]
        st.success(
            f"**{trabajo['filename']}** ya se está procesando. Puedes cerrar "
            "esta página: la clase te espera en **Mis clases**."
        )


if seccion == SECCIONES[0]:
    st.header("Nueva clase")

    modo = st.radio(
        "Cómo quieres cargar la clase",
        ["grabar", "subir"],
        format_func={
            "grabar": ":material/mic: Grabar ahora",
            "subir": ":material/folder_open: Subir un fichero",
        }.get,
        key="modo_de_carga",
        horizontal=True,
        label_visibility="collapsed",
    )

    if modo == "grabar":
        st.markdown(
            "Deja el teléfono cerca de quien habla y empieza a grabar. Al "
            "parar, la grabación se envía sola a transcribir."
        )

        grabacion = st.audio_input("Grabación de la clase")

        if grabacion is not None:
            tamano_mb = len(grabacion.getvalue()) / 1e6
            nombre = f"clase_{datetime.now():%Y-%m-%d_%H%M}.wav"
            st.audio(grabacion)
            st.info(f"**{nombre}** — {tamano_mb:.1f} MB")

            destino = elegir_destino("grabacion")

            if st.button("Transcribir y generar apuntes", type="primary"):
                encolar(grabacion, nombre=nombre, grupo_id=destino[0], tema_id=destino[1])

        st.warning(
            "Esta grabadora es provisional: guarda el audio sin comprimir y no "
            "envía nada hasta que la paras, así que solo aguanta clases cortas. "
            "Para una clase de varias horas, graba con la app de tu teléfono y "
            "súbela como fichero."
        )

    else:
        st.markdown(
            "Audio o video, en los formatos habituales. Del video solo se "
            "aprovecha el sonido. Una clase de **2 a 4 horas** se procesa "
            "entera, sin partirla."
        )

        archivo = st.file_uploader(
            "Grabación de la clase",
            type=["mp3", "m4a", "wav", "flac", "ogg", "opus", "aac",
                  "mp4", "mov", "mkv", "webm", "avi"],
            label_visibility="collapsed",
        )

        if archivo is not None:
            st.info(f"**{archivo.name}** — {archivo.size / 1e6:.1f} MB")

            destino = elegir_destino("fichero")

            if st.button("Transcribir y generar apuntes", type="primary"):
                encolar(archivo, grupo_id=destino[0], tema_id=destino[1])

    st.divider()
    st.caption(
        "El procesado corre en segundo plano: puedes cerrar el navegador y "
        "volver más tarde. Una clase de 4 h tarda entre 10 y 30 minutos."
    )


# ---------------------------------------------------------------------------
# Mis clases: indice y ficha
# ---------------------------------------------------------------------------

# La lista se corta aqui y se pide afinar la busqueda. No es un limite del
# backend: es que una columna de doscientos botones ya no se lee, y el buscador
# encuentra antes que el ojo.
TOPE_EN_LISTA = 40

# A partir de aqui aparecen el buscador y el filtro por materia. Por debajo, la
# lista se lee entera sin ayuda y los controles solo estorbarian.
CLASES_PARA_FILTRAR = 8

SIN_ARCHIVAR = "Sin archivar"
TODAS_LAS_MATERIAS = "Todas las materias"


def dato_util_de(clase: dict) -> str:
    """Lo que conviene saber de una clase de un vistazo.

    Si esta lista, cuanto dura, que es lo que ayuda a elegir. Si no, en que
    estado va, **con palabras**: el icono solo obliga a recordar que significa
    cada dibujo, y en voz alta no dice nada util.
    """
    if clase["status"] == "completed":
        return formatear_duracion(clase.get("audio_duration_seconds"))
    return PALABRA_DE_ESTADO.get(clase["status"], "En proceso")


def abrir_clase(job_id: str) -> None:
    """Deja la app en Mis clases con esa clase abierta."""
    st.session_state["clase_abierta"] = job_id
    st.session_state["seccion"] = SECCIONES[1]


def guardar_titulo(job_id: str) -> None:
    """Guarda el nombre de la clase al salir del campo."""
    nuevo = st.session_state.get(f"titulo_{job_id}", "") or ""
    api_llamar("PATCH", f"/api/jobs/{job_id}/titulo", json={"titulo": nuevo})


def etiqueta_de_grupo(grupo: dict | None) -> str:
    return f"{grupo['materia']} · {grupo['nombre']}" if grupo else SIN_ARCHIVAR


def indice_de_clases(trabajos: list[dict], grupos: dict[str, dict]) -> None:
    """Indice de clases, agrupado por materia.

    Agrupar por materia y no por fecha no es una preferencia estetica: es como
    el estudiante tiene organizada la cursada, y es lo unico que distingue
    cinco grabaciones que el movil llamo a todas igual.
    """
    # Los filtros aparecen cuando hacen falta. Con media docena de clases la
    # lista entera cabe de un vistazo, y en el movil las dos columnas se apilan:
    # serian dos pantallazos de controles delante de cinco lineas de contenido.
    busqueda, materia = "", TODAS_LAS_MATERIAS

    if len(trabajos) > CLASES_PARA_FILTRAR:
        columna_busqueda, columna_materia = st.columns([3, 2])
        busqueda = sin_acentos(
            columna_busqueda.text_input(
                "Buscar una clase",
                placeholder="Buscar por nombre",
                key="buscar_clase",
                label_visibility="collapsed",
            ).strip()
        )
        materias = [
            TODAS_LAS_MATERIAS,
            *sorted(
                {
                    etiqueta_de_grupo(grupos.get(t.get("grupo_id") or ""))
                    for t in trabajos
                }
            ),
        ]
        materia = columna_materia.selectbox(
            "Materia",
            materias,
            key="filtro_materia",
            label_visibility="collapsed",
        )

    def coincide(clase: dict) -> bool:
        if materia != TODAS_LAS_MATERIAS:
            if etiqueta_de_grupo(grupos.get(clase.get("grupo_id") or "")) != materia:
                return False
        if not busqueda:
            return True
        return (
            busqueda in sin_acentos(nombre_de(clase))
            or busqueda in sin_acentos(clase["filename"])
        )

    visibles = [t for t in trabajos if coincide(t)]

    if not visibles:
        st.info(
            "Ninguna clase coincide con lo que buscas. Prueba con otra palabra "
            "o quita el filtro de materia."
        )
        return

    # Las materias van en orden alfabetico y las clases sueltas al final: lo
    # archivado se busca por su nombre; lo demas, por descarte.
    por_materia: dict[str, list[dict]] = {}
    for clase in visibles[:TOPE_EN_LISTA]:
        por_materia.setdefault(
            etiqueta_de_grupo(grupos.get(clase.get("grupo_id") or "")), []
        ).append(clase)

    orden = sorted(k for k in por_materia if k != SIN_ARCHIVAR)
    if SIN_ARCHIVAR in por_materia:
        orden.append(SIN_ARCHIVAR)

    for materia_actual in orden:
        st.markdown(f"###### {materia_actual}")
        for clase in por_materia[materia_actual]:
            icono = ICONOS_DE_ESTADO.get(clase["status"], "•")
            fila_nombre, fila_fecha = st.columns([5, 2], vertical_alignment="center")
            if fila_nombre.button(
                f"{icono}  {nombre_de(clase)}",
                key=f"abrir_{clase['id']}",
                type="tertiary",
                use_container_width=True,
            ):
                st.session_state["clase_abierta"] = clase["id"]
                st.rerun()
            fila_fecha.caption(
                f"{fecha_corta(clase['created_at'])} · {dato_util_de(clase)}"
            )

    if len(visibles) > TOPE_EN_LISTA:
        st.caption(
            f"Se muestran {TOPE_EN_LISTA} de {len(visibles)} clases. "
            "Escribe en el buscador para acotar."
        )


def ficha_de_clase(resumen: dict) -> None:
    """Todo lo de una sola clase. Es la unica que se pide al backend.

    Antes cada clase era un desplegable, y Streamlit ejecuta el cuerpo de un
    desplegable este abierto o cerrado: con treinta clases se pedian treinta
    fichas completas —transcripcion y apuntes enteros— para leer una.
    """
    if st.button(
        "Todas las clases",
        key="volver_al_indice",
        type="tertiary",
        icon=":material/arrow_back:",
    ):
        st.session_state["clase_abierta"] = None
        st.rerun()

    estado = resumen["status"]
    creado = datetime.fromisoformat(resumen["created_at"]).strftime("%d/%m/%Y %H:%M")

    st.text_input(
        "Nombre de la clase",
        value=nombre_de(resumen),
        key=f"titulo_{resumen['id']}",
        max_chars=120,
        on_change=guardar_titulo,
        args=(resumen["id"],),
        help="Se guarda al salir del campo. El nombre del fichero no se pierde.",
    )

    if estado == "failed":
        st.error(
            resumen.get("error") or "Esta clase falló y no se guardó el motivo."
        )
        _mostrar_transcripcion_rescatada(
            resumen["id"], nombre_para_fichero(nombre_de(resumen))
        )
        return

    if estado in ESTADOS_EN_CURSO:
        # Se dice la etapa y cuanto lleva, y no se dibuja una barra de progreso:
        # la que habia usaba porcentajes inventados que no se movian en veinte
        # minutos, asi que parecia colgada. El tiempo transcurrido es un dato
        # real y sube solo.
        st.info(
            f"**{ETIQUETAS_EN_CURSO[estado]}** · "
            f"{transcurrido_desde(resumen['created_at'])}"
        )
        st.caption(
            "Puedes cerrar el navegador: el procesado sigue en el servidor. "
            "Una clase de 4 h tarda entre 10 y 30 minutos."
        )
        return

    detalle = api_get(f"/api/jobs/{resumen['id']}")
    if not detalle:
        st.warning(
            "No se pudo leer esta clase. Vuelve al índice y prueba otra vez."
        )
        return

    # Duracion y oradores iban en dos `st.metric`, que son lo mas grande que
    # dibuja Streamlit: dos numeros de ficha tecnica se comian el sitio de lo
    # unico por lo que se abre una clase, que son sus apuntes. Aqui son una
    # linea de datos, como corresponde a un dato de contexto.
    oradores = len(detalle.get("speakers") or [])
    grupos = {g["id"]: g for g in cargar_grupos()}
    ubicacion = etiqueta_de_grupo(grupos.get(detalle.get("grupo_id") or ""))
    st.caption(
        " · ".join(
            [
                f"De **{resumen['filename']}**",
                creado,
                formatear_duracion(detalle.get("audio_duration_seconds")),
                f"{oradores} oradores" if oradores != 1 else "1 orador",
                ubicacion,
            ]
        )
    )

    mostrar_ubicacion(detalle)
    st.divider()

    # `segmented_control` y no `st.tabs`: Streamlit pinta la etiqueta de la
    # pestana activa con `primaryColor`, y ese mismo token tiene que ser oscuro
    # para que el texto blanco del boton primario se lea. Las dos exigencias son
    # incompatibles —esta calculado: con este lienzo no existe ningun color que
    # cumpla ambas—, asi que la solapa activa quedaba en 3.41:1, menos legible
    # que las inactivas.
    vista = st.segmented_control(
        "Qué quieres ver",
        ["apuntes", "texto"],
        format_func={
            "apuntes": ":material/description: Apuntes",
            "texto": ":material/subject: Transcripción",
        }.get,
        default="apuntes",
        key=f"vista_{resumen['id']}",
        label_visibility="collapsed",
    )

    nombre_fichero = nombre_para_fichero(nombre_de(detalle))

    if vista == "texto":
        transcripcion = detalle.get("transcript_diarized") or ""
        st.text_area(
            "Transcripción con oradores y marcas de tiempo",
            value=transcripcion,
            height=420,
            key=f"tx_{resumen['id']}",
            # Se puede seleccionar y copiar, pero no editar: no hay nada que
            # guarde los cambios, asi que dejarla editable prometia algo falso.
            # Lo que si se puede corregir son los apuntes, y eso tiene su boton.
            disabled=True,
        )
        st.download_button(
            "Descargar transcripción (.txt)",
        icon=":material/download:",
            data=transcripcion,
            file_name=f"{nombre_fichero}_transcripcion.txt",
            mime="text/plain",
            key=f"dl_tx_{resumen['id']}",
        )
    else:
        # Tambien cuando el control queda sin seleccion: la vista de apuntes es
        # la que importa y no puede quedarse en blanco.
        mostrar_apuntes(detalle, nombre_fichero)


if seccion == SECCIONES[1]:
    st.header("Mis clases")

    trabajos = api_get("/api/jobs") or []
    grupos_por_id = {g["id"]: g for g in cargar_grupos()}

    if any(t["status"] in ESTADOS_EN_CURSO for t in trabajos):
        st.caption("Esta pantalla se actualiza sola cada 15 segundos.")

        # Un fragmento con temporizador es lo unico que hace falta: repinta la
        # pantalla mientras haya algo procesandose y deja de existir en cuanto
        # no lo hay, asi que no se sondea el backend de balde. Antes habia que
        # pulsar "Actualizar" a mano durante media hora para saber si la clase
        # seguia viva.
        @st.fragment(run_every=SEGUNDOS_ENTRE_REFRESCOS)
        def _refresco_automatico() -> None:
            refrescar()
            st.rerun(scope="app")

        _refresco_automatico()

    if not trabajos:
        st.info(
            "Aquí van a aparecer tus clases. Sube la primera desde "
            "**Nueva clase**, arriba."
        )
    else:
        abierta = st.session_state.get("clase_abierta")
        elegida = next((t for t in trabajos if t["id"] == abierta), None)

        if elegida:
            ficha_de_clase(elegida)
        else:
            # La clase abierta pudo borrarse desde otro sitio; entonces se
            # vuelve al indice en vez de dejar la pantalla en blanco.
            st.session_state["clase_abierta"] = None
            indice_de_clases(trabajos, grupos_por_id)


# ---------------------------------------------------------------------------
# Grupos: la biblioteca por materia
# ---------------------------------------------------------------------------


def enlace_base() -> str:
    """URL de la app para construir los enlaces compartidos."""
    return os.getenv("APP_URL", "http://localhost:8501").rstrip("/")


def enlace_solo_local() -> bool:
    """Indica si el enlace que se generaria no sirve fuera de este equipo.

    `localhost` en el telefono de otra persona apunta a su propio telefono, no
    a esta maquina. Sin `APP_URL` la app producia un enlace roto y lo presentaba
    como listo para enviar: el fallo no lo veia quien compartia, sino quien
    recibia, mas tarde y sin forma de saber por que.
    """
    base = enlace_base()
    return "localhost" in base or "127.0.0.1" in base


def panel_material(grupo_id: str, temas: list[dict]) -> None:
    """Adjuntar y listar los PDFs de la materia."""
    st.caption(
        "El programa de la materia, las guías de prácticos o los apuntes del "
        "docente. La IA los lee al escribir los apuntes, así que puede situar "
        "la clase dentro del programa y usar la terminología de la cátedra."
    )

    # El selector de Streamlit anuncia su tope global (1 GB), no el que aplica
    # el backend a un PDF. Se dice aqui y se comprueba antes de subir: si no,
    # la pantalla promete un limite y la subida falla con otro.
    tope_mb = (salud or {}).get("max_material_mb", 50)

    with st.form(f"material_{grupo_id}", clear_on_submit=True):
        pdf = st.file_uploader("PDF", type=["pdf"], key=f"pdf_{grupo_id}")
        st.caption(f"Hasta {tope_mb} MB por documento.")
        columnas = st.columns(2)
        tipo = columnas[0].selectbox(
            "Tipo de documento",
            ["programa", "material", "practico"],
            key=f"tipo_{grupo_id}",
            format_func=lambda t: {
                "programa": "Programa de la materia",
                "material": "Apuntes del docente",
                "practico": "Guía de prácticos",
            }[t],
        )
        opciones = {"Toda la materia": None}
        opciones.update({t["nombre"]: t["id"] for t in temas})
        tema = columnas[1].selectbox(
            "Se aplica a", list(opciones), key=f"mat_tema_{grupo_id}"
        )

        if st.form_submit_button("Adjuntar"):
            # Antes, pulsar sin haber elegido fichero no hacia nada: ni
            # error, ni mensaje, ni pista. Un boton que a veces no hace
            # nada ensena a desconfiar de todos los demas.
            if pdf is None:
                st.warning("Elige un PDF antes de adjuntarlo.")
            else:
                if len(pdf.getvalue()) > tope_mb * 1024 * 1024:
                    st.error(
                        f"{pdf.name} pesa {len(pdf.getvalue()) / 1e6:.0f} MB y el "
                        f"máximo son {tope_mb} MB. Un PDF tan grande suele ser un "
                        "escaneo, y de un escaneo la IA no puede leer nada."
                    )
                    st.stop()
                datos = {"tipo": tipo}
                if opciones[tema]:
                    datos["tema_id"] = opciones[tema]
                resultado = api_llamar(
                    "POST",
                    f"/api/grupos/{grupo_id}/materiales",
                    files={"file": (pdf.name, pdf.getvalue(), "application/pdf")},
                    data=datos,
                )
                if resultado:
                    st.success(
                        f"**{resultado['filename']}** adjuntado: "
                        f"{resultado['paginas']} páginas leídas por la IA."
                    )
                    st.rerun()

    materiales = api_get(f"/api/grupos/{grupo_id}/materiales") or []
    if not materiales:
        st.info(
            "Sin material todavía. Adjunta el programa de la materia: es lo "
            "que permite a la IA situar cada clase dentro del programa."
        )
        return

    nombres_tema = {t["id"]: t["nombre"] for t in temas}
    for material in materiales:
        ambito = nombres_tema.get(material["tema_id"], "toda la materia")
        cabecera = f"{material['filename']} — {material['tipo']} · {ambito}"
        with st.expander(cabecera):
            st.caption(f"{material['paginas']} páginas, leídas por la IA")
            st.text(resumen_de(material["texto"]))
            if confirmar_borrado(
                f"mat_{material['id']}",
                "Quitar este documento",
                f"Se va a quitar **{material['filename']}**. La IA dejará de "
                "leerlo al escribir los apuntes de este grupo, y para "
                "recuperarlo habrá que subir el PDF otra vez.",
            ):
                if api_llamar("DELETE", f"/api/materiales/{material['id']}") is not None:
                    st.rerun()


def resumen_de(texto: str, limite: int = 300) -> str:
    """Primeras lineas de un documento, para reconocerlo sin abrirlo."""
    limpio = " ".join((texto or "").split())
    return limpio[:limite] + ("..." if len(limpio) > limite else "")


def panel_notas(grupo_id: str, temas: list[dict]) -> None:
    """Notas escritas por uno mismo, al margen de lo que genera la IA."""
    with st.form(f"nota_{grupo_id}", clear_on_submit=True):
        titulo = st.text_input("Título", key=f"nota_titulo_{grupo_id}")
        contenido = st.text_area("Contenido", key=f"nota_cuerpo_{grupo_id}", height=140)
        opciones = {"Sin tema": None}
        opciones.update({t["nombre"]: t["id"] for t in temas})
        tema = opciones[
            st.selectbox("Tema", list(opciones), key=f"nota_tema_{grupo_id}")
        ]

        if st.form_submit_button("Añadir nota"):
            if not titulo.strip():
                st.warning("Ponle un título a la nota antes de añadirla.")
            elif api_llamar(
                "POST",
                f"/api/grupos/{grupo_id}/notas",
                json={"titulo": titulo, "contenido": contenido, "tema_id": tema},
            ):
                st.rerun()

    notas = api_get(f"/api/grupos/{grupo_id}/notas") or []
    if not notas:
        st.info(
            "Sin notas todavía. Aquí van tus apuntes propios, los que no "
            "escribe la IA."
        )
        return

    nombres_tema = {t["id"]: t["nombre"] for t in temas}
    for nota in notas:
        ambito = nombres_tema.get(nota["tema_id"], "")
        cabecera = nota["titulo"] + (f" — {ambito}" if ambito else "")
        with st.expander(cabecera):
            texto = st.text_area(
                "Contenido",
                value=nota["contenido"],
                key=f"nota_ed_{nota['id']}",
                height=160,
                label_visibility="collapsed",
            )
            columnas = st.columns(2)
            if columnas[0].button("Guardar", key=f"nota_save_{nota['id']}"):
                if api_llamar(
                    "PUT", f"/api/notas/{nota['id']}", json={"contenido": texto}
                ) is not None:
                    st.rerun()
            if confirmar_borrado(
                f"nota_{nota['id']}",
                "Borrar la nota",
                f"Se va a borrar **{nota['titulo']}**. Es texto tuyo y no hay "
                "forma de recuperarlo.",
                contenedor=columnas[1],
            ):
                if api_llamar("DELETE", f"/api/notas/{nota['id']}") is not None:
                    st.rerun()


def panel_compartir(grupo: dict) -> None:
    """Generar, cambiar o revocar el enlace del grupo."""
    st.caption(
        "El grupo es privado hasta que generes un enlace. Quien lo reciba entra "
        "sin necesidad de cuenta, así que compártelo solo con quien quieras "
        "que vea estos apuntes."
    )

    guardado = grupo.get("share_permiso", "lectura")
    compartido = bool(grupo.get("share_token"))

    permiso = st.radio(
        "Qué puede hacer quien reciba el enlace",
        ["lectura", "escritura"],
        index=0 if guardado == "lectura" else 1,
        horizontal=True,
        key=f"permiso_{grupo['id']}",
        format_func=lambda p: "Solo leer" if p == "lectura" else "Leer y escribir",
    )

    # El radio por si solo no cambia nada: hace falta confirmar. Antes no se
    # decia, asi que alguien podia bajar de escritura a lectura, verlo marcado,
    # irse, y dejar el acceso de escritura intacto. Con privado por defecto como
    # unica restriccion innegociable, ese silencio era el peor de la app.
    if compartido and permiso != guardado:
        st.warning(
            f"Cambio sin guardar: el enlace sigue dando acceso de "
            f"**{'solo lectura' if guardado == 'lectura' else 'escritura'}** "
            "hasta que lo apliques."
        )
        if st.button(
            f"Aplicar: {'solo leer' if permiso == 'lectura' else 'leer y escribir'}",
            key=f"aplicar_{grupo['id']}",
            type="primary",
        ):
            if api_llamar(
                "POST",
                f"/api/grupos/{grupo['id']}/compartir",
                params={"permiso": permiso},
            ) is not None:
                st.rerun()

    if not compartido:
        if st.button("Generar enlace", key=f"compartir_{grupo['id']}"):
            if api_llamar(
                "POST",
                f"/api/grupos/{grupo['id']}/compartir",
                params={"permiso": permiso},
            ) is not None:
                st.rerun()
        return

    actual = "solo lectura" if guardado == "lectura" else "escritura"
    st.caption(f"Ahora mismo el enlace da acceso de **{actual}**.")

    # El enlace solo se ofrece para copiar si de verdad sirve fuera de aqui.
    if enlace_solo_local():
        st.warning(
            "Este enlace solo funciona en este ordenador. Para compartirlo con "
            "alguien más, arranca la app con `APP_URL` apuntando a tu IP de la "
            "red local; está explicado en `docs/movil.md`."
        )
        st.caption("Enlace tal como está hoy, para referencia:")

    st.code(f"{enlace_base()}?grupo={grupo['share_token']}", language=None)

    st.divider()

    # Las dos acciones que dejan fuera a quien ya tiene el enlace piden
    # confirmacion, igual que los borrados: son igual de irreversibles.
    if confirmar_borrado(
        f"revocar_{grupo['id']}",
        "Dejar de compartir",
        "El enlace dejará de funcionar para todas las personas a las que ya se "
        "lo hayas pasado. El grupo vuelve a ser privado.",
    ):
        if api_llamar("DELETE", f"/api/grupos/{grupo['id']}/compartir") is not None:
            st.rerun()

    if confirmar_borrado(
        f"regenerar_{grupo['id']}",
        "Generar un enlace nuevo",
        "Se creará otro enlace y el actual dejará de funcionar, así que quien "
        "ya lo tenga se quedará fuera y habrá que volver a repartirlo.",
    ):
        if api_llamar(
            "DELETE", f"/api/grupos/{grupo['id']}/compartir"
        ) is not None and api_llamar(
            "POST",
            f"/api/grupos/{grupo['id']}/compartir",
            params={"permiso": permiso},
        ) is not None:
            st.rerun()


def panel_grupo(grupo: dict) -> None:
    """Contenido de un grupo: clases, temas, material, notas y compartir."""
    temas = api_get(f"/api/grupos/{grupo['id']}/temas") or []

    pestanas = st.tabs(
        ["Clases", "Temas", "Material", "Notas", "Compartir"]
    )

    with pestanas[0]:
        clases = api_get("/api/jobs", grupo_id=grupo["id"]) or []
        if not clases:
            st.info(
                "Ninguna clase archivada aquí todavía. Al subir una grabación "
                "puedes elegir este grupo como destino."
            )
        nombres_tema = {t["id"]: t["nombre"] for t in temas}
        for clase in clases:
            # La fila lleva a los apuntes. Antes era texto muerto: el grupo
            # enumeraba sus clases y luego mandaba a buscarlas a mano en otra
            # seccion, con el nombre del fichero como unica pista.
            icono = ICONOS_DE_ESTADO.get(clase["status"], "•")
            ambito = nombres_tema.get(clase["tema_id"], "sin tema")
            fila, fila_meta = st.columns([5, 2], vertical_alignment="center")
            # `on_click` y no una asignacion suelta: Streamlit prohibe tocar el
            # estado de un widget ya dibujado, y la navegacion es uno.
            fila.button(
                f"{icono}  {nombre_de(clase)}",
                key=f"ir_a_clase_{clase['id']}",
                type="tertiary",
                use_container_width=True,
                on_click=abrir_clase,
                args=(clase["id"],),
            )
            fila_meta.caption(
                f"{fecha_corta(clase['created_at'])} · {ambito} · "
                f"{dato_util_de(clase)}"
            )
        if clases:
            st.caption("Pulsa una clase para abrir sus apuntes.")

    with pestanas[1]:
        with st.form(f"tema_{grupo['id']}", clear_on_submit=True):
            nombre = st.text_input(
                "Nuevo tema",
                placeholder="Unidad 3: Integrales",
                key=f"tema_nombre_{grupo['id']}",
            )
            if st.form_submit_button("Añadir tema"):
                if not nombre.strip():
                    st.warning("Ponle un nombre al tema antes de añadirlo.")
                elif api_llamar(
                    "POST",
                    f"/api/grupos/{grupo['id']}/temas",
                    json={"nombre": nombre},
                ) is not None:
                    st.rerun()

        for tema in temas:
            columnas = st.columns([5, 1])
            columnas[0].write(f"- {tema['nombre']}")
            if confirmar_borrado(
                f"tema_{tema['id']}",
                "Borrar tema",
                f"Se va a borrar el tema **{tema['nombre']}**. Su material, sus "
                "notas y sus clases no se borran: quedan en el grupo, sin tema "
                "asignado.",
                contenedor=columnas[1],
            ):
                if api_llamar("DELETE", f"/api/temas/{tema['id']}") is not None:
                    st.rerun()
        if temas:
            st.caption(
                "Borrar un tema no borra su material ni sus notas: se quedan en "
                "el grupo, sin tema asignado."
            )

    with pestanas[2]:
        panel_material(grupo["id"], temas)

    with pestanas[3]:
        panel_notas(grupo["id"], temas)

    with pestanas[4]:
        panel_compartir(grupo)


if seccion == SECCIONES[2]:
    st.header("Grupos")
    st.caption(
        "Un grupo por materia: reúne sus clases, su material y tus notas. "
        "Es privado salvo que generes un enlace para compartirlo."
    )

    with st.expander("Crear un grupo"):
        with st.form("crear_grupo", clear_on_submit=True):
            columnas = st.columns(2)
            nombre_grupo = columnas[0].text_input(
                "Nombre", placeholder="Clase 1 — Limites y continuidad"
            )
            materia_grupo = columnas[1].text_input(
                "Materia", placeholder="Analisis Matematico I"
            )
            if st.form_submit_button("Crear", type="primary"):
                if not nombre_grupo.strip() or not materia_grupo.strip():
                    st.error("Escribe el nombre y la materia para crear el grupo.")
                elif api_llamar(
                    "POST",
                    "/api/grupos",
                    json={"nombre": nombre_grupo, "materia": materia_grupo},
                ) is not None:
                    st.rerun()

    grupos = cargar_grupos()
    if not grupos:
        st.info(
            "Todavía no tienes grupos. Crea uno y adjúntale el programa de la "
            "materia: la IA lo tendrá en cuenta al escribir los apuntes."
        )

    if grupos:
        # Se muestra un grupo cada vez, elegido con un selector, y no todos
        # desplegables a la vez: Streamlit no admite expanders anidados, y el
        # contenido de un grupo (material, notas) ya los usa por dentro.
        etiquetas = {
            f"{g['materia']} · {g['nombre']}"
            + ("  ·  compartido" if g["share_token"] else ""): g
            for g in grupos
        }
        elegido = st.selectbox("Grupo", list(etiquetas), key="grupo_abierto")
        grupo = etiquetas[elegido]

        panel_grupo(grupo)

        st.divider()
        if confirmar_borrado(
            f"grupo_{grupo['id']}",
            "Borrar el grupo",
            f"Se va a borrar **{grupo['materia']} · {grupo['nombre']}** con sus "
            "temas, su material y tus notas, y no hay forma de recuperarlo. "
            "Las clases transcritas **no** se borran: quedan sin archivar.",
        ):
            if api_llamar("DELETE", f"/api/grupos/{grupo['id']}") is not None:
                st.rerun()
