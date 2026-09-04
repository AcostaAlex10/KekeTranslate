"""Lo que cuesta dibujar una pantalla, medido en peticiones al backend.

Es un test de rendimiento, no de aspecto, y por eso cuenta peticiones en vez de
mirar pixeles: la ficha completa de una clase trae su transcripcion y sus
apuntes enteros —entre 180 y 270 KB por cada hora de grabacion, medido sobre la
base real—, asi que pedir una de mas no es un detalle.

El fallo que este fichero impide volver a introducir: cada clase era un
`st.expander`, y Streamlit ejecuta el cuerpo de un desplegable este abierto o
cerrado. Abrir "Mis clases" con treinta clases pedia las treinta fichas
completas para leer una sola.
"""

from __future__ import annotations

import json
import threading
from collections import Counter
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

APP = "frontend/app.py"
CLASES = 12

RUTAS_CONOCIDAS = {
    "api", "jobs", "grupos", "temas", "materiales", "notas", "health",
    "notes", "transcript", "compartido", "ubicacion", "titulo",
}


def _normalizar(ruta: str) -> str:
    """Convierte `/api/jobs/abc123` en `/api/jobs/{id}` para poder contarla."""
    partes = ruta.strip("/").split("/")
    return "/" + "/".join(p if p in RUTAS_CONOCIDAS else "{id}" for p in partes)


class _Backend:
    """Backend de mentira que lleva la cuenta de lo que se le pide."""

    def __init__(self) -> None:
        self.peticiones: Counter = Counter()
        ahora = datetime.now(timezone.utc)

        self.grupos = [
            {
                "id": "g1",
                "nombre": "Comision 3",
                "materia": "Analisis Matematico I",
                "created_at": ahora.isoformat(),
                "updated_at": ahora.isoformat(),
                "share_token": None,
                "share_permiso": "lectura",
            }
        ]
        self.trabajos = [
            {
                "id": f"j{i}",
                "filename": "clase_larga.wav",
                "titulo": None,
                "status": "completed",
                "created_at": (ahora - timedelta(days=i)).isoformat(),
                "audio_duration_seconds": 5400.0,
                "error": None,
                "grupo_id": "g1" if i % 2 else None,
                "tema_id": None,
            }
            for i in range(CLASES)
        ]

        contador = self.peticiones
        cuerpo = self._cuerpo

        class Manejador(BaseHTTPRequestHandler):
            def log_message(self, *_args):  # sin ruido en la salida del test
                pass

            def do_GET(self):  # noqa: N802 - lo impone BaseHTTPRequestHandler
                ruta = urlparse(self.path).path
                contador[_normalizar(ruta)] += 1
                datos = json.dumps(cuerpo(ruta)).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(datos)))
                self.end_headers()
                self.wfile.write(datos)

        self._servidor = ThreadingHTTPServer(("127.0.0.1", 0), Manejador)
        threading.Thread(target=self._servidor.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self._servidor.server_port}"

    def _cuerpo(self, ruta: str):
        if ruta == "/api/health":
            return {
                "status": "ok",
                "transcription_provider": "assemblyai",
                "transcription_key_configured": True,
                "annotator_provider": "gemini",
                "annotator_model": "gemini-flash-latest",
                "annotator_key_configured": True,
                "diarization_enabled": True,
                "max_upload_bytes": 5_000_000_000,
            }
        if ruta == "/api/jobs":
            return self.trabajos
        if ruta == "/api/grupos":
            return self.grupos
        if ruta.startswith("/api/compartido/"):
            return {**self.grupos[0], "share_permiso": "lectura"}
        if ruta.startswith("/api/jobs/"):
            base = next(
                t for t in self.trabajos if t["id"] == ruta.rsplit("/", 1)[-1]
            )
            return {
                **base,
                "speakers": ["Orador A"],
                "transcript_diarized": "[00:00:00] Orador A: " + "palabra " * 50,
                "notes_markdown": "# Apuntes\n\n" + "Texto. " * 50,
                "notes_editadas": None,
            }
        return []

    def cerrar(self) -> None:
        self._servidor.shutdown()


@pytest.fixture
def backend(monkeypatch):
    servidor = _Backend()
    monkeypatch.setenv("BACKEND_URL", servidor.url)
    # Hay que vaciar las dos caches. La de datos guarda las respuestas; la de
    # recursos guarda el cliente HTTP, que apunta al servidor del test
    # anterior: sin esto el segundo test se queda esperando a un puerto muerto.
    st.cache_data.clear()
    st.cache_resource.clear()
    yield servidor
    servidor.cerrar()
    st.cache_data.clear()
    st.cache_resource.clear()


def _abrir(backend, **estado):
    app = AppTest.from_file(APP, default_timeout=90)
    app.session_state["seccion"] = "📚 Mis clases"
    for clave, valor in estado.items():
        app.session_state[clave] = valor
    app.run()
    assert not app.exception, app.exception
    return app


def test_el_indice_no_pide_la_ficha_de_ninguna_clase(backend):
    """El listado ya trae nombre, fecha, estado y duracion: basta con el."""
    _abrir(backend)

    assert backend.peticiones["/api/jobs/{id}"] == 0
    assert backend.peticiones["/api/jobs"] == 1


def test_abrir_una_clase_pide_una_sola_ficha(backend):
    _abrir(backend, clase_abierta="j3")

    assert backend.peticiones["/api/jobs/{id}"] == 1


def test_el_indice_lista_todas_las_clases(backend):
    """Ninguna clase puede quedar fuera del indice sin que se note."""
    app = _abrir(backend)

    filas = [b for b in app.button if b.key and b.key.startswith("abrir_")]
    assert len(filas) == CLASES


def test_el_indice_agrupa_por_materia(backend):
    """Es la unica pista que distingue grabaciones con el mismo nombre."""
    app = _abrir(backend)

    encabezados = " ".join(m.value for m in app.markdown)
    assert "Analisis Matematico I" in encabezados
    assert "Sin archivar" in encabezados


def test_la_ficha_deja_ponerle_nombre_a_la_clase(backend):
    app = _abrir(backend, clase_abierta="j3")

    assert [c for c in app.text_input if c.key == "titulo_j3"]


def test_una_clase_borrada_devuelve_al_indice_sin_romper(backend):
    """La clase abierta pudo borrarse desde otro sitio."""
    app = _abrir(backend, clase_abierta="ya-no-existe")

    assert not app.exception
    assert [b for b in app.button if b.key and b.key.startswith("abrir_")]


def test_un_grupo_compartido_no_descarga_todos_sus_apuntes(backend):
    """Es la primera pantalla de alguien que llega por un enlace.

    Con quince clases archivadas, abrir el enlace se traia los quince juegos de
    apuntes enteros antes de que el visitante tocara nada.
    """
    app = AppTest.from_file(APP, default_timeout=90)
    app.query_params["grupo"] = "un-token"
    app.run()

    assert not app.exception, app.exception
    assert backend.peticiones["/api/jobs/{id}"] == 1
