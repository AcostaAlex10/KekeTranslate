"""Selecciona el anotador segun la configuracion."""

from __future__ import annotations

from ..config import Settings
from .base import AnnotationError, BaseAnnotator


def get_annotator(settings: Settings) -> BaseAnnotator:
    """Devuelve el anotador configurado en `ANNOTATOR_PROVIDER`.

    Se importa cada implementacion solo cuando se pide, para que quien use
    Gemini no necesite tener instalado el SDK de Anthropic ni al reves.
    """
    proveedor = settings.annotator_provider

    if proveedor == "anthropic":
        from .claude import ClaudeAnnotator

        return ClaudeAnnotator(settings)

    if proveedor == "gemini":
        from .gemini import GeminiAnnotator

        return GeminiAnnotator(settings)

    raise AnnotationError(
        f"Anotador desconocido: '{proveedor}'. "
        "Valores validos para ANNOTATOR_PROVIDER: anthropic, gemini."
    )


def clave_del_anotador(settings: Settings) -> str:
    """Clave de API del anotador activo, para comprobar si esta configurada."""
    return {
        "anthropic": settings.anthropic_api_key,
        "gemini": settings.gemini_api_key,
    }.get(settings.annotator_provider, "")


def modelo_del_anotador(settings: Settings) -> str:
    """Modelo del anotador activo, para mostrarlo en la interfaz."""
    return {
        "anthropic": settings.anthropic_model,
        "gemini": settings.gemini_model,
    }.get(settings.annotator_provider, "desconocido")
