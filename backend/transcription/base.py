"""Contrato comun a todos los proveedores de transcripcion."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Awaitable, Callable

from ..models import TranscriptionResult

# Callback opcional para reportar progreso ("subiendo", "en cola", ...).
ProgressCallback = Callable[[str], Awaitable[None]]


class TranscriptionError(RuntimeError):
    """Fallo recuperable o definitivo de un proveedor de transcripcion."""


class TranscriptionProvider(ABC):
    """Interfaz que debe implementar cada proveedor.

    La implementacion es responsable de resolver, internamente, todo lo que el
    audio largo exija: subida en streaming, sondeo asincrono y segmentacion si
    la API impone un tope de tamano.
    """

    name: str

    @abstractmethod
    async def transcribe(
        self,
        audio_path: Path,
        *,
        language: str | None = None,
        diarize: bool = True,
        expected_speakers: int | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> TranscriptionResult:
        """Transcribe un fichero de audio/video y devuelve el resultado."""
        raise NotImplementedError

    @property
    @abstractmethod
    def supports_diarization(self) -> bool:
        """Indica si el proveedor puede identificar oradores."""
        raise NotImplementedError

    async def _report(self, on_progress: ProgressCallback | None, message: str) -> None:
        """Envia un mensaje de progreso si hay callback registrado."""
        if on_progress is not None:
            await on_progress(message)
