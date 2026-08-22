"""Seleccion del proveedor de transcripcion segun la configuracion."""

from __future__ import annotations

from ..config import Settings
from .assemblyai import AssemblyAIProvider
from .base import TranscriptionError, TranscriptionProvider
from .deepgram import DeepgramProvider
from .whisper_openai import OpenAIWhisperProvider


def get_provider(settings: Settings) -> TranscriptionProvider:
    """Instancia el proveedor indicado en `TRANSCRIPTION_PROVIDER`."""
    provider = settings.transcription_provider

    if provider == "assemblyai":
        return AssemblyAIProvider(
            settings.assemblyai_api_key,
            poll_interval=settings.poll_interval_seconds,
            timeout_seconds=settings.transcription_timeout_seconds,
        )
    if provider == "deepgram":
        return DeepgramProvider(
            settings.deepgram_api_key,
            timeout_seconds=settings.transcription_timeout_seconds,
        )
    if provider == "openai":
        return OpenAIWhisperProvider(settings.openai_api_key)

    raise TranscriptionError(
        f"Proveedor de transcripcion desconocido: {provider!r}. "
        "Valores validos: assemblyai, deepgram, openai."
    )
