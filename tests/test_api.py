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

    async def annotate(self, transcription, *, filename, contexto=None):
        # El anotador debe recibir el texto con orador y marca de tiempo.
        assert "[00:00:00] Orador A: Hola clase." in transcription.to_diarized_text()
        return "# Clase de calculo\n\n## Resumen ejecutivo\n\nDerivadas."


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Cliente de la API con almacenamiento aislado y servicios simulados."""
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path))
    monkeypatch.setenv("GEMINI_API_KEY", "clave-de-prueba")
    monkeypatch.setenv("ASSEMBLYAI_API_KEY", "clave-de-prueba")

    from backend.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(pipeline, "get_provider", lambda settings: _FakeProvider())
    monkeypatch.setattr(pipeline, "get_annotator", lambda settings: _FakeAnnotator(settings))

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
    assert payload["annotator_key_configured"] is True
    assert payload["annotator_provider"] == "gemini"


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


def test_delete_de_job_inexistente_devuelve_404(client):
    assert client.delete("/api/jobs/noexiste").status_code == 404


def test_apuntes_y_transcripcion_dan_409_antes_de_estar_listos(client, monkeypatch):
    """Pedir el resultado de un trabajo a medio hacer no es un 404: existe."""
    # Un anotador que se cuelga dejaria el job en curso; basta con crear el
    # trabajo a mano en el almacen para representar ese estado intermedio.
    import backend.main as main
    from backend.models import Job

    store = main.get_store()
    store.create(Job(id="encurso", filename="clase.mp3", status=JobStatus.TRANSCRIBING))

    notas = client.get("/api/jobs/encurso/notes")
    assert notas.status_code == 409
    # El mensaje nombra la etapa en que esta, no el codigo interno del estado:
    # "transcribing" no le dice nada a quien esta esperando sus apuntes.
    assert "transcribiendo la clase" in notas.json()["detail"].lower()

    assert client.get("/api/jobs/encurso/transcript").status_code == 409


def test_un_trabajo_fallido_se_ve_en_el_listado(client, monkeypatch):
    """El frontend lista los errores: el resumen debe arrastrar el motivo."""
    import backend.main as main
    from backend.models import Job

    main.get_store().create(
        Job(
            id="roto",
            filename="clase.mp3",
            status=JobStatus.FAILED,
            error="Falta ASSEMBLYAI_API_KEY.",
        )
    )

    resumen = next(j for j in client.get("/api/jobs").json() if j["id"] == "roto")
    assert resumen["status"] == JobStatus.FAILED.value
    assert resumen["error"] == "Falta ASSEMBLYAI_API_KEY."


def test_rechaza_ficheros_que_superan_el_limite(tmp_path, monkeypatch):
    """Al pasarse del tope, la subida se corta y no deja basura en disco."""
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path))
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")

    from backend.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(pipeline, "get_provider", lambda settings: _FakeProvider())
    monkeypatch.setattr(pipeline, "get_annotator", lambda settings: _FakeAnnotator(settings))

    import backend.main as main

    monkeypatch.setattr(main, "_store", None)

    with TestClient(main.app) as client:
        respuesta = client.post("/api/jobs", files=_audio(size=2 * 1024 * 1024))

    assert respuesta.status_code == 413

    # El fichero parcial se borra: si no, cada intento fallido llenaria el disco.
    assert list((tmp_path / "uploads").iterdir()) == []

    get_settings.cache_clear()



def test_un_trabajo_fallido_devuelve_el_error_y_no_un_falso_en_curso(client):
    """Si el trabajo fallo, los apuntes no estan 'en camino': nunca llegaran."""
    import backend.main as main
    from backend.models import Job

    main.get_store().create(
        Job(
            id="fallido",
            filename="clase.mp3",
            status=JobStatus.FAILED,
            error="AssemblyAI rechazo el audio",
        )
    )

    for recurso in ("notes", "transcript"):
        respuesta = client.get(f"/api/jobs/fallido/{recurso}")
        assert respuesta.status_code == 409
        detalle = respuesta.json()["detail"]
        # Se devuelve el error real y nada mas: un prefijo del tipo "el trabajo
        # fallo" solo repite lo que el propio mensaje ya dice.
        assert detalle == "AssemblyAI rechazo el audio"
        assert "todavía no" not in detalle


# ---------------------------------------------------------------------------
# Reintento de los apuntes
# ---------------------------------------------------------------------------


class _AnotadorQueFalla:
    """Simula el fallo real: el modelo no redacta, la transcripcion sí esta."""

    def __init__(self, settings):
        pass

    async def annotate(self, transcription, *, filename, contexto=None):
        from backend.annotator import AnnotationError

        raise AnnotationError("Gemini no genero los apuntes tras varios reintentos.")


def _job_fallido_al_anotar(client, monkeypatch):
    """Deja un trabajo transcrito pero sin apuntes, como pasa en la practica."""
    monkeypatch.setattr(
        pipeline, "get_annotator", lambda settings: _AnotadorQueFalla(settings)
    )
    respuesta = client.post("/api/jobs", files=_audio())
    job_id = respuesta.json()["id"]
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == JobStatus.FAILED.value
    return job_id


def test_se_puede_rehacer_los_apuntes_sin_volver_a_transcribir(client, monkeypatch):
    """Lo caro es la transcripcion: un fallo del anotador no debe tirarla."""
    job_id = _job_fallido_al_anotar(client, monkeypatch)

    # El anotador vuelve a funcionar y se reintenta solo esa parte.
    monkeypatch.setattr(
        pipeline, "get_annotator", lambda settings: _FakeAnnotator(settings)
    )
    respuesta = client.post(f"/api/jobs/{job_id}/reanotar")

    assert respuesta.status_code == 200
    final = client.get(f"/api/jobs/{job_id}").json()
    assert final["status"] == JobStatus.COMPLETED.value
    assert "Resumen ejecutivo" in final["notes_markdown"]
    assert final["error"] is None


def test_al_reanotar_se_conservan_las_marcas_de_tiempo(client, monkeypatch):
    """El anotador simulado exige el formato diarizado: si se perdiera, fallaria."""
    job_id = _job_fallido_al_anotar(client, monkeypatch)
    antes = client.get(f"/api/jobs/{job_id}").json()["transcript_diarized"]

    monkeypatch.setattr(
        pipeline, "get_annotator", lambda settings: _FakeAnnotator(settings)
    )
    client.post(f"/api/jobs/{job_id}/reanotar")

    assert client.get(f"/api/jobs/{job_id}").json()["transcript_diarized"] == antes


def test_reanotar_un_trabajo_inexistente_da_404(client):
    assert client.post("/api/jobs/noexiste/reanotar").status_code == 404


def test_no_se_reanota_lo_que_nunca_se_transcribio(client, monkeypatch):
    """Sin transcripcion no hay nada que reaprovechar; hay que decirlo claro."""
    class _ProveedorQueFalla:
        name = "fake"
        supports_diarization = True

        async def transcribe(self, path, **kwargs):
            from backend.transcription import TranscriptionError

            raise TranscriptionError("AssemblyAI rechazo el audio")

    monkeypatch.setattr(pipeline, "get_provider", lambda settings: _ProveedorQueFalla())
    job_id = client.post("/api/jobs", files=_audio()).json()["id"]

    respuesta = client.post(f"/api/jobs/{job_id}/reanotar")

    assert respuesta.status_code == 409
    assert "subir el audio" in respuesta.json()["detail"]
