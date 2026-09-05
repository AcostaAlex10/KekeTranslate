"""Configuracion central de KekeTranslate.

Todos los ajustes se leen de variables de entorno (o del fichero `.env`).
Ver `.env.example` para la lista completa y su significado.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

TranscriptionProvider = Literal["assemblyai", "deepgram", "openai"]
AnnotatorProvider = Literal["anthropic", "gemini"]


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
    # Gemini por defecto porque tiene nivel gratuito y Anthropic no: quien
    # clone el repositorio tiene que poder generar apuntes sin poner dinero.
    # Con ANNOTATOR_PROVIDER=anthropic se cambia a Claude.
    annotator_provider: AnnotatorProvider = "gemini"

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5"
    anthropic_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    anthropic_max_tokens: int = 32_000

    # --- Anotador: Google Gemini (alternativa gratuita) ---
    gemini_api_key: str = ""
    # Alias, no una version concreta: Google lo mantiene apuntando al modelo
    # flash vigente. Fijar una version tiene dos problemas comprobados: se
    # retira sin aviso para las cuentas nuevas (la serie 2.5 ya devuelve 404) y
    # concentra la carga, asi que devuelve 503 cuando hay demanda alta.
    gemini_model: str = "gemini-flash-latest"
    gemini_max_tokens: int = 32_000
    # Baja: los apuntes deben ceñirse a lo que se dijo en clase, no inventar.
    gemini_temperature: float = 0.3

    # --- Almacenamiento y limites ---
    storage_dir: Path = Path("./storage")
    max_upload_mb: int = 5_120  # 5 GB, el tope del endpoint de AssemblyAI.
    # Entrar con Google. Vacios = la opcion no aparece y solo se entra con
    # correo y contrasena. El secreto no sale nunca del backend.
    google_client_id: str = ""
    google_client_secret: str = ""

    # Ritmo con el que se llama al modelo al trocear una clase larga. El nivel
    # gratuito de Gemini admite muy pocas peticiones por minuto y avisa con un
    # 503 "high demand" que no parece un limite de ritmo, asi que lanzarlas
    # todas a la vez hacia fallar casi todas. Medido el 04/09/2026: tras una
    # pausa, la primera y la segunda responden y la tercera ya no.
    #
    # Ir de una en una alarga el procesado unos segundos por fragmento. No
    # importa: una clase de 4 h tarda entre 10 y 30 minutos de todas formas, y
    # esto es la diferencia entre que salga y que no. Con un anotador de pago,
    # que no tiene este techo, se puede subir la concurrencia y bajar la pausa.
    annotation_concurrency: int = 1
    annotation_pause_seconds: float = 6.0

    max_material_mb: int = 50   # PDFs adjuntos a un grupo.
    backend_url: str = "http://localhost:8000"

    # --- Ajustes internos del pipeline ---
    # Umbral (en caracteres) a partir del cual el anotador deja de hacer una
    # sola pasada y aplica una estrategia map-reduce sobre la transcripcion.
    # ~1.2M caracteres equivalen a ~300k tokens: muy por debajo del millon de
    # tokens de contexto de Claude Opus 5, pero deja margen de seguridad.
    annotation_single_pass_char_limit: int = 1_200_000

    # Tamano de cada bloque en la fase "map" del anotador (en caracteres).
    annotation_chunk_chars: int = 280_000

    # Espacio maximo que puede ocupar el material del grupo (programa, guias)
    # dentro del prompt, repartido entre los documentos adjuntos. Es contexto
    # de apoyo: no debe competir con la transcripcion, que es lo que se anota.
    annotation_material_char_limit: int = 120_000

    # Segundos entre sondeos al proveedor de transcripcion.
    poll_interval_seconds: float = 15.0

    # Tiempo maximo de espera de una transcripcion (4 h de audio suelen
    # procesarse en 10-25 min, pero damos margen amplio).
    transcription_timeout_seconds: float = 4 * 60 * 60

    @model_validator(mode="before")
    @classmethod
    def _ignorar_numericos_vacios(cls, valores):
        """Trata una variable numerica vacia como si no estuviera puesta.

        En un `.env` lo natural es dejar `EXPECTED_SPEAKERS=` sin valor para
        decir "esto no lo configuro", y asi viene en `.env.example`. Pydantic
        recibia la cadena vacia e intentaba convertirla a numero, de modo que
        el backend no arrancaba con el fichero de ejemplo recien copiado.

        Solo se descartan los campos numericos y booleanos: en los de texto el
        vacio puede ser significativo (`TRANSCRIPTION_LANGUAGE` vacio pide
        deteccion automatica del idioma).
        """
        if not isinstance(valores, dict):
            return valores

        no_textuales = {
            nombre
            for nombre, campo in cls.model_fields.items()
            if campo.annotation not in (str, Path)
        }
        return {
            clave: valor
            for clave, valor in valores.items()
            if not (
                clave.lower() in no_textuales
                and isinstance(valor, str)
                and not valor.strip()
            )
        }

    @property
    def uploads_dir(self) -> Path:
        return self.storage_dir / "uploads"

    @property
    def results_dir(self) -> Path:
        return self.storage_dir / "results"

    @property
    def materiales_dir(self) -> Path:
        """PDFs adjuntos a los grupos (programa, guias, apuntes del docente)."""
        return self.storage_dir / "materiales"

    @property
    def db_path(self) -> Path:
        return self.storage_dir / "keketranslate.db"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def max_material_bytes(self) -> int:
        """Tope de un PDF adjunto. Muy por debajo del de una grabacion: un
        programa o un practico pesan pocos MB, y algo mayor suele ser un
        escaneo del que no se va a poder extraer texto igualmente."""
        return self.max_material_mb * 1024 * 1024

    def ensure_dirs(self) -> None:
        """Crea la estructura de carpetas de trabajo si no existe."""
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.materiales_dir.mkdir(parents=True, exist_ok=True)


# Por debajo de esto no hay clave de ningun proveedor conocido: la de Gemini
# ronda los 39 caracteres y la de AssemblyAI los 32. Sirve para cazar un pegado
# que no entro entero, que es el fallo mas caro porque se descubre tardisimo y
# disfrazado de error del proveedor.
LARGO_MINIMO_DE_CLAVE = 20


def clave_completa(valor: str) -> bool:
    """Dice si una clave tiene pinta de estar entera.

    No comprueba que sea valida —eso solo lo sabe el proveedor— sino que haya
    algo con forma de clave. Comprobar solo que no este vacia daba por buena una
    clave de un caracter y anunciaba "todo listo" hasta que fallaba la primera
    clase.
    """
    return len(valor.strip()) >= LARGO_MINIMO_DE_CLAVE


@lru_cache
def get_settings() -> Settings:
    """Devuelve los ajustes (cacheados: se leen del entorno una sola vez)."""
    settings = Settings()
    settings.ensure_dirs()
    return settings
