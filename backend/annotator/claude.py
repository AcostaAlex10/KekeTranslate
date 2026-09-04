"""Anotador basado en Claude Opus 5.

Su ventana de contexto de 1M tokens permite procesar una clase de 4 horas
(~50k-70k tokens de transcripcion) en una sola pasada.

Detalles de la integracion:

* **Streaming.** La generacion de unos apuntes completos puede superar los
  varios minutos; con `messages.stream()` la peticion no choca contra el
  timeout HTTP del SDK.
* **Adaptive thinking.** Claude decide cuanto razonar segun la dificultad del
  fragmento; `output_config.effort` regula el gasto global.
* **Prompt caching.** La transcripcion se marca con `cache_control`, de modo
  que regenerar los apuntes con otro enfoque sobre la misma clase reutiliza el
  prefijo cacheado en lugar de volver a pagarlo entero.

La logica compartida con los demas anotadores (troceado, metadatos, limpieza)
vive en `base.py`.
"""

from __future__ import annotations

from anthropic import AsyncAnthropic

from ..config import Settings
from .base import AnnotationError, BaseAnnotator, _strip_code_fence


class ClaudeAnnotator(BaseAnnotator):
    """Genera los apuntes con la API de Anthropic."""

    nombre = "Claude"

    def __init__(self, settings: Settings) -> None:
        if not settings.anthropic_api_key:
            raise AnnotationError(
                "Falta la clave de Claude. Consíguela en "
                "https://console.anthropic.com y añádela a tu fichero .env "
                "como ANTHROPIC_API_KEY. Si prefieres no pagar, deja "
                "ANNOTATOR_PROVIDER=gemini, que tiene nivel gratuito."
            )
        super().__init__(settings)
        self._client = AsyncAnthropic(
            api_key=settings.anthropic_api_key,
            # La generacion se hace en streaming, pero un timeout amplio evita
            # cortes en las pasadas 'map' de transcripciones muy largas.
            timeout=1800.0,
        )

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
            raise AnnotationError(
                f"Claude devolvió un error: {exc}"
            ) from exc

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
            raise AnnotationError(
            "Claude respondió sin contenido. Vuelve a intentarlo con "
            "'Reintentar apuntes': la transcripción no se pierde."
        )
        return _strip_code_fence(text)
