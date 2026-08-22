"""Configuracion central de KekeTranslate.

Todos los ajustes se leen de variables de entorno (o del fichero `.env`).
Ver `.env.example` para la lista completa y su significado.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

TranscriptionProvider = Literal["assemblyai", "deepgram", "openai"]


class Settings(BaseSettings):
    """Ajustes de la aplicacion cargados desde el entorno."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Transcripcion ---
    transcription_provider: TranscriptionProvider = "assemblyai"
    assemblyai_api_key: str = ""
    deepgram_api_key: str = ""
    openai_api_key: str = ""

    # Idioma ISO-639-1 de la clase. Vacio => deteccion automatica.
    transcription_language: str = "es"
    enable_diarization: bool = True
    expected_speakers: int | None = None

    # --- Anotador IA ---
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5"
    anthropic_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    anthropic_max_tokens: int = 32_000

    # --- Almacenamiento y limites ---
    storage_dir: Path = Path("./storage")
    max_upload_mb: int = 5_120  # 5 GB, el tope del endpoint de AssemblyAI.
    backend_url: str = "http://localhost:8000"

    # --- Ajustes internos del pipeline ---
    # Umbral (en caracteres) a partir del cual el anotador deja de hacer una
    # sola pasada y aplica una estrategia map-reduce sobre la transcripcion.
    # ~1.2M caracteres equivalen a ~300k tokens: muy por debajo del millon de
    # tokens de contexto de Claude Opus 5, pero deja margen de seguridad.
    annotation_single_pass_char_limit: int = 1_200_000

    # Tamano de cada bloque en la fase "map" del anotador (en caracteres).
    annotation_chunk_chars: int = 280_000

    # Segundos entre sondeos al proveedor de transcripcion.
    poll_interval_seconds: float = 15.0

    # Tiempo maximo de espera de una transcripcion (4 h de audio suelen
    # procesarse en 10-25 min, pero damos margen amplio).
    transcription_timeout_seconds: float = 4 * 60 * 60

    @property
    def uploads_dir(self) -> Path:
        return self.storage_dir / "uploads"

    @property
    def results_dir(self) -> Path:
        return self.storage_dir / "results"

    @property
    def db_path(self) -> Path:
        return self.storage_dir / "keketranslate.db"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    def ensure_dirs(self) -> None:
        """Crea la estructura de carpetas de trabajo si no existe."""
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.results_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    """Devuelve los ajustes (cacheados: se leen del entorno una sola vez)."""
    settings = Settings()
    settings.ensure_dirs()
    return settings
