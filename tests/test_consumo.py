"""El libro de cuentas: cuanto ha gastado cada persona.

Existe porque esta decidido que se va a cobrar y porque el gasto **no se puede
reconstruir hacia atras**: los minutos de audio de una clase ya procesada se
podrian deducir, pero cuantas peticiones costo redactar sus apuntes no lo sabe
nadie una vez ha pasado.

Las dos propiedades que estos tests protegen, que son las que se pierden al
implementar esto de la forma obvia:

1. **Se apunta tambien lo que fallo.** Si la anotacion se cae a mitad de una
   clase larga, las peticiones que llegaron a salir se pagaron igual. Un
   contador que solo mire los exitos da de menos justo en los meses malos.
2. **Borrar una clase no borra lo que costo.** Es un libro de cuentas, no un
   dato derivado de los trabajos.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import backend.pipeline as pipeline
from backend.annotator.base import AnnotationError, BaseAnnotator
from backend.config import Settings
from backend.consumo import Consumo
from backend.models import Gasto, TranscriptionResult, Utterance

CLASE = "Hoy vemos integracion por partes."


# ---------------------------------------------------------------------------
# El almacen, por su cuenta
# ---------------------------------------------------------------------------


@pytest.fixture
def libro(tmp_path) -> Consumo:
    return Consumo(tmp_path / "keketranslate.db")


def test_una_cuenta_nueva_no_ha_gastado_nada(libro):
    resumen = libro.resumen("u1")

    assert resumen.mes.clases_transcritas == 0
    assert resumen.total.segundos_de_audio == 0


def test_se_suman_los_minutos_y_las_peticiones(libro):
    libro.transcripcion("u1", "j1", "assemblyai", 3600.0)
    libro.anotacion("u1", "j1", "gemini", "gemini-3.7-flash", Gasto(
        peticiones=2, caracteres_entrada=90_000, caracteres_salida=12_000
    ))

    total = libro.resumen("u1").total
    assert total.clases_transcritas == 1
    assert total.segundos_de_audio == 3600.0
    assert total.peticiones_al_modelo == 2
    assert total.caracteres_entrada == 90_000
    assert total.caracteres_salida == 12_000


def test_cada_cual_ve_lo_suyo(libro):
    """Es lo mismo que ya vale para las clases, y aqui son cifras de dinero."""
    libro.transcripcion("u1", "j1", "assemblyai", 3600.0)
    libro.transcripcion("u2", "j2", "assemblyai", 7200.0)

    assert libro.resumen("u1").total.segundos_de_audio == 3600.0
    assert libro.resumen("u2").total.segundos_de_audio == 7200.0


def test_lo_del_mes_pasado_no_cuenta_este_mes(libro):
    """El mes es lo que interesa para una factura; el total, para lo demas."""
    libro.transcripcion("u1", "j1", "assemblyai", 3600.0)
    viejo = datetime.now(timezone.utc).replace(day=1) - timedelta(days=5)
    libro._apuntar(
        usuario_id="u1",
        job_id="j0",
        concepto="transcripcion",
        segundos_de_audio=7200.0,
        momento=viejo.isoformat(),
    )

    resumen = libro.resumen("u1")
    assert resumen.mes.segundos_de_audio == 3600.0
    assert resumen.total.segundos_de_audio == 10_800.0


def test_una_anotacion_que_no_llego_a_pedir_nada_no_deja_fila(libro):
    """Una fila de ceros solo ensucia: no costo nada."""
    libro.anotacion("u1", "j1", "gemini", "gemini-3.7-flash", Gasto())

    assert libro.resumen("u1").total.peticiones_al_modelo == 0


# ---------------------------------------------------------------------------
# Lo que cuenta el anotador
# ---------------------------------------------------------------------------


class _AnotadorContado(BaseAnnotator):
    """Anotador que responde algo fijo, para poder medir lo que se apunta."""

    respuesta = "unos apuntes"
    fallar_en = None

    async def _complete(self, system_prompt: str, user_prompt: str) -> str:
        if self.fallar_en is not None and self.gasto.peticiones >= self.fallar_en:
            raise AnnotationError("El modelo no contesta.")
        return self.respuesta

    async def _respirar(self) -> None:
        """El ritmo se prueba en su propio fichero."""


def _transcripcion(texto: str = CLASE) -> TranscriptionResult:
    return TranscriptionResult(
        provider="fake",
        text=texto,
        utterances=[Utterance(speaker="Orador A", start_ms=0, end_ms=3000, text=texto)],
        audio_duration_seconds=3600.0,
        language_code="es",
    )


def _ajustes(**extra) -> Settings:
    valores = {"gemini_api_key": "clave-de-prueba-con-largo-realista"}
    valores.update(extra)
    return Settings(_env_file=None, **valores)


@pytest.mark.asyncio
async def test_una_clase_normal_cuenta_una_peticion():
    anotador = _AnotadorContado(_ajustes())

    await anotador.annotate(_transcripcion(), filename="clase.mp3")

    assert anotador.gasto.peticiones == 1
    assert anotador.gasto.caracteres_salida == len(_AnotadorContado.respuesta)
    assert anotador.gasto.caracteres_entrada > len(CLASE)


@pytest.mark.asyncio
async def test_una_clase_larga_cuenta_todas_sus_peticiones():
    """Cada fragmento del map-reduce es una peticion que se paga."""
    anotador = _AnotadorContado(
        _ajustes(annotation_single_pass_char_limit=100, annotation_chunk_chars=60)
    )
    larga = _transcripcion("\n".join(["Orador A: " + "palabra " * 6] * 12))

    await anotador.annotate(larga, filename="clase.mp3")

    assert anotador.gasto.peticiones > 2


@pytest.mark.asyncio
async def test_lo_gastado_antes_de_un_fallo_sigue_contando():
    """La mitad que un contador ingenuo pierde.

    Si la anotacion revienta en el cuarto fragmento, los tres primeros salieron
    y se pagaron. Contarlos solo cuando todo va bien da de menos justo en los
    meses en los que el proveedor falla.
    """
    anotador = _AnotadorContado(
        _ajustes(annotation_single_pass_char_limit=100, annotation_chunk_chars=60)
    )
    anotador.fallar_en = 3
    larga = _transcripcion("\n".join(["Orador A: " + "palabra " * 6] * 12))

    with pytest.raises(AnnotationError):
        await anotador.annotate(larga, filename="clase.mp3")

    assert anotador.gasto.peticiones >= 3
    assert anotador.gasto.caracteres_entrada > 0


@pytest.mark.asyncio
async def test_cada_anotacion_empieza_a_contar_de_cero():
    """Reutilizar el anotador no puede sumar el gasto de la clase anterior."""
    anotador = _AnotadorContado(_ajustes())

    await anotador.annotate(_transcripcion(), filename="una.mp3")
    await anotador.annotate(_transcripcion(), filename="otra.mp3")

    assert anotador.gasto.peticiones == 1


# ---------------------------------------------------------------------------
# De punta a punta, por la API
# ---------------------------------------------------------------------------


class _Proveedor:
    name = "assemblyai"
    supports_diarization = True

    async def transcribe(self, path, **kwargs) -> TranscriptionResult:
        return _transcripcion()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path))
    monkeypatch.setenv("GEMINI_API_KEY", "clave-de-prueba-con-largo-realista")
    monkeypatch.setenv("ASSEMBLYAI_API_KEY", "clave-de-prueba-con-largo-realista")

    from backend.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(pipeline, "get_provider", lambda settings: _Proveedor())
    monkeypatch.setattr(pipeline, "get_annotator", lambda s: _AnotadorContado(s))

    import backend.main as main

    monkeypatch.setattr(main, "_store", None)
    monkeypatch.setattr(main, "_biblioteca", None)
    monkeypatch.setattr(main, "_usuarios", None)
    monkeypatch.setattr(main, "_consumo", None)

    with TestClient(main.app) as test_client:
        respuesta = test_client.post(
            "/api/auth/registro",
            json={"email": "alumno@unam.edu.ar", "password": "una-frase-larga"},
        )
        test_client.headers["Authorization"] = f"Bearer {respuesta.json()['token']}"
        yield test_client

    get_settings.cache_clear()


def _subir(client):
    return client.post(
        "/api/jobs", files={"file": ("clase.mp3", b"\x00" * 4096, "audio/mpeg")}
    ).json()


def test_procesar_una_clase_deja_apuntado_lo_que_costo(client):
    _subir(client)

    total = client.get("/api/consumo").json()["total"]
    assert total["clases_transcritas"] == 1
    assert total["segundos_de_audio"] == 3600.0
    assert total["peticiones_al_modelo"] == 1


def test_rehacer_los_apuntes_suma_modelo_pero_no_transcripcion(client):
    """Es el motivo entero de que exista rehacer: no se vuelve a transcribir."""
    creada = _subir(client)

    client.post(f"/api/jobs/{creada['id']}/reanotar")

    total = client.get("/api/consumo").json()["total"]
    assert total["clases_transcritas"] == 1, "la transcripcion no se repitio"
    assert total["peticiones_al_modelo"] == 2


def test_borrar_la_clase_no_borra_lo_que_costo(client):
    """El proveedor ya lo cobro. Es un libro de cuentas, no un dato derivado."""
    creada = _subir(client)

    client.delete(f"/api/jobs/{creada['id']}")

    assert client.get("/api/jobs").json() == []
    assert client.get("/api/consumo").json()["total"]["segundos_de_audio"] == 3600.0


def test_nadie_ve_el_consumo_de_otra_persona(client):
    """No hay parametro para pedir el de otro: no se puede pedir y ya esta."""
    _subir(client)

    otra = client.post(
        "/api/auth/registro",
        json={"email": "otra@unam.edu.ar", "password": "otra-frase-larga"},
    ).json()
    client.headers["Authorization"] = f"Bearer {otra['token']}"

    assert client.get("/api/consumo").json()["total"]["clases_transcritas"] == 0


def test_el_consumo_exige_sesion(client):
    del client.headers["Authorization"]

    assert client.get("/api/consumo").status_code == 401


# ---------------------------------------------------------------------------
# La interfaz
# ---------------------------------------------------------------------------

import streamlit as st  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

from .backend_de_mentira import TESTIGO, BackendDeMentira  # noqa: E402


def test_el_consumo_se_ve_en_la_barra_lateral(monkeypatch):
    """Ensenarlo desde el principio evita la sorpresa del primer recibo."""
    servidor = BackendDeMentira(clases=2)
    monkeypatch.setenv("BACKEND_URL", servidor.url)
    st.cache_data.clear()
    st.cache_resource.clear()
    try:
        app = AppTest.from_file("frontend/app.py", default_timeout=90)
        app.session_state["sesion"] = TESTIGO
        app.run()
        assert not app.exception, app.exception

        pies = " ".join(c.value for c in app.caption)
        assert "Este mes" in pies
        # 7200 segundos son dos horas: el dato se formatea, no se suelta crudo.
        assert "2 h 00 min" in pies
    finally:
        servidor.cerrar()
        st.cache_data.clear()
        st.cache_resource.clear()
