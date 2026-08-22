"""Esquemas de datos compartidos entre el pipeline, la API y el frontend."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    """Estados por los que pasa un trabajo de principio a fin."""

    PENDING = "pending"            # Creado, aun sin procesar
    UPLOADING = "uploading"        # Subiendo el audio al proveedor
    TRANSCRIBING = "transcribing"  # El proveedor esta transcribiendo
    ANNOTATING = "annotating"      # Claude esta generando los apuntes
    COMPLETED = "completed"
    FAILED = "failed"


class Utterance(BaseModel):
    """Intervencion continua de un mismo orador."""

    speaker: str = Field(description="Etiqueta del orador, p. ej. 'A' o 'Orador 1'.")
    start_ms: int = Field(description="Inicio de la intervencion en milisegundos.")
    end_ms: int = Field(description="Fin de la intervencion en milisegundos.")
    text: str

    @property
    def timestamp(self) -> str:
        """Marca de tiempo legible `HH:MM:SS` del inicio de la intervencion."""
        total_seconds, _ = divmod(self.start_ms, 1000)
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


class TranscriptionResult(BaseModel):
    """Resultado crudo devuelto por el proveedor de transcripcion."""

    provider: str
    text: str = Field(description="Transcripcion completa en texto plano.")
    utterances: list[Utterance] = Field(default_factory=list)
    audio_duration_seconds: float | None = None
    language_code: str | None = None
    provider_job_id: str | None = None

    def to_diarized_text(self) -> str:
        """Formatea la transcripcion con orador y marca de tiempo por linea.

        Este es el formato que se envia a Claude: conservar los tiempos permite
        que los apuntes cronologicos referencien momentos concretos de la clase.
        """
        if not self.utterances:
            return self.text
        return "\n".join(
            f"[{u.timestamp}] {u.speaker}: {u.text}" for u in self.utterances
        )

    @property
    def speakers(self) -> list[str]:
        """Lista ordenada de oradores detectados."""
        return sorted({u.speaker for u in self.utterances})


class Job(BaseModel):
    """Trabajo de transcripcion + anotacion, tal y como se persiste."""

    id: str
    filename: str
    status: JobStatus = JobStatus.PENDING
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    provider: str | None = None
    provider_job_id: str | None = None
    file_size_bytes: int | None = None
    audio_duration_seconds: float | None = None
    speakers: list[str] = Field(default_factory=list)

    transcript_text: str | None = None
    transcript_diarized: str | None = None
    notes_markdown: str | None = None

    error: str | None = None

    @property
    def progress_label(self) -> str:
        """Descripcion en lenguaje natural del estado actual."""
        return {
            JobStatus.PENDING: "En cola",
            JobStatus.UPLOADING: "Subiendo el audio al proveedor",
            JobStatus.TRANSCRIBING: "Transcribiendo la clase",
            JobStatus.ANNOTATING: "Generando apuntes con Claude",
            JobStatus.COMPLETED: "Completado",
            JobStatus.FAILED: "Error",
        }[self.status]


class JobSummary(BaseModel):
    """Vista reducida de un trabajo, para el listado."""

    id: str
    filename: str
    status: JobStatus
    created_at: datetime
    audio_duration_seconds: float | None = None
    error: str | None = None
