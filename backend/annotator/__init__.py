"""Generacion de apuntes estructurados a partir de una transcripcion."""

from .base import AnnotationError, BaseAnnotator
from .factory import clave_del_anotador, get_annotator, modelo_del_anotador

__all__ = [
    "AnnotationError",
    "BaseAnnotator",
    "clave_del_anotador",
    "get_annotator",
    "modelo_del_anotador",
]
