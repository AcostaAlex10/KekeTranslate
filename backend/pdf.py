"""Extraccion del texto de los PDF que se adjuntan a un grupo.

El texto se guarda en la base de datos porque es lo que lee el anotador; el
fichero original se conserva aparte solo para poder descargarlo.

Un limite deliberado: **no se hace OCR**. Un PDF escaneado (una foto de unos
apuntes, por ejemplo) no lleva texto dentro, y sacarselo requiere reconocer
caracteres sobre la imagen, que es otro problema y otra dependencia pesada.
En vez de guardar una cadena vacia sin explicacion, se detecta y se avisa.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pypdf import PdfReader

logger = logging.getLogger(__name__)

# Por debajo de esto se asume que el PDF no tiene texto util: son las cuatro
# palabras sueltas que a veces deja un escaneo, no contenido de verdad.
MINIMO_CARACTERES = 120


class PdfSinTexto(RuntimeError):
    """El PDF no contiene texto extraible (probablemente esta escaneado)."""


def extraer_texto(ruta: Path) -> tuple[str, int]:
    """Devuelve el texto del PDF y su numero de paginas.

    Lanza `PdfSinTexto` si no hay practicamente nada que leer, para que la
    interfaz pueda decir por que ese PDF no le va a servir a la IA.
    """
    try:
        lector = PdfReader(str(ruta))
    except Exception as exc:  # noqa: BLE001 - pypdf lanza de todo con ficheros rotos
        raise PdfSinTexto(
            "No se pudo abrir este PDF. Suele pasar cuando el fichero está "
            "dañado o protegido con contraseña. Prueba a abrirlo y volver a "
            f"guardarlo desde tu lector de PDF. Detalle técnico: {exc}"
        ) from exc

    paginas = len(lector.pages)
    partes: list[str] = []

    for numero, pagina in enumerate(lector.pages, start=1):
        try:
            texto = pagina.extract_text() or ""
        except Exception:  # noqa: BLE001 - una pagina rota no invalida el resto
            logger.warning("No se pudo extraer la pagina %d de %s", numero, ruta.name)
            continue
        if texto.strip():
            # La pagina se marca para que la IA pueda citar de donde sale algo.
            partes.append(f"[Pagina {numero}]\n{texto.strip()}")

    completo = "\n\n".join(partes)

    if len(completo) < MINIMO_CARACTERES:
        raise PdfSinTexto(
            "Este PDF no lleva texto dentro: casi seguro es un escaneo o unas "
            "fotos. La IA no podría leerlo, así que no sirve como material de "
            "la materia. Si tienes el original en Word o en texto, expórtalo a "
            "PDF desde ahí y súbelo."
        )

    return completo, paginas


def recortar(texto: str, maximo: int) -> str:
    """Acorta el material para que quepa junto a la transcripcion.

    Se corta por el final y se avisa dentro del propio texto: el modelo debe
    saber que esta viendo un fragmento, no el documento entero, para no
    concluir que algo no esta en el programa cuando solo esta mas abajo.
    """
    if len(texto) <= maximo:
        return texto
    return texto[:maximo] + "\n\n[...documento recortado por longitud...]"
