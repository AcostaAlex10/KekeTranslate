"""Orquestacion del flujo completo: audio -> transcripcion -> apuntes.

Se ejecuta en segundo plano porque una clase de 4 horas tarda entre 10 y 30
minutos en procesarse: la peticion HTTP que sube el fichero devuelve el id del
trabajo de inmediato y el cliente consulta el estado por separado.
"""

from __future__ import annotations

import logging

from .annotator import AnnotationError, ClaudeAnnotator
from .config import Settings
from .models import JobStatus, TranscriptionResult
from .store import JobStore
from .transcription import TranscriptionError, get_provider

logger = logging.getLogger(__name__)


async def run_job(job_id: str, settings: Settings, store: JobStore) -> None:
    """Procesa un trabajo de principio a fin, registrando su avance.

    Nunca propaga excepciones: cualquier fallo se guarda en el trabajo con
    estado `FAILED` para que el frontend pueda mostrarlo.
    """
    job = store.get(job_id)
    if job is None:
        logger.error("El trabajo %s desaparecio antes de procesarse", job_id)
        return

    audio_path = settings.uploads_dir / f"{job_id}_{job.filename}"

    try:
        # --- 1. Transcripcion -------------------------------------------
        provider = get_provider(settings)
        store.update(
            job_id, status=JobStatus.UPLOADING, provider=provider.name, error=None
        )

        async def on_progress(message: str) -> None:
            """Refleja el avance del proveedor en el estado del trabajo."""
            status = (
                JobStatus.UPLOADING
                if "Subiendo" in message or "Enviando" in message
                else JobStatus.TRANSCRIBING
            )
            store.update(job_id, status=status)
            logger.info("[%s] %s", job_id, message)

        diarize = settings.enable_diarization and provider.supports_diarization
        if settings.enable_diarization and not provider.supports_diarization:
            logger.warning(
                "El proveedor %s no soporta diarizacion; se omite la "
                "identificacion de oradores.",
                provider.name,
            )

        transcription = await provider.transcribe(
            audio_path,
            language=settings.transcription_language or None,
            diarize=diarize,
            expected_speakers=settings.expected_speakers,
            on_progress=on_progress,
        )

        _persist_transcript(job_id, settings, transcription)
        store.update(
            job_id,
            status=JobStatus.ANNOTATING,
            provider_job_id=transcription.provider_job_id,
            audio_duration_seconds=transcription.audio_duration_seconds,
            speakers=transcription.speakers,
            transcript_text=transcription.text,
            transcript_diarized=transcription.to_diarized_text(),
        )

        # --- 2. Anotacion IA --------------------------------------------
        annotator = ClaudeAnnotator(settings)
        notes = await annotator.annotate(transcription, filename=job.filename)

        notes_path = settings.results_dir / f"{job_id}_apuntes.md"
        notes_path.write_text(notes, encoding="utf-8")

        store.update(job_id, status=JobStatus.COMPLETED, notes_markdown=notes)
        logger.info("[%s] Trabajo completado", job_id)

    except (TranscriptionError, AnnotationError) as exc:
        # Errores esperados del dominio: el mensaje ya es util para el usuario.
        logger.error("[%s] %s", job_id, exc)
        store.set_status(job_id, JobStatus.FAILED, error=str(exc))

    except Exception as exc:  # noqa: BLE001 - ultimo recurso: nada debe colgar el job
        logger.exception("[%s] Fallo inesperado", job_id)
        store.set_status(job_id, JobStatus.FAILED, error=f"Fallo inesperado: {exc}")

    finally:
        # El audio original ya no hace falta una vez transcrito; se conserva
        # solo si el trabajo fallo, para poder reintentarlo.
        current = store.get(job_id)
        if current and current.status == JobStatus.COMPLETED:
            audio_path.unlink(missing_ok=True)


def _persist_transcript(
    job_id: str, settings: Settings, transcription: TranscriptionResult
) -> None:
    """Vuelca la transcripcion a disco antes de pasar por el LLM.

    Si la anotacion falla, la transcripcion (la parte cara del proceso) sigue
    estando disponible.
    """
    path = settings.results_dir / f"{job_id}_transcripcion.txt"
    path.write_text(transcription.to_diarized_text(), encoding="utf-8")
