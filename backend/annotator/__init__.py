"""Generacion de apuntes estructurados a partir de una transcripcion."""

from .claude import AnnotationError, ClaudeAnnotator

__all__ = ["AnnotationError", "ClaudeAnnotator"]
