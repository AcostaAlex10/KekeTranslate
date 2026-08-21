"""Interfaz de KekeTranslate en Streamlit.

Habla con el backend de FastAPI por HTTP, asi que ambos procesos pueden vivir
en maquinas distintas. La URL se configura con `BACKEND_URL`.
"""

from __future__ import annotations

import os
from datetime import datetime

import httpx
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000").rstrip("/")

# La subida de una clase de varias horas puede tardar minutos: el timeout de
# escritura se desactiva para no cortar la transferencia a mitad.
UPLOAD_TIMEOUT = httpx.Timeout(connect=30.0, read=600.0, write=None, pool=30.0)

ESTADOS_EN_CURSO = {"pending", "uploading", "transcribing", "annotating"}

st.set_page_config(page_title="KekeTranslate", page_icon="🎓", layout="wide")


# ---------------------------------------------------------------------------
# Cliente del backend
# ---------------------------------------------------------------------------


def api_get(path: str, **params):
    """GET contra el backend. Devuelve `None` si el backend no responde."""
    try:
        response = httpx.get(f"{BACKEND_URL}{path}", params=params, timeout=30.0)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as exc:
        st.error(f"No se pudo contactar con el backend ({BACKEND_URL}): {exc}")
        return None


def upload_file(uploaded) -> dict | None:
    """Sube la grabacion y devuelve el trabajo creado."""
    files = {"file": (uploaded.name, uploaded, uploaded.type or "application/octet-stream")}
    try:
        response = httpx.post(
            f"{BACKEND_URL}/api/jobs", files=files, timeout=UPLOAD_TIMEOUT
        )
        if response.status_code >= 400:
            st.error(f"Error al subir: {response.json().get('detail', response.text)}")
            return None
        return response.json()
    except httpx.HTTPError as exc:
        st.error(f"Fallo de red durante la subida: {exc}")
        return None


def formatear_duracion(segundos: float | None) -> str:
    """Convierte segundos en un texto del tipo `3 h 12 min`."""
    if not segundos:
        return "—"
    total_minutos = int(segundos // 60)
    horas, minutos = divmod(total_minutos, 60)
    return f"{horas} h {minutos:02d} min" if horas else f"{minutos} min"


# ---------------------------------------------------------------------------
# Barra lateral: configuracion activa
# ---------------------------------------------------------------------------

st.sidebar.title("🎓 KekeTranslate")
st.sidebar.caption("Transcripcion y apuntes automaticos de clases largas.")

salud = api_get("/api/health")
if salud:
    st.sidebar.success("Backend conectado")
    st.sidebar.markdown(
        f"""
        **Transcripcion:** `{salud['transcription_provider']}`
        **Anotador:** `{salud['annotator_model']}`
        **Diarizacion:** {'activada' if salud['diarization_enabled'] else 'desactivada'}
        **Subida maxima:** {salud['max_upload_mb'] / 1024:.1f} GB
        """
    )
    if not salud["transcription_key_configured"]:
        st.sidebar.error("Falta la clave del proveedor de transcripcion en el .env")
    if not salud["anthropic_key_configured"]:
        st.sidebar.error("Falta ANTHROPIC_API_KEY en el .env")
else:
    st.sidebar.error("Backend no disponible")
    st.sidebar.code("uvicorn backend.main:app --reload", language="bash")


# ---------------------------------------------------------------------------
# Pestanas
# ---------------------------------------------------------------------------

tab_subir, tab_trabajos = st.tabs(["📤 Nueva clase", "📚 Mis clases"])


with tab_subir:
    st.header("Sube la grabacion de tu clase")
    st.markdown(
        "Formatos de audio y video habituales. Una clase de **2 a 4 horas** se "
        "procesa completa, sin necesidad de partirla."
    )

    archivo = st.file_uploader(
        "Grabacion",
        type=["mp3", "m4a", "wav", "flac", "ogg", "opus", "aac",
              "mp4", "mov", "mkv", "webm", "avi"],
        label_visibility="collapsed",
    )

    if archivo is not None:
        st.info(f"**{archivo.name}** — {archivo.size / 1e6:.1f} MB")

        if st.button("🚀 Transcribir y generar apuntes", type="primary"):
            with st.spinner("Subiendo la grabacion..."):
                trabajo = upload_file(archivo)
            if trabajo:
                st.session_state["trabajo_activo"] = trabajo["id"]
                st.success(
                    f"Trabajo `{trabajo['id']}` encolado. "
                    "Sigue su avance en la pestana **Mis clases**."
                )

    st.divider()
    st.caption(
        "El procesado corre en segundo plano: puedes cerrar esta pestana y "
        "volver mas tarde. Una clase de 4 h suele tardar entre 10 y 30 minutos."
    )


with tab_trabajos:
    st.header("Mis clases")

    columna_refresco, columna_auto = st.columns([1, 3])
    with columna_refresco:
        if st.button("🔄 Actualizar"):
            st.rerun()

    trabajos = api_get("/api/jobs") or []

    with columna_auto:
        hay_activos = any(t["status"] in ESTADOS_EN_CURSO for t in trabajos)
        if hay_activos:
            st.caption("Hay trabajos en curso. Pulsa *Actualizar* para ver el avance.")

    if not trabajos:
        st.info("Todavia no has procesado ninguna clase.")

    for resumen in trabajos:
        estado = resumen["status"]
        icono = {
            "pending": "⏳",
            "uploading": "📤",
            "transcribing": "🎙️",
            "annotating": "🧠",
            "completed": "✅",
            "failed": "❌",
        }.get(estado, "•")

        creado = datetime.fromisoformat(resumen["created_at"]).strftime("%d/%m/%Y %H:%M")
        titulo = f"{icono} {resumen['filename']} — {creado}"

        with st.expander(titulo, expanded=(estado in ESTADOS_EN_CURSO)):
            if estado == "failed":
                st.error(resumen.get("error") or "Error desconocido")

            elif estado in ESTADOS_EN_CURSO:
                etiquetas = {
                    "pending": "En cola",
                    "uploading": "Subiendo el audio al proveedor",
                    "transcribing": "Transcribiendo la clase",
                    "annotating": "Generando apuntes con Claude",
                }
                st.info(f"**{etiquetas[estado]}**")
                st.progress(
                    {"pending": 0.05, "uploading": 0.2,
                     "transcribing": 0.6, "annotating": 0.9}[estado]
                )

            else:
                detalle = api_get(f"/api/jobs/{resumen['id']}")
                if not detalle:
                    continue

                metrica_1, metrica_2 = st.columns(2)
                metrica_1.metric(
                    "Duracion",
                    formatear_duracion(detalle.get("audio_duration_seconds")),
                )
                metrica_2.metric("Oradores", len(detalle.get("speakers") or []) or "—")

                vista_apuntes, vista_transcripcion = st.tabs(
                    ["📝 Apuntes", "📄 Transcripcion"]
                )

                with vista_apuntes:
                    apuntes = detalle.get("notes_markdown") or ""
                    st.markdown(apuntes)
                    st.download_button(
                        "⬇️ Descargar apuntes (.md)",
                        data=apuntes,
                        file_name=f"{resumen['filename']}_apuntes.md",
                        mime="text/markdown",
                        key=f"dl_notes_{resumen['id']}",
                    )

                with vista_transcripcion:
                    transcripcion = detalle.get("transcript_diarized") or ""
                    st.text_area(
                        "Transcripcion con oradores y marcas de tiempo",
                        value=transcripcion,
                        height=420,
                        key=f"tx_{resumen['id']}",
                    )
                    st.download_button(
                        "⬇️ Descargar transcripcion (.txt)",
                        data=transcripcion,
                        file_name=f"{resumen['filename']}_transcripcion.txt",
                        mime="text/plain",
                        key=f"dl_tx_{resumen['id']}",
                    )
