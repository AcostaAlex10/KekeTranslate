"""Grabar una clase por trozos, mientras ocurre.

La grabadora vieja guardaba la clase entera en la memoria del navegador y no
enviaba nada hasta pararla: una clase de 4 h eran unos 2,5 GB, y si el telefono
se quedaba sin bateria no quedaba nada de nada.

Ahora la clase se crea vacia y los trozos van llegando segun se graban. Lo que
estos tests protegen:

1. **El audio se reconstruye exacto.** Los trozos de `MediaRecorder` no son
   ficheros sueltos —solo el primero lleva cabecera—: el fichero valido es la
   concatenacion de todos **en orden**. Un trozo perdido no da error en ningun
   sitio; da un audio roto por dentro que nadie descubre hasta oirlo.
2. **Reenviar es normal, saltarse uno no.** El movil pierde la red un momento y
   el navegador reintenta: eso tiene que ser inofensivo. Un hueco, en cambio,
   tiene que doler enseguida.
3. **Una grabacion interrumpida conserva lo que llego.** Es la mitad del valor:
   si el telefono muere en el minuto 200, hay 200 minutos de clase.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import backend.pipeline as pipeline
from backend.models import TranscriptionResult, Utterance

ORIGEN_DE_LA_APP = "http://localhost:8501"


class _Proveedor:
    """Proveedor que se queda con los bytes exactos que le llegaron."""

    name = "assemblyai"
    supports_diarization = True
    audio_visto: bytes = b""

    async def transcribe(self, path, **kwargs) -> TranscriptionResult:
        _Proveedor.audio_visto = path.read_bytes()
        return TranscriptionResult(
            provider="fake",
            text="Hoy vemos integracion por partes.",
            utterances=[
                Utterance(
                    speaker="Orador A", start_ms=0, end_ms=3000,
                    text="Hoy vemos integracion por partes.",
                )
            ],
            audio_duration_seconds=3600.0,
        )


class _Anotador:
    def __init__(self, settings):
        self.gasto = None

    async def annotate(self, transcription, *, filename, contexto=None, idioma=None):
        return "# Apuntes\n\n## Resumen ejecutivo\n\nTexto."


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path))
    monkeypatch.setenv("GEMINI_API_KEY", "clave-de-prueba-con-largo-realista")
    monkeypatch.setenv("ASSEMBLYAI_API_KEY", "clave-de-prueba-con-largo-realista")

    from backend.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(pipeline, "get_provider", lambda settings: _Proveedor())
    monkeypatch.setattr(pipeline, "get_annotator", lambda s: _Anotador(s))

    import backend.main as main

    monkeypatch.setattr(main, "_store", None)
    monkeypatch.setattr(main, "_biblioteca", None)
    monkeypatch.setattr(main, "_usuarios", None)
    monkeypatch.setattr(main, "_consumo", None)

    _Proveedor.audio_visto = b""

    with TestClient(main.app) as test_client:
        respuesta = test_client.post(
            "/api/auth/registro",
            json={"email": "alumno@unam.edu.ar", "password": "una-frase-larga"},
        )
        test_client.headers["Authorization"] = f"Bearer {respuesta.json()['token']}"
        yield test_client

    get_settings.cache_clear()


def _empezar(client, **params):
    return client.post("/api/jobs/grabacion", params=params)


def _parte(client, job_id: str, n: int, datos: bytes):
    return client.put(f"/api/jobs/{job_id}/parte", params={"n": n}, content=datos)


# ---------------------------------------------------------------------------
# El camino normal
# ---------------------------------------------------------------------------


def test_la_clase_existe_desde_el_primer_segundo(client):
    """Aparece en la lista mientras se graba, y sobrevive a cerrar el navegador."""
    creada = _empezar(client, nombre="clase de calculo").json()

    assert creada["status"] == "uploading"
    assert creada["partes_recibidas"] == 0
    assert creada["filename"].endswith(".webm")
    assert client.get("/api/jobs").json()[0]["id"] == creada["id"]


def test_el_audio_llega_entero_y_en_orden(client):
    """Un trozo de `MediaRecorder` no es un fichero: el fichero son todos."""
    trozos = [b"CABECERA-y-primer-trozo", b"segundo-trozo", b"tercer-trozo"]
    creada = _empezar(client)

    for n, trozo in enumerate(trozos):
        assert _parte(client, creada.json()["id"], n, trozo).status_code == 200
    client.post(f"/api/jobs/{creada.json()['id']}/cerrar")

    assert _Proveedor.audio_visto == b"".join(trozos)


def test_cerrar_encola_el_procesado(client):
    creada = _empezar(client).json()
    _parte(client, creada["id"], 0, b"audio")

    client.post(f"/api/jobs/{creada['id']}/cerrar")

    assert client.get(f"/api/jobs/{creada['id']}").json()["status"] == "completed"


def test_el_tamano_se_ve_crecer(client):
    """Es lo unico que dice, mientras se graba, que el audio esta llegando."""
    creada = _empezar(client).json()

    _parte(client, creada["id"], 0, b"x" * 1000)
    segunda = _parte(client, creada["id"], 1, b"x" * 500).json()

    assert segunda["file_size_bytes"] == 1500
    assert segunda["partes_recibidas"] == 2


# ---------------------------------------------------------------------------
# Cuando la red falla
# ---------------------------------------------------------------------------


def test_reenviar_un_trozo_no_lo_duplica(client):
    """El movil pierde la red un momento y el navegador reintenta.

    Si el reenvio se anadiera otra vez, el audio saldria con un trozo repetido:
    la clase se oiria bien hasta el tropiezo y a partir de ahi diria dos veces
    lo mismo. Se acepta y se ignora.
    """
    creada = _empezar(client).json()
    _parte(client, creada["id"], 0, b"uno")
    _parte(client, creada["id"], 1, b"dos")

    repetido = _parte(client, creada["id"], 1, b"dos")

    assert repetido.status_code == 200
    assert repetido.json()["partes_recibidas"] == 2
    client.post(f"/api/jobs/{creada['id']}/cerrar")
    assert _Proveedor.audio_visto == b"unodos"


def test_saltarse_un_trozo_se_rechaza_diciendo_cual_toca(client):
    """Aceptarlo daria un audio roto por dentro que nadie descubre hasta oirlo."""
    creada = _empezar(client).json()
    _parte(client, creada["id"], 0, b"uno")

    respuesta = _parte(client, creada["id"], 5, b"muy-adelantado")

    assert respuesta.status_code == 409
    assert "1" in respuesta.json()["detail"]
    assert client.get(f"/api/jobs/{creada['id']}").json()["partes_recibidas"] == 1


def test_un_trozo_vacio_se_rechaza(client):
    creada = _empezar(client).json()

    assert _parte(client, creada["id"], 0, b"").status_code == 400


# ---------------------------------------------------------------------------
# Cuando la grabacion se corta
# ---------------------------------------------------------------------------


def test_una_grabacion_interrumpida_conserva_lo_que_llego(client):
    """Si el telefono muere en el minuto 200, hay 200 minutos de clase.

    Es la mitad del valor de subir por trozos. Cerrarla despues es exactamente
    lo mismo que cerrar una que acabo bien: lo que hay en disco ya es audio
    valido, solo que mas corto.
    """
    creada = _empezar(client).json()
    _parte(client, creada["id"], 0, b"lo-que-dio-tiempo")
    # Aqui el navegador desaparece sin cerrar nada.

    en_la_lista = client.get("/api/jobs").json()[0]
    assert en_la_lista["status"] == "uploading"
    assert en_la_lista["partes_recibidas"] == 1, "es lo que la distingue de una subida"

    client.post(f"/api/jobs/{creada['id']}/cerrar")
    assert _Proveedor.audio_visto == b"lo-que-dio-tiempo"


def test_cerrar_sin_audio_no_crea_una_clase_muda(client):
    creada = _empezar(client).json()

    respuesta = client.post(f"/api/jobs/{creada['id']}/cerrar")

    assert respuesta.status_code == 400
    assert "micrófono" in respuesta.json()["detail"]


def test_no_se_puede_seguir_grabando_sobre_una_clase_cerrada(client):
    creada = _empezar(client).json()
    _parte(client, creada["id"], 0, b"audio")
    client.post(f"/api/jobs/{creada['id']}/cerrar")

    assert _parte(client, creada["id"], 1, b"tarde").status_code == 409
    assert client.post(f"/api/jobs/{creada['id']}/cerrar").status_code == 409


# ---------------------------------------------------------------------------
# Lo que se rechaza de entrada
# ---------------------------------------------------------------------------


def test_un_formato_que_no_se_admite_se_dice_al_empezar(client):
    """Y no una hora despues, cuando el proveedor lo rechace por la extension."""
    respuesta = _empezar(client, tipo="audio/quimera")

    assert respuesta.status_code == 415
    assert client.get("/api/jobs").json() == []


def test_el_tipo_con_codecs_detras_se_entiende(client):
    """`MediaRecorder` devuelve `audio/webm;codecs=opus`, no `audio/webm`."""
    creada = _empezar(client, tipo="audio/webm;codecs=opus")

    assert creada.status_code == 201
    assert creada.json()["filename"].endswith(".webm")


def test_la_grabacion_de_otra_persona_no_existe(client):
    creada = _empezar(client).json()
    otra = client.post(
        "/api/auth/registro",
        json={"email": "otra@unam.edu.ar", "password": "otra-frase-larga"},
    ).json()
    client.headers["Authorization"] = f"Bearer {otra['token']}"

    assert _parte(client, creada["id"], 0, b"audio").status_code == 404
    assert client.post(f"/api/jobs/{creada['id']}/cerrar").status_code == 404


def test_pasarse_del_tope_no_tira_lo_ya_grabado(client, monkeypatch):
    """Se avisa y se para, pero la clase sigue ahi para procesarla."""
    from backend.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    creada = _empezar(client).json()

    _parte(client, creada["id"], 0, b"x" * 900_000)
    respuesta = _parte(client, creada["id"], 1, b"x" * 900_000)

    assert respuesta.status_code == 413
    assert client.get(f"/api/jobs/{creada['id']}").json()["partes_recibidas"] == 1
    assert client.post(f"/api/jobs/{creada['id']}/cerrar").status_code == 200


# ---------------------------------------------------------------------------
# CORS: el navegador habla con el backend, y es lo unico que lo hace
# ---------------------------------------------------------------------------


def test_el_navegador_puede_subir_trozos_desde_la_app(client):
    """La comprobacion previa llega **sin** sesion: el navegador no la manda.

    Si el middleware que exige sesion la viera antes que CORS, contestaria 401 y
    el navegador ni siquiera intentaria la subida.
    """
    respuesta = client.options(
        "/api/jobs/loquesea/parte",
        headers={
            "Origin": ORIGEN_DE_LA_APP,
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "authorization",
        },
    )

    assert respuesta.status_code == 200
    assert respuesta.headers["access-control-allow-origin"] == ORIGEN_DE_LA_APP


def test_una_pagina_cualquiera_no_puede_hablar_con_la_api(client):
    respuesta = client.options(
        "/api/jobs/loquesea/parte",
        headers={
            "Origin": "https://sitio-de-otro.example",
            "Access-Control-Request-Method": "PUT",
        },
    )

    assert "access-control-allow-origin" not in respuesta.headers


def test_un_error_llega_al_navegador_y_no_como_fallo_de_red(client):
    """Sin cabeceras de CORS en la respuesta de error, el componente solo ve
    "failed to fetch" y no puede contar lo que pasa."""
    sin_sesion = TestClient(client.app)
    respuesta = sin_sesion.put(
        "/api/jobs/loquesea/parte",
        params={"n": 0},
        content=b"audio",
        headers={"Origin": ORIGEN_DE_LA_APP},
    )

    assert respuesta.status_code == 401
    assert respuesta.headers["access-control-allow-origin"] == ORIGEN_DE_LA_APP


# ---------------------------------------------------------------------------
# La grabadora en la pagina
# ---------------------------------------------------------------------------

import streamlit as st  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

from .backend_de_mentira import TESTIGO, BackendDeMentira  # noqa: E402


def _guion_de_la_grabadora(app: AppTest) -> str:
    guiones = [m.proto.srcdoc for m in app.get("iframe") if "grabar" in m.proto.srcdoc]
    assert guiones, "no se dibujo la grabadora"
    return guiones[0]


@pytest.fixture
def pantalla(monkeypatch):
    servidor = BackendDeMentira(clases=2)
    monkeypatch.setenv("BACKEND_URL", servidor.url)
    st.cache_data.clear()
    st.cache_resource.clear()

    def abrir() -> AppTest:
        app = AppTest.from_file("frontend/app.py", default_timeout=90)
        app.session_state["sesion"] = TESTIGO
        app.session_state["seccion"] = "nueva"
        app.session_state["modo_de_carga"] = "grabar"
        app.run()
        assert not app.exception, app.exception
        return app

    yield abrir
    servidor.cerrar()
    st.cache_data.clear()
    st.cache_resource.clear()


def test_el_html_de_la_grabadora_no_cambia_entre_pasadas(pantalla):
    """La invariante de la que depende que una grabación sobreviva.

    Streamlit repinta el script entero ante cualquier interacción, y esta
    comprobado en un navegador de verdad que el iframe solo sobrevive si su HTML
    es **el mismo**. Cambiarlo lo recarga, y con el se lleva por delante el
    `MediaRecorder` y la clase que se estuviera grabando.

    Por eso aqui no puede entrar nada que varie: ni la hora, ni un
    identificador nuevo, ni el estado de la grabacion.
    """
    primera = _guion_de_la_grabadora(pantalla())
    segunda = _guion_de_la_grabadora(pantalla())

    assert primera == segunda


def test_la_grabadora_lleva_dentro_los_grupos_y_los_idiomas(pantalla):
    """Van dentro del componente porque no pueden llegarle desde fuera.

    Un selector de Streamlit obligaria a pasarle su valor al componente, y eso
    es cambiarle el HTML: la grabacion moriria al elegir la materia.
    """
    guion = _guion_de_la_grabadora(pantalla())

    assert "Analisis Matematico I" in guion
    assert "inglés" in guion


def test_la_grabadora_sabe_a_que_backend_hablar(pantalla):
    """Es el unico sitio de la app donde el navegador llama a la API."""
    guion = _guion_de_la_grabadora(pantalla())

    assert "/api/jobs/grabacion" in guion
    assert "/parte?n=" in guion
    assert "/cerrar" in guion


def test_un_nombre_de_grupo_no_puede_salirse_del_script(pantalla, monkeypatch):
    """Un `</script>` en el nombre cerraria el bloque y lo de detras se ejecuta.

    El nombre lo escribe la propia persona, asi que es un agujero contra uno
    mismo antes que contra nadie; pero un grupo compartido lo puede escribir
    otro, y no hay ninguna razon para dejarlo abierto.
    """
    from frontend.grabadora import grabadora  # noqa: F401  (solo por claridad)

    import json as _json
    import frontend.grabadora as modulo

    peligroso = [{"id": "g9", "nombre": "</script><script>alert(1)</script>",
                  "materia": "Trampa"}]
    capturado = {}
    monkeypatch.setattr(
        modulo.components, "html",
        lambda html, **kw: capturado.setdefault("html", html),
    )

    modulo.grabadora("http://localhost:8000", "testigo", peligroso, [])

    assert "</script><script>" not in capturado["html"]
    assert "\u003c/script>" in capturado["html"]
    # Y el dato sigue llegando entero: JSON lo devuelve tal cual al leerlo.
    inicio = capturado["html"].index("var CFG = ") + len("var CFG = ")
    fin = capturado["html"].index(";", inicio)
    datos = _json.loads(capturado["html"][inicio:fin])
    assert datos["grupos"][0]["nombre"] == peligroso[0]["nombre"]


# ---------------------------------------------------------------------------
# Una grabacion que se corto, vista desde la app
# ---------------------------------------------------------------------------


@pytest.fixture
def pantalla_con_grabacion(monkeypatch):
    servidor = BackendDeMentira(clases=2, grabacion_sin_cerrar=True)
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


def test_la_lista_distingue_una_grabacion_sin_cerrar(pantalla_con_grabacion):
    """Con «Subiendo» a secas parecería que hay que esperar, y no llega nunca."""
    app = pantalla_con_grabacion()

    pies = " ".join(c.value for c in app.caption)
    assert "Sin cerrar" in pies
    assert "2.4 MB" in pies


def test_se_puede_procesar_lo_que_llego_a_grabarse(pantalla_con_grabacion):
    """Si el teléfono murió en el minuto 200, hay 200 minutos de clase."""
    app = pantalla_con_grabacion(clase_abierta="grab1")

    assert [b for b in app.button if b.key == "cerrar_grab1"]
    assert any("audio válido" in c.value for c in app.caption)


def test_una_grabacion_sin_cerrar_no_repinta_la_pantalla_sin_parar(
    pantalla_con_grabacion,
):
    """En el servidor no avanza nada: la mueve el navegador de quien graba.

    Contándola como «en curso», la pantalla se repintaba cada quince segundos
    para siempre, porque ese estado no se acaba solo.
    """
    app = pantalla_con_grabacion()

    pies = " ".join(c.value for c in app.caption)
    assert "se actualiza sola" not in pies
