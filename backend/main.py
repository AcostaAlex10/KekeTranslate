"""API HTTP de KekeTranslate (FastAPI).

Flujo de uso:

    POST /api/jobs            -> sube el audio y encola el trabajo
    GET  /api/jobs            -> lista los trabajos recientes
    GET  /api/jobs/{id}       -> consulta el estado y el resultado
    GET  /api/jobs/{id}/notes -> descarga los apuntes en Markdown
    GET  /api/jobs/{id}/transcript -> descarga la transcripcion
    DELETE /api/jobs/{id}     -> borra el trabajo y sus ficheros
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse

from .config import Settings, get_settings
from .models import Job, JobStatus, JobSummary
from .pipeline import run_job
from .store import JobStore

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicializa el almacen de trabajos al arrancar el servidor."""
    global _store
    settings = get_settings()
    _store = JobStore(settings.db_path)
    logger.info(
        "KekeTranslate listo | transcripcion=%s | modelo=%s",
        settings.transcription_provider,
        settings.anthropic_model,
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


@app.get("/api/health")
async def health(settings: Settings = Depends(get_settings)) -> dict:
    """Comprueba que el servicio esta vivo y correctamente configurado."""
    return {
        "status": "ok",
        "transcription_provider": settings.transcription_provider,
        "annotator_model": settings.anthropic_model,
        "diarization_enabled": settings.enable_diarization,
        "max_upload_mb": settings.max_upload_mb,
        # Se informa de si faltan claves sin exponer su valor.
        "transcription_key_configured": bool(
            _provider_key(settings)
        ),
        "anthropic_key_configured": bool(settings.anthropic_api_key),
    }


@app.post("/api/jobs", response_model=Job, status_code=201)
async def create_job(
    background_tasks: BackgroundTasks,
    file: UploadFile,
    settings: Settings = Depends(get_settings),
    store: JobStore = Depends(get_store),
) -> Job:
    """Sube una grabacion y encola su transcripcion y anotacion."""
    filename = Path(file.filename or "grabacion").name
    extension = Path(filename).suffix.lower()

    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=(
                f"Formato no soportado: {extension or 'sin extension'}. "
                f"Formatos validos: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
            ),
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
        )
    )

    # El procesado ocurre fuera del ciclo peticion/respuesta: la transcripcion
    # de una clase larga tarda mucho mas de lo que aguanta una conexion HTTP.
    background_tasks.add_task(run_job, job_id, settings, store)
    logger.info("Trabajo %s encolado (%s, %.1f MB)", job_id, filename, size / 1e6)
    return job


@app.get("/api/jobs", response_model=list[JobSummary])
async def list_jobs(
    limit: int = 50, store: JobStore = Depends(get_store)
) -> list[JobSummary]:
    """Lista los trabajos mas recientes."""
    return store.list(limit=limit)


@app.get("/api/jobs/{job_id}", response_model=Job)
async def get_job(job_id: str, store: JobStore = Depends(get_store)) -> Job:
    """Devuelve el estado completo de un trabajo, con transcripcion y apuntes."""
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Trabajo no encontrado")
    return job


@app.get("/api/jobs/{job_id}/notes", response_class=PlainTextResponse)
async def get_notes(job_id: str, store: JobStore = Depends(get_store)) -> str:
    """Devuelve los apuntes en Markdown."""
    job = _require_job(job_id, store)
    if not job.notes_markdown:
        raise HTTPException(
            status_code=409,
            detail=f"Los apuntes aun no estan listos (estado: {job.status.value}).",
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
            detail=(
                "La transcripcion aun no esta lista "
                f"(estado: {job.status.value})."
            ),
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
# Auxiliares
# ---------------------------------------------------------------------------


def _require_job(job_id: str, store: JobStore) -> Job:
    """Recupera un trabajo o lanza un 404."""
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Trabajo no encontrado")
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
                            "El fichero supera el limite de "
                            f"{max_bytes // (1024 * 1024)} MB."
                        ),
                    )
                handle.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise

    if size == 0:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="El fichero esta vacio.")

    return size
