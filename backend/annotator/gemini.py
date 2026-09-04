"""Anotador basado en Google Gemini.

Existe por una razon practica: **Gemini tiene un nivel gratuito**, y Anthropic
no. Para un estudiante que quiere probar la app sin poner dinero, esta es la
via.

Encaja bien con el diseno porque Gemini tambien ofrece una ventana de contexto
de 1M de tokens, que es lo que permite leer la clase entera de una vez en lugar
de trocearla y perder la vision de conjunto.

Diferencias con el anotador de Claude:

* No hay *prompt caching* explicito: aqui el ahorro no aplica igual, y en el
  nivel gratuito lo que limita es la cuota de peticiones, no el precio.
* Gemini puede cortar la respuesta por su filtro de contenidos o por agotar el
  limite de tokens de salida; ambos casos se detectan y se explican, en vez de
  devolver unos apuntes truncados sin avisar.
"""

from __future__ import annotations

import asyncio
import logging

from google import genai
from google.genai import types

from ..config import Settings
from .base import AnnotationError, BaseAnnotator, _strip_code_fence

logger = logging.getLogger(__name__)

# Motivos de fin de generacion que no son un final limpio.
_FIN_CORRECTO = "STOP"

# Esperas entre reintentos, en segundos. El ultimo `None` marca el final: ya no
# se reintenta y se propaga el error.
#
# Son deliberadamente largas. Una saturacion de Gemini dura minutos, no
# segundos, y para cuando se llega aqui la transcripcion ya esta hecha y
# pagada: esperar dos minutos y medio es preferible a tirarla y tener que
# volver a subir la clase entera.
ESPERAS_REINTENTO = (15, 45, 90, None)


class GeminiAnnotator(BaseAnnotator):
    """Genera los apuntes con la API de Google Gemini."""

    nombre = "Gemini"

    def __init__(self, settings: Settings) -> None:
        if not settings.gemini_api_key:
            raise AnnotationError(
                "Falta la clave de Gemini. Consíguela gratis en "
                "https://aistudio.google.com/apikey y añádela a tu fichero "
                ".env como GEMINI_API_KEY."
            )
        super().__init__(settings)
        self._client = genai.Client(api_key=settings.gemini_api_key)

    async def _complete(self, system_prompt: str, user_prompt: str) -> str:
        """Envia la peticion y devuelve el texto de los apuntes.

        Reintenta ante los fallos temporales del servicio: llegados a este
        punto la transcripcion ya esta hecha y pagada, asi que rendirse por una
        saturacion pasajera de Google seria tirar el trabajo caro a la basura.
        """
        ultimo: Exception | None = None

        for intento, espera in enumerate(ESPERAS_REINTENTO, start=1):
            try:
                respuesta = await self._client.aio.models.generate_content(
                    model=self._settings.gemini_model,
                    contents=user_prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        max_output_tokens=self._settings.gemini_max_tokens,
                        temperature=self._settings.gemini_temperature,
                    ),
                )
            except Exception as exc:  # noqa: BLE001 - se reempaqueta con contexto
                ultimo = exc
                if not _es_temporal(exc) or espera is None:
                    raise AnnotationError(
                        _explicar_fallo(exc, self._settings)
                    ) from exc
                logger.warning(
                    "Gemini no disponible (intento %d); se reintenta en %ss",
                    intento,
                    espera,
                )
                await asyncio.sleep(espera)
                continue

            return _strip_code_fence(_extraer_texto(respuesta))

        raise AnnotationError(_explicar_fallo(ultimo, self._settings))


# ---------------------------------------------------------------------------
# Auxiliares
# ---------------------------------------------------------------------------


def _extraer_texto(respuesta) -> str:
    """Saca el texto de la respuesta, distinguiendo los finales anomalos.

    Gemini puede devolver un 200 sin apuntes: porque su filtro bloqueo la
    peticion o porque se quedo sin tokens de salida. Conviene decirlo, no
    entregar unos apuntes cortados como si estuvieran completos.
    """
    bloqueo = getattr(getattr(respuesta, "prompt_feedback", None), "block_reason", None)
    if bloqueo:
        raise AnnotationError(
            f"Gemini se negó a procesar esta clase (motivo: {bloqueo}). "
            "Su filtro de contenidos la considera sensible. La transcripción "
            "está guardada; puedes leerla aunque no haya apuntes."
        )

    candidatos = getattr(respuesta, "candidates", None) or []
    if not candidatos:
        raise AnnotationError(
            "Gemini respondió sin contenido. Vuelve a intentarlo con "
            "'Reintentar apuntes': la transcripción no se pierde."
        )

    motivo = str(getattr(candidatos[0], "finish_reason", "") or "")
    # El SDK devuelve un enum: 'FinishReason.MAX_TOKENS'. Basta con el sufijo.
    motivo = motivo.rsplit(".", 1)[-1]

    texto = (getattr(respuesta, "text", None) or "").strip()

    if motivo == "MAX_TOKENS":
        raise AnnotationError(
            "Los apuntes quedarían cortados: Gemini se quedó sin espacio de "
            "salida. Sube GEMINI_MAX_TOKENS en el .env y vuelve a intentarlo."
        )
    if motivo and motivo != _FIN_CORRECTO and not texto:
        raise AnnotationError(
            f"Gemini interrumpió los apuntes a medias ({motivo}). "
            "La transcripción está guardada; vuelve a intentarlo."
        )

    if not texto:
        raise AnnotationError(
            "Gemini respondió sin contenido. Vuelve a intentarlo con "
            "'Reintentar apuntes': la transcripción no se pierde."
        )
    return texto


def _es_temporal(exc: Exception) -> bool:
    """Indica si el fallo es pasajero y merece reintentarse."""
    detalle = str(exc).lower()
    return "503" in detalle or "unavailable" in detalle or "high demand" in detalle


def _explicar_fallo(exc: Exception | None, settings: Settings) -> str:
    """Traduce los fallos mas habituales a algo accionable.

    Siempre se conserva el mensaje original de Google al final: suele traer el
    dato que resuelve el problema (por ejemplo, que modelo usar en lugar del
    que se retiro).
    """
    detalle = str(exc) if exc else "sin detalle"
    minusculas = detalle.lower()

    if "api key" in minusculas or "api_key" in minusculas or "401" in detalle:
        pista = (
            "Gemini rechazó la clave. Revisa GEMINI_API_KEY en el .env; "
            "se consigue gratis en https://aistudio.google.com/apikey"
        )
    elif "not found" in minusculas or "404" in detalle:
        pista = (
            f"Gemini ya no ofrece el modelo '{settings.gemini_model}' a esta "
            "cuenta. Cambia GEMINI_MODEL en el .env por el que indique el "
            "mensaje de Google, que viene más abajo"
        )
    elif "quota" in minusculas or "resource_exhausted" in minusculas or "429" in detalle:
        # La cuota gratuita suele ser por proyecto y por dia, asi que cambiar de
        # modelo no siempre ayuda: lo unico seguro es esperar. Lo importante es
        # que la transcripcion no se pierde y se puede reintentar solo esta
        # parte, sin volver a subir ni a pagar el audio.
        pista = (
            "Se agotó la cuota gratuita de Gemini. Suele renovarse al día "
            "siguiente. La transcripción está guardada: usa 'Reintentar "
            "apuntes' cuando vuelva la cuota, o cambia a Claude poniendo "
            "ANNOTATOR_PROVIDER=anthropic en el .env si tienes clave"
        )
    elif _es_temporal(exc) if exc else False:
        # Google devuelve este 503 ("high demand") tanto cuando de verdad esta
        # saturado como cuando el nivel gratuito ya no acepta generaciones
        # largas por haber agotado la cuota del dia. Se comprobo: con el mismo
        # modelo, una peticion corta responde y una larga da 503. Decir solo
        # "esta saturado" manda al usuario a reintentar en bucle sin exito.
        pista = (
            "Gemini no generó los apuntes tras varios reintentos. Suele ser la "
            "cuota gratuita del día, que corta las respuestas largas aunque el "
            "error hable de saturación. La transcripción está guardada: puedes "
            "reintentar solo los apuntes más tarde, sin volver a subir la clase"
        )
    else:
        return f"Gemini devolvió un error inesperado: {detalle}"

    # El texto de Google se conserva largo a proposito: el dato accionable (el
    # limite concreto que se alcanzo, o el modelo sustituto) suele aparecer
    # despues de un par de URLs de documentacion, y recortando a 300 se perdia.
    return f"{pista}.\n\nGoogle dijo: {detalle[:800]}"
