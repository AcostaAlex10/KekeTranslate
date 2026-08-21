"""Test de integracion de la API con proveedor y anotador simulados.

Recorre el flujo completo (subida -> transcripcion -> anotacion -> descarga)
sin gastar una sola llamada a las APIs externas.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import backend.pipeline as pipeline
from backend.models import JobStatus, TranscriptionResult, Utterance


class _FakeProvider:
    """Proveedor de transcripcion simulado."""

    name = "fake"
    supports_diarization = True

    async def transcribe(
        self, path, *, language=None, diarize=True, expected_speakers=None,
        on_progress=None,
    ) -> TranscriptionResult:
        if on_progress:
            await on_progress("Subiendo el audio a AssemblyAI")
            await on_progress("Transcripcion en curso (estado: processing)")
        return TranscriptionResult(
            provider="fake",
            text="Hola clase. Empezamos con derivadas.",
            utterances=[
                Utterance(speaker="Orador A", start_ms=0, end_ms=2000,
                          text="Hola clase."),
                Utterance(speaker="Orador A", start_ms=2000, end_ms=5000,
                          text="Empezamos con derivadas."),
            ],
            audio_duration_seconds=11_520.0,
            language_code="es",
            provider_job_id="fake-1",
        )


class _FakeAnnotator:
    """Anotador simulado que comprueba el formato de entrada."""

    def __init__(self, settings):
        pass

    async def annotate(self, transcription, *, filename):
        # El anotador debe recibir el texto con orador y marca de tiempo.
        assert "[00:00:00] Orador A: Hola clase." in transcription.to_diarized_text()
        return "# Clase de calculo\n\n## Resumen ejecutivo\n\nDerivadas."


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Cliente de la API con almacenamiento aislado y servicios simulados."""
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "clave-de-prueba")
    monkeypatch.setenv("ASSEMBLYAI_API_KEY", "clave-de-prueba")

    from backend.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(pipeline, "get_provider", lambda settings: _FakeProvider())
    monkeypatch.setattr(pipeline, "ClaudeAnnotator", _FakeAnnotator)

    # `main` cachea el almacen en una global durante el lifespan; se reinicia
    # para que cada test escriba en su propio directorio temporal.
    import backend.main as main

    monkeypatch.setattr(main, "_store", None)

    with TestClient(main.app) as test_client:
        yield test_client

    get_settings.cache_clear()


def _audio(name: str = "clase_calculo.mp3", size: int = 4096):
    return {"file": (name, b"\x00" * size, "audio/mpeg")}


def test_health_reporta_la_configuracion(client):
    payload = client.get("/api/health").json()
    assert payload["status"] == "ok"
    assert payload["transcription_provider"] == "assemblyai"
    assert payload["anthropic_key_configured"] is True


def test_rechaza_formatos_no_soportados(client):
    response = client.post(
        "/api/jobs", files={"file": ("apuntes.txt", b"texto", "text/plain")}
    )
    assert response.status_code == 415


def test_rechaza_ficheros_vacios(client):
    response = client.post("/api/jobs", files=_audio(size=0))
    assert response.status_code == 400


def test_flujo_completo(client):
    response = client.post("/api/jobs", files=_audio())
    assert response.status_code == 201
    job_id = response.json()["id"]

    # TestClient ejecuta las BackgroundTasks antes de devolver la respuesta,
    # asi que el trabajo ya esta terminado en este punto.
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == JobStatus.COMPLETED.value
    assert job["error"] is None
    assert job["speakers"] == ["Orador A"]
    assert job["audio_duration_seconds"] == 11_520.0

    assert client.get(f"/api/jobs/{job_id}/notes").text.startswith("# Clase de calculo")
    assert "[00:00:00] Orador A" in client.get(f"/api/jobs/{job_id}/transcript").text

    assert len(client.get("/api/jobs").json()) == 1


def test_borrado(client):
    job_id = client.post("/api/jobs", files=_audio()).json()["id"]
    assert client.delete(f"/api/jobs/{job_id}").status_code == 204
    assert client.get(f"/api/jobs/{job_id}").status_code == 404


def test_job_inexistente_devuelve_404(client):
    assert client.get("/api/jobs/noexiste").status_code == 404
