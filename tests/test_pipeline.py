"""Tests del orquestador `run_job`, centrados en los caminos de fallo.

`run_job` promete tres cosas en su docstring: que nunca propaga excepciones,
que deja el motivo del fallo en el trabajo, y que solo borra el audio original
cuando el trabajo termina bien. Los tests de la API cubren el camino feliz;
aqui se comprueba lo que pasa cuando algo se rompe, que es justo lo que el
usuario acaba viendo en pantalla.
"""

from __future__ import annotations

import pytest

import backend.pipeline as pipeline
from backend.annotator import AnnotationError
from backend.config import Settings
from backend.models import Job, JobStatus, TranscriptionResult, Utterance
from backend.store import JobStore
from backend.transcription import TranscriptionError

AUDIO = "clase.mp3"


# ---------------------------------------------------------------------------
# Dobles de prueba
# ---------------------------------------------------------------------------


class _ProveedorOk:
    """Proveedor que transcribe correctamente."""

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
                Utterance(speaker="Orador B", start_ms=2000, end_ms=5000,
                          text="Empezamos con derivadas."),
            ],
            audio_duration_seconds=11_520.0,
            language_code="es",
            provider_job_id="fake-1",
        )


class _ProveedorQueFalla(_ProveedorOk):
    """Proveedor que se rinde con un error de dominio."""

    async def transcribe(self, path, **kwargs) -> TranscriptionResult:
        raise TranscriptionError("AssemblyAI rechazo el audio: formato corrupto")


class _ProveedorQueRevienta(_ProveedorOk):
    """Proveedor que lanza un error no previsto por el pipeline."""

    async def transcribe(self, path, **kwargs) -> TranscriptionResult:
        raise ValueError("respuesta JSON inesperada")


class _ProveedorSinDiarizacion(_ProveedorOk):
    """Proveedor que no sabe separar oradores."""

    name = "sin-diarizacion"
    supports_diarization = False


class _AnotadorOk:
    def __init__(self, settings):
        pass

    async def annotate(self, transcription, *, filename, contexto=None):
        return "# Apuntes\n\nContenido."


class _AnotadorQueFalla:
    def __init__(self, settings):
        pass

    async def annotate(self, transcription, *, filename, contexto=None):
        raise AnnotationError("Claude devolvio una respuesta vacia")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    """Prepara ajustes, almacen y un audio de mentira en disco.

    Devuelve una funcion `ejecutar(proveedor, anotador)` que monta los dobles y
    procesa el trabajo, para que cada test solo declare que quiere romper.
    """
    settings = Settings(storage_dir=tmp_path, anthropic_api_key="clave-de-prueba-con-largo-realista")
    settings.ensure_dirs()
    store = JobStore(settings.db_path)

    job_id = "job-test"
    store.create(Job(id=job_id, filename=AUDIO, status=JobStatus.PENDING))
    audio_path = settings.uploads_dir / f"{job_id}_{AUDIO}"
    audio_path.write_bytes(b"\x00" * 128)

    async def ejecutar(proveedor=None, anotador=None):
        monkeypatch.setattr(
            pipeline, "get_provider", lambda s: proveedor or _ProveedorOk()
        )
        clase_anotador = anotador or _AnotadorOk
        monkeypatch.setattr(
            pipeline, "get_annotator", lambda s: clase_anotador(s)
        )
        await pipeline.run_job(job_id, settings, store)
        return store.get(job_id)

    ejecutar.settings = settings
    ejecutar.store = store
    ejecutar.job_id = job_id
    ejecutar.audio_path = audio_path
    return ejecutar


# ---------------------------------------------------------------------------
# Camino feliz (lo minimo para poder contrastar los fallos)
# ---------------------------------------------------------------------------


async def test_trabajo_correcto_queda_completado_y_borra_el_audio(entorno):
    job = await entorno()

    assert job.status == JobStatus.COMPLETED
    assert job.error is None
    assert job.speakers == ["Orador A", "Orador B"]
    assert job.notes_markdown.startswith("# Apuntes")
    assert "[00:00:00] Orador A: Hola clase." in job.transcript_diarized

    # El audio original ya no hace falta una vez transcrito.
    assert not entorno.audio_path.exists()


# ---------------------------------------------------------------------------
# Caminos de fallo
# ---------------------------------------------------------------------------


async def test_fallo_de_transcripcion_deja_el_motivo_en_el_trabajo(entorno):
    job = await entorno(proveedor=_ProveedorQueFalla())

    assert job.status == JobStatus.FAILED
    # El mensaje del proveedor llega intacto al usuario, sin envoltorios.
    assert job.error == "AssemblyAI rechazo el audio: formato corrupto"
    assert job.notes_markdown is None


async def test_error_inesperado_no_tumba_el_trabajo(entorno):
    """Un fallo no previsto tiene que acabar en FAILED, no propagarse."""
    job = await entorno(proveedor=_ProveedorQueRevienta())

    assert job.status == JobStatus.FAILED
    assert job.error.startswith("Fallo inesperado:")
    assert "respuesta JSON inesperada" in job.error


async def test_el_audio_sobrevive_a_un_fallo_para_poder_reintentar(entorno):
    await entorno(proveedor=_ProveedorQueFalla())

    assert entorno.audio_path.exists()


async def test_si_falla_la_anotacion_la_transcripcion_no_se_pierde(entorno):
    """La transcripcion es la parte cara: debe sobrevivir a un fallo del LLM."""
    job = await entorno(anotador=_AnotadorQueFalla)

    assert job.status == JobStatus.FAILED
    assert job.error == "Claude devolvio una respuesta vacia"

    # Guardada tanto en el trabajo...
    assert job.transcript_text == "Hola clase. Empezamos con derivadas."
    assert "[00:00:00] Orador A" in job.transcript_diarized

    # ...como en disco, antes de pasar por Claude.
    volcado = entorno.settings.results_dir / f"{entorno.job_id}_transcripcion.txt"
    assert volcado.exists()
    assert "[00:00:02] Orador B" in volcado.read_text(encoding="utf-8")


async def test_trabajo_inexistente_no_lanza(entorno):
    """Si el trabajo se borro mientras esperaba en cola, se ignora sin ruido."""
    await pipeline.run_job("no-existe", entorno.settings, entorno.store)


# ---------------------------------------------------------------------------
# Diarizacion
# ---------------------------------------------------------------------------


async def test_proveedor_sin_diarizacion_completa_igual(entorno):
    """Pedir oradores a un proveedor que no los soporta no debe romper nada."""
    assert entorno.settings.enable_diarization is True

    job = await entorno(proveedor=_ProveedorSinDiarizacion())

    assert job.status == JobStatus.COMPLETED
    assert job.provider == "sin-diarizacion"


# ---------------------------------------------------------------------------
# Progreso
# ---------------------------------------------------------------------------


async def test_el_progreso_del_proveedor_se_refleja_en_el_estado(entorno, monkeypatch):
    """Los mensajes del proveedor deben mover el estado que ve el frontend."""
    vistos: list[JobStatus] = []
    original = entorno.store.update

    def espia(job_id, **fields):
        if "status" in fields:
            vistos.append(fields["status"])
        return original(job_id, **fields)

    monkeypatch.setattr(entorno.store, "update", espia)
    await entorno()

    assert JobStatus.UPLOADING in vistos       # "Subiendo el audio..."
    assert JobStatus.TRANSCRIBING in vistos    # "Transcripcion en curso..."
    assert JobStatus.ANNOTATING in vistos
    assert vistos[-1] == JobStatus.COMPLETED
