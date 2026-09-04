"""Tests del troceado temporal de una clase larga.

Cubren el fallo que aparecio al probar la app con una clase real: AssemblyAI
solo corta las intervenciones cuando cambia de orador, asi que una clase con un
unico profesor volvia como un bloque continuo. Eso dejaba todas las marcas de
tiempo de los apuntes en [00:00:00] y, de paso, sin puntos por donde trocear la
transcripcion antes de mandarla al modelo.
"""

from __future__ import annotations

from backend.annotator.base import _split_on_line_boundaries
from backend.transcription.assemblyai import (
    MAX_UTTERANCE_MS,
    _partir_intervencion,
)


def _palabras(frases: list[str], ms_por_palabra: int = 400) -> list[dict]:
    """Construye palabras con tiempos como las devuelve AssemblyAI."""
    palabras: list[dict] = []
    reloj = 0
    for frase in frases:
        for termino in frase.split():
            palabras.append({"text": termino, "start": reloj, "end": reloj + ms_por_palabra})
            reloj += ms_por_palabra
    return palabras


def _intervencion(frases: list[str], **extra) -> dict:
    palabras = _palabras(frases)
    return {
        "speaker": "A",
        "start": palabras[0]["start"],
        "end": palabras[-1]["end"],
        "text": " ".join(frases),
        "words": palabras,
        **extra,
    }


# ---------------------------------------------------------------------------
# Partido de intervenciones
# ---------------------------------------------------------------------------


def test_una_intervencion_corta_no_se_toca():
    item = _intervencion(["Buenos dias a todos."])

    trozos = _partir_intervencion(item)

    assert len(trozos) == 1
    assert trozos[0].text == "Buenos dias a todos."


def test_una_clase_de_un_solo_orador_deja_de_ser_un_bloque_unico():
    """El caso que rompia la app: 10 minutos seguidos sin cambiar de orador."""
    frases = [f"Esta es la frase numero {i} de la clase." for i in range(120)]
    item = _intervencion(frases)

    trozos = _partir_intervencion(item)

    assert len(trozos) > 1
    # Las marcas de tiempo deben avanzar, que es justo lo que fallaba.
    assert len({t.timestamp for t in trozos}) == len(trozos)
    assert trozos[0].timestamp == "00:00:00"
    assert trozos[-1].timestamp != "00:00:00"


def test_los_trozos_respetan_el_limite_de_duracion():
    frases = [f"Frase numero {i} de la explicacion." for i in range(120)]

    trozos = _partir_intervencion(_intervencion(frases))

    # Se corta al terminar una frase, asi que se admite pasarse un poco; lo que
    # no puede es doblar el limite.
    for trozo in trozos[:-1]:
        assert trozo.end_ms - trozo.start_ms <= 2 * MAX_UTTERANCE_MS


def test_no_se_pierde_ni_una_palabra_al_partir():
    frases = [f"Frase numero {i} de la clase." for i in range(120)]
    item = _intervencion(frases)

    recompuesto = " ".join(t.text for t in _partir_intervencion(item))

    assert recompuesto.split() == item["text"].split()


def test_se_corta_al_terminar_una_frase():
    frases = [f"Frase numero {i} de la clase." for i in range(120)]

    trozos = _partir_intervencion(_intervencion(frases))

    for trozo in trozos[:-1]:
        assert trozo.text.endswith(".")


def test_un_orador_sin_puntuacion_tambien_se_corta():
    """Sin puntos donde cortar, el corte se fuerza: si no, no se cortaria nunca."""
    frases = ["palabra " * 400]

    trozos = _partir_intervencion(_intervencion([frases[0].strip()]))

    assert len(trozos) > 1


def test_sin_palabras_se_conserva_la_intervencion_entera():
    """Otros modelos pueden no devolver tiempos por palabra; no se pierde nada."""
    item = {"speaker": "A", "start": 0, "end": 600_000, "text": "Clase entera.", "words": []}

    trozos = _partir_intervencion(item)

    assert len(trozos) == 1
    assert trozos[0].text == "Clase entera."


# ---------------------------------------------------------------------------
# Red de seguridad del troceador del anotador
# ---------------------------------------------------------------------------


def test_una_linea_gigante_se_parte_igual():
    """Sin esto el map-reduce devolvia un solo bloque del tamano de la clase."""
    linea = "Una frase de relleno. " * 500

    bloques = _split_on_line_boundaries(linea, max_chars=1_000)

    assert len(bloques) > 1
    assert all(len(b) <= 1_000 for b in bloques)


def test_partir_una_linea_gigante_no_pierde_texto():
    linea = "Una frase de relleno. " * 500

    bloques = _split_on_line_boundaries(linea, max_chars=1_000)

    assert " ".join(bloques).split() == linea.split()


def test_las_lineas_normales_se_siguen_respetando():
    texto = "\n".join(f"[00:0{i}:00] Orador A: linea {i}" for i in range(5))

    bloques = _split_on_line_boundaries(texto, max_chars=10_000)

    assert bloques == [texto]
