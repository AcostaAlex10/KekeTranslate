"""Anotador IA: convierte una transcripcion cruda en apuntes estilo Notion.

Usa Claude Opus 5, cuya ventana de contexto de 1M tokens permite procesar una
clase de 4 horas (~50k-70k tokens de transcripcion) en una sola pasada. Para
grabaciones excepcionalmente largas se conserva una estrategia map-reduce como
red de seguridad.

Detalles de la integracion:

* **Streaming.** La generacion de unos apuntes completos puede superar los
  varios minutos; con `messages.stream()` la peticion no choca contra el
  timeout HTTP del SDK.
* **Adaptive thinking.** Claude decide cuanto razonar segun la dificultad del
  fragmento; `output_config.effort` regula el gasto global.
* **Prompt caching.** La transcripcion se marca con `cache_control`, de modo
  que regenerar los apuntes con otro enfoque sobre la misma clase reutiliza el
  prefijo cacheado en lugar de volver a pagarlo entero.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from anthropic import AsyncAnthropic

from ..config import Settings
from ..models import TranscriptionResult
from . import prompts


class AnnotationError(RuntimeError):
    """Fallo al generar los apuntes con el LLM."""


class ClaudeAnnotator:
    """Genera apuntes estructurados en Markdown a partir de la transcripcion."""

    def __init__(self, settings: Settings) -> None:
        if not settings.anthropic_api_key:
            raise AnnotationError(
                "Falta ANTHROPIC_API_KEY. Anadela a tu fichero .env."
            )
        self._settings = settings
        self._client = AsyncAnthropic(
            api_key=settings.anthropic_api_key,
            # La generacion se hace en streaming, pero un timeout amplio evita
            # cortes en las pasadas 'map' de transcripciones muy largas.
            timeout=1800.0,
        )

    async def annotate(
        self,
        transcription: TranscriptionResult,
        *,
        filename: str,
    ) -> str:
        """Devuelve los apuntes en Markdown de la clase transcrita."""
        transcript = transcription.to_diarized_text()
        if not transcript.strip():
            raise AnnotationError(
                "La transcripcion esta vacia; no hay nada que anotar."
            )

        metadata = _build_metadata(transcription, filename)

        if len(transcript) <= self._settings.annotation_single_pass_char_limit:
            return await self._single_pass(transcript, metadata)
        return await self._map_reduce(transcript, metadata)

    # -- Pasada unica -------------------------------------------------------

    async def _single_pass(self, transcript: str, metadata: dict[str, str]) -> str:
        """Genera los apuntes en una sola llamada al modelo."""
        user_prompt = prompts.USER_PROMPT_TEMPLATE.format(
            output_template=prompts.OUTPUT_TEMPLATE,
            transcript=transcript,
            **metadata,
        )
        return await self._complete(prompts.SYSTEM_PROMPT, user_prompt)

    # -- Map-reduce ---------------------------------------------------------

    async def _map_reduce(self, transcript: str, metadata: dict[str, str]) -> str:
        """Procesa la transcripcion por bloques y despues los fusiona.

        Solo se activa por encima de `annotation_single_pass_char_limit`, es
        decir, para grabaciones que exceden con mucho las 4 horas objetivo.
        """
        chunks = _split_on_line_boundaries(
            transcript, self._settings.annotation_chunk_chars
        )

        # Los fragmentos son independientes entre si, asi que se procesan en
        # paralelo; el orden se restaura al recomponer la lista.
        partial_tasks = [
            self._complete(
                prompts.MAP_SYSTEM_PROMPT,
                prompts.MAP_USER_PROMPT_TEMPLATE.format(
                    index=index + 1, total=len(chunks), transcript=chunk
                ),
            )
            for index, chunk in enumerate(chunks)
        ]
        partials = await asyncio.gather(*partial_tasks)

        joined = "\n\n---\n\n".join(
            f"## Extracto {index + 1} de {len(partials)}\n\n{partial}"
            for index, partial in enumerate(partials)
        )

        reduce_prompt = prompts.REDUCE_USER_PROMPT_TEMPLATE.format(
            output_template=prompts.OUTPUT_TEMPLATE,
            partials=joined,
            **metadata,
        )
        return await self._complete(prompts.SYSTEM_PROMPT, reduce_prompt)

    # -- Llamada al modelo --------------------------------------------------

    async def _complete(self, system_prompt: str, user_prompt: str) -> str:
        """Ejecuta una peticion en streaming y devuelve el texto final."""
        try:
            async with self._client.messages.stream(
                model=self._settings.anthropic_model,
                max_tokens=self._settings.anthropic_max_tokens,
                # Adaptive thinking: el modelo ajusta por si mismo la
                # profundidad del razonamiento en cada seccion de la clase.
                thinking={"type": "adaptive"},
                output_config={"effort": self._settings.anthropic_effort},
                system=[
                    {
                        "type": "text",
                        "text": system_prompt,
                        # El prompt de sistema es estable entre ejecuciones:
                        # cachearlo abarata cada clase procesada.
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": user_prompt,
                                # La transcripcion es el bloque grande; con el
                                # cacheado, reprocesar la misma clase cuesta
                                # una fraccion de la primera pasada.
                                "cache_control": {"type": "ephemeral"},
                            }
                        ],
                    }
                ],
            ) as stream:
                message = await stream.get_final_message()
        except Exception as exc:  # noqa: BLE001 - se reempaqueta con contexto
            raise AnnotationError(f"Claude devolvio un error: {exc}") from exc

        # Claude Opus 5 puede rechazar una peticion devolviendo HTTP 200 con
        # `stop_reason == "refusal"`; hay que comprobarlo antes de leer el texto.
        if message.stop_reason == "refusal":
            detail = getattr(message.stop_details, "explanation", None)
            raise AnnotationError(
                f"Claude declino generar los apuntes. {detail or ''}".strip()
            )

        text = "".join(
            block.text for block in message.content if block.type == "text"
        ).strip()

        if not text:
            raise AnnotationError("Claude devolvio una respuesta vacia.")
        return _strip_code_fence(text)


# ---------------------------------------------------------------------------
# Auxiliares
# ---------------------------------------------------------------------------


def _build_metadata(
    transcription: TranscriptionResult, filename: str
) -> dict[str, str]:
    """Prepara los metadatos que se inyectan en el prompt."""
    speakers = transcription.speakers
    return {
        "filename": filename,
        "duration": _format_duration(transcription.audio_duration_seconds),
        "speakers": ", ".join(speakers) if speakers else "no identificados",
        "processed_at": datetime.now().strftime("%d/%m/%Y"),
    }


def _format_duration(seconds: float | None) -> str:
    """Formatea una duracion en un texto legible del tipo `3 h 12 min`."""
    if not seconds:
        return "desconocida"
    total_minutes = int(seconds // 60)
    hours, minutes = divmod(total_minutes, 60)
    if hours:
        return f"{hours} h {minutes:02d} min"
    return f"{minutes} min"


def _split_on_line_boundaries(text: str, max_chars: int) -> list[str]:
    """Trocea el texto sin partir ninguna linea por la mitad.

    Cada linea de la transcripcion es una intervencion completa con su orador y
    su marca de tiempo, asi que cortar por lineas mantiene intacto el contexto
    de cada fragmento.
    """
    chunks: list[str] = []
    current: list[str] = []
    current_length = 0

    for line in text.splitlines():
        line_length = len(line) + 1
        if current and current_length + line_length > max_chars:
            chunks.append("\n".join(current))
            current, current_length = [], 0
        current.append(line)
        current_length += line_length

    if current:
        chunks.append("\n".join(current))
    return chunks


def _strip_code_fence(text: str) -> str:
    """Quita el bloque de codigo envolvente si el modelo lo anadio."""
    if not text.startswith("```"):
        return text

    lines = text.splitlines()
    if len(lines) >= 2 and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return text
