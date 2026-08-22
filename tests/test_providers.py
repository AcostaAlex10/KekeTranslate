"""Tests de normalizacion de las respuestas de los proveedores."""

from backend.transcription.assemblyai import AssemblyAIProvider
from backend.transcription.deepgram import DeepgramProvider, _group_words_by_speaker


def test_assemblyai_normaliza_utterances():
    provider = AssemblyAIProvider(api_key="clave-de-prueba")
    payload = {
        "id": "abc123",
        "status": "completed",
        "text": "Hola clase. Empezamos.",
        "audio_duration": 11_520,
        "language_code": "es",
        "utterances": [
            {"speaker": "A", "start": 0, "end": 2000, "text": "Hola clase."},
            {"speaker": "B", "start": 2000, "end": 4000, "text": "Empezamos."},
            # Las intervenciones vacias se descartan.
            {"speaker": "A", "start": 4000, "end": 4100, "text": "   "},
        ],
    }

    resultado = provider._to_result(payload)

    assert resultado.provider == "assemblyai"
    assert resultado.provider_job_id == "abc123"
    assert resultado.audio_duration_seconds == 11_520
    assert len(resultado.utterances) == 2
    assert resultado.utterances[0].speaker == "Orador A"
    assert resultado.speakers == ["Orador A", "Orador B"]


def test_deepgram_agrupa_palabras_consecutivas_del_mismo_orador():
    palabras = [
        {"punctuated_word": "Hola", "start": 0.0, "end": 0.5, "speaker": 0},
        {"punctuated_word": "clase.", "start": 0.5, "end": 1.0, "speaker": 0},
        {"punctuated_word": "Empezamos.", "start": 1.2, "end": 2.0, "speaker": 1},
    ]

    agrupadas = _group_words_by_speaker(palabras)

    assert len(agrupadas) == 2
    assert agrupadas[0].speaker == "Orador 0"
    assert agrupadas[0].text == "Hola clase."
    assert agrupadas[0].end_ms == 1000
    assert agrupadas[1].text == "Empezamos."


def test_deepgram_prefiere_el_array_de_utterances():
    provider = DeepgramProvider(api_key="clave-de-prueba")
    payload = {
        "metadata": {"duration": 120.0, "request_id": "req-1"},
        "results": {
            "channels": [{"alternatives": [{"transcript": "Hola clase.", "words": []}]}],
            "utterances": [
                {"transcript": "Hola clase.", "start": 0.0, "end": 2.0, "speaker": 0}
            ],
        },
    }

    resultado = provider._to_result(payload)

    assert resultado.audio_duration_seconds == 120.0
    assert len(resultado.utterances) == 1
    assert resultado.utterances[0].speaker == "Orador 0"
