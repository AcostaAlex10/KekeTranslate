"""Proveedores de transcripcion intercambiables."""

from .base import TranscriptionError, TranscriptionProvider
from .factory import get_provider

__all__ = ["TranscriptionError", "TranscriptionProvider", "get_provider"]
