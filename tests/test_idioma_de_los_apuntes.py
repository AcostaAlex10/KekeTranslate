"""Los apuntes se piden en un idioma, y solo los apuntes.

Es el nombre del producto: se puede cursar en un idioma y recibir los apuntes en
otro, y el idioma se elige **en cada clase**, no una vez para todo.

La regla que mas se protege aqui es la segunda mitad: **la transcripcion nunca
se traduce**. Es el registro fiel de lo que se dijo, es a donde se vuelve cuando
un apunte no se entiende, y traducirla costaria otra pasada entera del modelo
sobre 50-70k tokens. Si algun dia alguien decide traducirla tambien, sera
anadiendo algo, no cambiando esto.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import backend.pipeline as pipeline
from backend.annotator.base import BaseAnnotator
from backend.config import Settings
from backend.models import TranscriptionResult, Utterance

CLASE = "Hoy vemos integracion por partes."


class _AnotadorEspia(BaseAnnotator):
    """Anotador real que guarda los prompts en vez de enviarlos a nadie."""

    prompts_vistos: list[str] = []

    async def _complete(self, system_prompt: str, user_prompt: str) -> str:
        _AnotadorEspia.prompts_vistos.append(user_prompt)
        return "# Apuntes\n\n## Resumen ejecutivo\n\nIntegration by parts."

    async def _respirar(self) -> None:
        """El ritmo se prueba en su propio fichero; aqui solo estorbaria."""


def _transcripcion(texto: str = CLASE) -> TranscriptionResult:
    return TranscriptionResult(
        provider="fake",
        text=texto,
        utterances=[Utterance(speaker="Orador A", start_ms=0, end_ms=3000, text=texto)],
        audio_duration_seconds=3600.0,
        language_code="es",
    )


@pytest.fixture(autouse=True)
def _sin_prompts_viejos():
    _AnotadorEspia.prompts_vistos = []


def _ajustes(**extra) -> Settings:
    valores = {"gemini_api_key": "clave-de-prueba-con-largo-realista"}
    valores.update(extra)
    return Settings(_env_file=None, **valores)


# ---------------------------------------------------------------------------
# Lo que llega al modelo
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_el_idioma_pedido_llega_al_prompt():
    espia = _AnotadorEspia(_ajustes())

    await espia.annotate(_transcripcion(), filename="clase.mp3", idioma="en")

    prompt = _AnotadorEspia.prompts_vistos[-1]
    assert "IDIOMA DE SALIDA" in prompt
    assert "inglés" in prompt


@pytest.mark.asyncio
async def test_el_prompt_nombra_el_idioma_en_si_mismo():
    """«Redacta en aleman» deja margen a contestar *sobre* el aleman.

    El prompt esta escrito en espanol, asi que el nombre del idioma tambien lo
    esta. Anadir el endonimo quita la ambiguedad.
    """
    espia = _AnotadorEspia(_ajustes())

    await espia.annotate(_transcripcion(), filename="clase.mp3", idioma="de")

    assert "Deutsch" in _AnotadorEspia.prompts_vistos[-1]


@pytest.mark.asyncio
async def test_sin_idioma_el_prompt_queda_como_estaba():
    """Una clase normal no debe pagar el coste de una seccion vacia."""
    espia = _AnotadorEspia(_ajustes())

    await espia.annotate(_transcripcion(), filename="clase.mp3")

    assert "IDIOMA DE SALIDA" not in _AnotadorEspia.prompts_vistos[-1]


@pytest.mark.asyncio
async def test_un_codigo_desconocido_no_deja_la_clase_sin_apuntes():
    """El sitio donde se rechaza un idioma invalido es la API, no el anotador.

    Aqui ya es tarde: la transcripcion esta pagada y hecha. Quedarse sin
    apuntes es peor que tenerlos en el idioma de la clase.
    """
    espia = _AnotadorEspia(_ajustes())

    apuntes = await espia.annotate(
        _transcripcion(), filename="clase.mp3", idioma="klingon"
    )

    assert apuntes
    assert "IDIOMA DE SALIDA" not in _AnotadorEspia.prompts_vistos[-1]


@pytest.mark.asyncio
async def test_en_una_clase_larga_solo_se_traduce_al_fusionar():
    """Traducir cada fragmento seria traducir dos veces.

    Los extractos de trabajo son intermedios: se quedan en el idioma de la
    clase, y la traduccion ocurre una sola vez, al componer los apuntes. Cada
    paso intermedio aleja el texto de lo que se dijo.
    """
    espia = _AnotadorEspia(
        _ajustes(annotation_single_pass_char_limit=100, annotation_chunk_chars=60)
    )
    larga = _transcripcion("\n".join(["Orador A: " + "palabra " * 6] * 12))

    await espia.annotate(larga, filename="clase.mp3", idioma="en")

    prompts_vistos = _AnotadorEspia.prompts_vistos
    assert len(prompts_vistos) > 2, "el troceado no llego a activarse"
    fragmentos, fusion = prompts_vistos[:-1], prompts_vistos[-1]
    assert all("IDIOMA DE SALIDA" not in p for p in fragmentos)
    assert "IDIOMA DE SALIDA" in fusion


# ---------------------------------------------------------------------------
# La API
# ---------------------------------------------------------------------------


class _Proveedor:
    name = "fake"
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
    monkeypatch.setattr(pipeline, "get_annotator", lambda s: _AnotadorEspia(s))

    import backend.main as main

    monkeypatch.setattr(main, "_store", None)
    monkeypatch.setattr(main, "_biblioteca", None)
    monkeypatch.setattr(main, "_usuarios", None)

    with TestClient(main.app) as test_client:
        respuesta = test_client.post(
            "/api/auth/registro",
            json={"email": "alumno@unam.edu.ar", "password": "una-frase-larga"},
        )
        test_client.headers["Authorization"] = f"Bearer {respuesta.json()['token']}"
        yield test_client

    get_settings.cache_clear()


def _audio():
    return {"file": ("clase.mp3", b"\x00" * 4096, "audio/mpeg")}


def _subir(client, **params):
    return client.post("/api/jobs", files=_audio(), params=params)


def test_health_publica_los_idiomas(client):
    """La interfaz los saca de aqui para no tener su propia copia."""
    idiomas = client.get("/api/health").json()["idiomas_de_apuntes"]

    codigos = [i["codigo"] for i in idiomas]
    assert "en" in codigos
    assert {"codigo", "nombre", "endonimo"} <= set(idiomas[0])


def test_el_idioma_elegido_se_guarda_en_la_clase(client):
    creada = _subir(client, idioma="en").json()

    assert creada["idioma_apuntes"] == "en"


def test_una_clase_sin_idioma_no_lo_lleva(client):
    assert _subir(client).json()["idioma_apuntes"] is None


def test_un_idioma_que_no_existe_se_rechaza_al_subir(client):
    """Y se rechaza entrando, mientras quien lo mando sigue escuchando.

    Dejarlo pasar significaria enterarse una hora despues, con la clase ya
    transcrita y a medias.
    """
    respuesta = _subir(client, idioma="klingon")

    assert respuesta.status_code == 422
    assert "klingon" in respuesta.json()["detail"]
    assert client.get("/api/jobs").json() == []


def test_el_idioma_aparece_en_el_listado(client):
    """`JobStore.list` monta el resumen campo a campo: se olvida solo."""
    _subir(client, idioma="pt")

    assert client.get("/api/jobs").json()[0]["idioma_apuntes"] == "pt"


def test_la_transcripcion_no_se_traduce(client):
    """La mitad que no se toca, y la que mas importa.

    Los apuntes salen en otro idioma; la transcripcion se queda exactamente
    como la devolvio el proveedor.
    """
    creada = _subir(client, idioma="en").json()

    detalle = client.get(f"/api/jobs/{creada['id']}").json()
    assert detalle["status"] == "completed"
    assert detalle["transcript_text"] == CLASE


# --- Cambiarlo despues ------------------------------------------------------


def test_rehacer_con_otro_idioma_lo_cambia(client):
    """Es la unica forma de traducir sin volver a pagar la transcripcion."""
    creada = _subir(client).json()

    rehecha = client.post(f"/api/jobs/{creada['id']}/reanotar", params={"idioma": "en"})

    assert rehecha.json()["idioma_apuntes"] == "en"
    assert "IDIOMA DE SALIDA" in _AnotadorEspia.prompts_vistos[-1]


def test_rehacer_sin_decir_nada_conserva_el_idioma(client):
    """Rehacer existe para reintentar tras un fallo del modelo.

    Quien reintenta no esta pidiendo volver al idioma de la clase.
    """
    creada = _subir(client, idioma="en").json()

    rehecha = client.post(f"/api/jobs/{creada['id']}/reanotar")

    assert rehecha.json()["idioma_apuntes"] == "en"


def test_rehacer_con_el_idioma_vacio_vuelve_al_de_la_clase(client):
    creada = _subir(client, idioma="en").json()

    rehecha = client.post(f"/api/jobs/{creada['id']}/reanotar", params={"idioma": ""})

    assert rehecha.json()["idioma_apuntes"] is None
    assert "IDIOMA DE SALIDA" not in _AnotadorEspia.prompts_vistos[-1]


def test_rehacer_con_un_idioma_invalido_no_toca_la_clase(client):
    creada = _subir(client, idioma="en").json()

    respuesta = client.post(
        f"/api/jobs/{creada['id']}/reanotar", params={"idioma": "klingon"}
    )

    assert respuesta.status_code == 422
    assert client.get(f"/api/jobs/{creada['id']}").json()["idioma_apuntes"] == "en"


# ---------------------------------------------------------------------------
# La interfaz
# ---------------------------------------------------------------------------

import streamlit as st  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

from .backend_de_mentira import TESTIGO, BackendDeMentira  # noqa: E402


@pytest.fixture
def pantalla(monkeypatch):
    """La app hablando con el backend de mentira, ya dentro de una cuenta."""
    servidor = BackendDeMentira(clases=3)
    monkeypatch.setenv("BACKEND_URL", servidor.url)
    st.cache_data.clear()
    st.cache_resource.clear()

    def abrir(**estado) -> AppTest:
        app = AppTest.from_file("frontend/app.py", default_timeout=90)
        app.session_state["sesion"] = TESTIGO
        app.session_state["seccion"] = "clases"
        for clave, valor in estado.items():
            app.session_state[clave] = valor
        app.run()
        assert not app.exception, app.exception
        return app

    yield abrir
    servidor.cerrar()
    st.cache_data.clear()
    st.cache_resource.clear()


def test_la_lista_distingue_una_clase_traducida(pantalla):
    """Sin esto habria que abrirlas una por una para saber cual es cual."""
    app = pantalla()

    pies = " ".join(c.value for c in app.caption)
    assert "inglés" in pies


def test_la_ficha_deja_cambiar_el_idioma_de_los_apuntes(pantalla):
    """Cambiar el idioma **es** rehacer: no hay forma de traducir sin el modelo.

    Por eso el selector vive junto al boton de rehacer y no en un sitio aparte.
    """
    app = pantalla(clase_abierta="j1")

    selectores = [s for s in app.selectbox if s.key == "idioma_rehacer_j1"]
    assert selectores, [s.key for s in app.selectbox]
    assert "El de la clase" in selectores[0].options
