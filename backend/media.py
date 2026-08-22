"""Utilidades de audio basadas en ffmpeg.

Solo hacen falta cuando el proveedor de transcripcion impone un tope de tamano
por peticion (el caso de OpenAI Whisper, con 25 MB). Los proveedores por
defecto de KekeTranslate aceptan el fichero completo y no pasan por aqui.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

MEDIA_ERROR_HINT = (
    "ffmpeg no esta instalado o no esta en el PATH. Es necesario para segmentar "
    "audios largos con el proveedor 'openai'. Instalalo con `brew install ffmpeg` "
    "(macOS) o `apt install ffmpeg` (Debian/Ubuntu)."
)


class MediaError(RuntimeError):
    """Fallo al inspeccionar o transformar un fichero multimedia."""


def ffmpeg_available() -> bool:
    """Indica si ffmpeg y ffprobe estan disponibles en el sistema."""
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


async def _run(*args: str) -> tuple[int, bytes, bytes]:
    """Ejecuta un comando y devuelve (codigo, stdout, stderr)."""
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    return process.returncode or 0, stdout, stderr


async def probe_duration(path: Path) -> float | None:
    """Devuelve la duracion del fichero en segundos, o `None` si se desconoce."""
    if not ffmpeg_available():
        return None

    code, stdout, _ = await _run(
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        str(path),
    )
    if code != 0:
        return None

    try:
        duration = json.loads(stdout)["format"]["duration"]
        return float(duration)
    except (KeyError, ValueError, json.JSONDecodeError):
        return None


async def split_audio(
    source: Path,
    output_dir: Path,
    *,
    segment_seconds: int = 600,
    bitrate: str = "64k",
) -> list[Path]:
    """Divide el audio en segmentos y los reconvierte a MP3 mono.

    Se usa un unico paso de ffmpeg con `-f segment`, que corta por tiempo sin
    volver a codificar el fichero completo en memoria. A 64 kbps mono, 10
    minutos de audio pesan ~4.8 MB: muy por debajo del tope de 25 MB de la API
    de Whisper, incluso con variaciones del bitrate.

    Devuelve la lista ordenada de segmentos generados.
    """
    if not ffmpeg_available():
        raise MediaError(MEDIA_ERROR_HINT)

    output_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(output_dir / "chunk_%04d.mp3")

    code, _, stderr = await _run(
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-i", str(source),
        "-vn",                       # descarta el video si la entrada lo tiene
        "-ac", "1",                  # mono: la voz no necesita estereo
        "-ar", "16000",              # 16 kHz basta para reconocimiento de voz
        "-b:a", bitrate,
        "-f", "segment",
        "-segment_time", str(segment_seconds),
        "-reset_timestamps", "1",
        pattern,
    )
    if code != 0:
        raise MediaError(
            f"ffmpeg fallo al segmentar el audio: {stderr.decode(errors='replace')}"
        )

    segments = sorted(output_dir.glob("chunk_*.mp3"))
    if not segments:
        raise MediaError("ffmpeg no genero ningun segmento de audio.")
    return segments
