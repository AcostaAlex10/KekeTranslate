"""Tests de la persistencia de trabajos."""

import pytest

from backend.models import Job, JobStatus
from backend.store import JobStore


@pytest.fixture
def store(tmp_path):
    return JobStore(tmp_path / "test.db")


def test_ciclo_de_vida_completo(store):
    store.create(Job(id="job1", filename="clase.mp3"))

    recuperado = store.get("job1")
    assert recuperado is not None
    assert recuperado.status == JobStatus.PENDING

    store.update("job1", status=JobStatus.TRANSCRIBING, provider="assemblyai")
    actualizado = store.get("job1")
    assert actualizado.status == JobStatus.TRANSCRIBING
    assert actualizado.provider == "assemblyai"
    assert actualizado.updated_at >= actualizado.created_at


def test_get_devuelve_none_si_no_existe(store):
    assert store.get("inexistente") is None


def test_update_de_trabajo_inexistente_lanza_keyerror(store):
    with pytest.raises(KeyError):
        store.update("inexistente", status=JobStatus.COMPLETED)


def test_list_ordena_por_fecha_descendente(store):
    store.create(Job(id="a", filename="primera.mp3"))
    store.create(Job(id="b", filename="segunda.mp3"))

    ids = [resumen.id for resumen in store.list()]
    assert set(ids) == {"a", "b"}
    assert len(ids) == 2


def test_delete(store):
    store.create(Job(id="job1", filename="clase.mp3"))
    assert store.delete("job1") is True
    assert store.delete("job1") is False
    assert store.get("job1") is None
