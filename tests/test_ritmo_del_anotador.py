"""El anotador no puede disparar todas las peticiones a la vez.

Ojo con el porque, que estuvo mal explicado un dia entero. Lo que hacia fallar
la anotacion **no** era esto: era que `gemini-flash-latest` apuntaba a un modelo
con un nivel gratuito de veinte peticiones al dia. Eso ya esta arreglado fijando
la version del modelo, y esta contado en `docs/ESTADO.md`.

Lo que si sigue valiendo: con una cuota diaria pequena, lanzar los fragmentos en
paralelo se come una porcion grande de golpe, y los reintentos salen igual de
juntos. Ir por turnos es un seguro barato.

Y es un seguro que casi nunca se usa: el limite de una sola pasada es de 1,2 M
caracteres, asi que hasta una clase de cuatro horas entra en una unica peticion.
Estos tests cubren el camino que solo se pisa con grabaciones descomunales.
"""

from __future__ import annotations

import asyncio
import re

import pytest

from backend.annotator.base import BaseAnnotator
from backend.config import Settings
from backend.models import TranscriptionResult, Utterance


class _AnotadorEspia(BaseAnnotator):
    """Anotador que anota cuantas peticiones tuvo en vuelo a la vez."""

    nombre = "Espia"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.en_vuelo = 0
        self.maximo_en_vuelo = 0
        self.llamadas: list[str] = []
        self.pausas = 0

    async def _complete(self, system_prompt: str, user_prompt: str) -> str:
        self.en_vuelo += 1
        self.maximo_en_vuelo = max(self.maximo_en_vuelo, self.en_vuelo)
        # Cede el control: si otra peticion estuviera en vuelo, se solaparia
        # aqui y el maximo lo delataria.
        await asyncio.sleep(0)
        self.en_vuelo -= 1
        self.llamadas.append(user_prompt[:40])
        return f"respuesta {len(self.llamadas)}"

    async def _respirar(self) -> None:
        # Se cuenta la pausa en vez de dormirla: el test comprueba el ritmo,
        # no la paciencia.
        self.pausas += 1


def _ajustes(**extra) -> Settings:
    valores = {
        "gemini_api_key": "clave-de-prueba-con-largo-realista",
        "annotation_single_pass_char_limit": 100,
        "annotation_chunk_chars": 60,
    }
    valores.update(extra)
    return Settings(_env_file=None, **valores)


def _transcripcion_larga() -> TranscriptionResult:
    linea = "Orador A: " + "palabra " * 6
    return TranscriptionResult(
        provider="fake",
        text="\n".join([linea] * 12),
        utterances=[Utterance(speaker="A", start_ms=0, end_ms=1000, text=linea)],
        audio_duration_seconds=14_400.0,
        diarized_text="\n".join([linea] * 12),
    )


@pytest.mark.asyncio
async def test_los_fragmentos_no_se_lanzan_todos_a_la_vez():
    """Con la concurrencia en uno, nunca hay dos peticiones solapadas."""
    espia = _AnotadorEspia(_ajustes(annotation_concurrency=1))

    await espia.annotate(_transcripcion_larga(), filename="clase.wav")

    assert espia.maximo_en_vuelo == 1
    assert len(espia.llamadas) > 2, "el troceado no llego a activarse"


@pytest.mark.asyncio
async def test_se_respeta_la_concurrencia_configurada():
    """Un anotador de pago no tiene este techo y puede ir mas rapido."""
    espia = _AnotadorEspia(_ajustes(annotation_concurrency=3))

    await espia.annotate(_transcripcion_larga(), filename="clase.wav")

    assert 1 < espia.maximo_en_vuelo <= 3


@pytest.mark.asyncio
async def test_hay_una_pausa_entre_peticiones_pero_no_antes_de_la_primera():
    """Esperar antes de la primera solo alargaria el proceso sin ganar nada."""
    espia = _AnotadorEspia(_ajustes(annotation_concurrency=1))

    await espia.annotate(_transcripcion_larga(), filename="clase.wav")

    # Una pausa por peticion menos la primera, mas la de la fusion final.
    assert espia.pausas == len(espia.llamadas) - 1


@pytest.mark.asyncio
async def test_el_orden_de_los_fragmentos_se_conserva():
    """Ir por turnos no puede desordenar la clase."""
    espia = _AnotadorEspia(_ajustes(annotation_concurrency=1))

    await espia.annotate(_transcripcion_larga(), filename="clase.wav")

    numeros = [
        int(m.group(1))
        for ll in espia.llamadas
        if (m := re.match(r"Fragmento (\d+) de", ll))
    ]
    assert numeros == list(range(1, len(numeros) + 1))


@pytest.mark.asyncio
async def test_una_clase_corta_sigue_yendo_en_una_sola_llamada():
    """El ritmo no debe penalizar lo que nunca necesito trocearse."""
    espia = _AnotadorEspia(_ajustes(annotation_single_pass_char_limit=10_000))
    corta = TranscriptionResult(
        provider="fake",
        text="Orador A: hola clase.",
        utterances=[],
        audio_duration_seconds=60.0,
        diarized_text="Orador A: hola clase.",
    )

    await espia.annotate(corta, filename="clase.wav")

    assert len(espia.llamadas) == 1
    assert espia.pausas == 0


def test_el_ritmo_por_defecto_es_el_que_aguanta_el_nivel_gratuito():
    ajustes = Settings(_env_file=None)

    assert ajustes.annotation_concurrency == 1
    assert ajustes.annotation_pause_seconds >= 5
