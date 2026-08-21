"""Tests del formateo y la normalizacion de la transcripcion."""

from backend.models import TranscriptionResult, Utterance


def _utterance(speaker: str, start_ms: int, end_ms: int, text: str) -> Utterance:
    return Utterance(speaker=speaker, start_ms=start_ms, end_ms=end_ms, text=text)


def test_timestamp_formatea_horas_completas():
    # 3 h 12 min 05 s expresados en milisegundos.
    utterance = _utterance("Orador A", 11_525_000, 11_530_000, "Hola")
    assert utterance.timestamp == "03:12:05"


def test_timestamp_arranca_en_cero():
    assert _utterance("Orador A", 0, 1000, "Hola").timestamp == "00:00:00"


def test_to_diarized_text_incluye_orador_y_tiempo():
    resultado = TranscriptionResult(
        provider="assemblyai",
        text="Hola clase. Empezamos.",
        utterances=[
            _utterance("Orador A", 0, 2000, "Hola clase."),
            _utterance("Orador B", 2000, 4000, "Empezamos."),
        ],
    )
    assert resultado.to_diarized_text() == (
        "[00:00:00] Orador A: Hola clase.\n"
        "[00:00:02] Orador B: Empezamos."
    )


def test_to_diarized_text_cae_al_texto_plano_sin_diarizacion():
    resultado = TranscriptionResult(
        provider="openai", text="Transcripcion sin oradores.", utterances=[]
    )
    assert resultado.to_diarized_text() == "Transcripcion sin oradores."


def test_speakers_devuelve_lista_unica_ordenada():
    resultado = TranscriptionResult(
        provider="assemblyai",
        text="",
        utterances=[
            _utterance("Orador B", 0, 1, "a"),
            _utterance("Orador A", 1, 2, "b"),
            _utterance("Orador B", 2, 3, "c"),
        ],
    )
    assert resultado.speakers == ["Orador A", "Orador B"]
