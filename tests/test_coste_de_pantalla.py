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

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from .backend_de_mentira import TESTIGO, BackendDeMentira

APP = "frontend/app.py"
CLASES = 12


@pytest.fixture
def backend(monkeypatch):
    servidor = BackendDeMentira(clases=CLASES)
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
    app.session_state["sesion"] = TESTIGO
    app.session_state["seccion"] = "clases"
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
    assert backend.peticiones["/api/compartido/{id}/clases/{id}"] == 1
