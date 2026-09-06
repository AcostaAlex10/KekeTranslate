"""Orquestacion del flujo completo: audio -> transcripcion -> apuntes.

Se ejecuta en segundo plano porque una clase de 4 horas tarda entre 10 y 30
minutos en procesarse: la peticion HTTP que sube el fichero devuelve el id del
trabajo de inmediato y el cliente consulta el estado por separado.
"""

from __future__ import annotations

import logging

from .annotator import AnnotationError, get_annotator
from .biblioteca import Biblioteca
from .config import Settings
from .models import ContextoMateria, Job, JobStatus, TranscriptionResult
from .store import JobStore
from .transcription import TranscriptionError, get_provider

logger = logging.getLogger(__name__)


def contexto_del_job(job: Job, biblioteca: Biblioteca | None) -> ContextoMateria | None:
    """Reune la materia y el material del grupo donde esta archivada la clase.

    Devuelve `None` si la clase no pertenece a ningun grupo: entonces se anota
    igual que siempre, sin contexto adicional.
    """
    if biblioteca is None or not job.grupo_id:
        return None

    grupo = biblioteca.grupo(job.grupo_id)
    if grupo is None:
        return None

    tema = biblioteca.tema(job.tema_id) if job.tema_id else None
    return ContextoMateria(
        materia=grupo.materia or grupo.nombre,
        tema=tema.nombre if tema else "",
        materiales=biblioteca.listar_materiales(grupo.id, tema_id=job.tema_id),
    )


async def run_job(
    job_id: str,
    settings: Settings,
    store: JobStore,
    biblioteca: Biblioteca | None = None,
) -> None:
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
        annotator = get_annotator(settings)
        notes = await annotator.annotate(
            transcription,
            filename=job.filename,
            contexto=contexto_del_job(job, biblioteca),
            idioma=job.idioma_apuntes,
        )

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


async def reanotar_job(
    job_id: str,
    settings: Settings,
    store: JobStore,
    biblioteca: Biblioteca | None = None,
) -> None:
    """Vuelve a generar los apuntes de un trabajo ya transcrito.

    La transcripcion es la parte cara y lenta del proceso: se paga por minuto
    de audio y tarda mas que todo lo demas junto. Cuando lo unico que falla es
    el modelo que redacta los apuntes —que se cae, se satura o agota su cuota
    gratuita—, tirar la transcripcion y pedirle al usuario que vuelva a subir
    la clase entera es inaceptable. Esta funcion reanuda desde donde quedo.
    """
    job = store.get(job_id)
    if job is None:
        logger.error("El trabajo %s no existe", job_id)
        return

    transcripcion = job.transcript_diarized or job.transcript_text
    if not transcripcion:
        store.set_status(
            job_id,
            JobStatus.FAILED,
            error="No hay transcripcion guardada: hay que subir el audio otra vez.",
        )
        return

    store.update(job_id, status=JobStatus.ANNOTATING, error=None)

    try:
        resultado = TranscriptionResult(
            provider=job.provider or "desconocido",
            text=job.transcript_text or transcripcion,
            diarized_text=job.transcript_diarized,
            speaker_names=job.speakers,
            audio_duration_seconds=job.audio_duration_seconds,
            provider_job_id=job.provider_job_id,
        )

        annotator = get_annotator(settings)
        notes = await annotator.annotate(
            resultado,
            filename=job.filename,
            contexto=contexto_del_job(job, biblioteca),
            idioma=job.idioma_apuntes,
        )

        notes_path = settings.results_dir / f"{job_id}_apuntes.md"
        notes_path.write_text(notes, encoding="utf-8")

        store.update(job_id, status=JobStatus.COMPLETED, notes_markdown=notes)
        logger.info("[%s] Apuntes regenerados", job_id)

    except AnnotationError as exc:
        logger.error("[%s] %s", job_id, exc)
        store.set_status(job_id, JobStatus.FAILED, error=str(exc))

    except Exception as exc:  # noqa: BLE001 - nada debe dejar el job colgado
        logger.exception("[%s] Fallo inesperado al reanotar", job_id)
        store.set_status(job_id, JobStatus.FAILED, error=f"Fallo inesperado: {exc}")


def _persist_transcript(
    job_id: str, settings: Settings, transcription: TranscriptionResult
) -> None:
    """Vuelca la transcripcion a disco antes de pasar por el LLM.

    Si la anotacion falla, la transcripcion (la parte cara del proceso) sigue
    estando disponible.
    """
    path = settings.results_dir / f"{job_id}_transcripcion.txt"
    path.write_text(transcription.to_diarized_text(), encoding="utf-8")
