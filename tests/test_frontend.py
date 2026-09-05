"""Comprobaciones basicas de la interfaz de Streamlit.

No sustituyen a probarla a mano, pero cazan lo que mas duele: que la pagina
reviente al abrirse. La interfaz se ejecuta con `AppTest`, el arnes propio de
Streamlit, sin navegador ni backend.
"""

from __future__ import annotations

import pytest

import streamlit as st
from streamlit.testing.v1 import AppTest

from .backend_de_mentira import TESTIGO, BackendDeMentira

APP = "frontend/app.py"


def radio_de(app, clave: str):
    """Localiza un radio por su clave, no por su posicion.

    Antes se usaba `app.radio[0]`, que apuntaba al primero que apareciera. Al
    mover la navegacion al cuerpo de la pagina ese indice cambio de significado
    sin avisar y los tests empezaron a comprobar otra cosa.
    """
    for radio in app.radio:
        if radio.key == clave:
            return radio
    raise AssertionError(f"no se encontro el radio '{clave}'")


def radio_de_opcional(app, clave: str):
    """Como `radio_de`, pero devuelve `None` en vez de fallar."""
    return next((r for r in app.radio if r.key == clave), None)


@pytest.fixture
def sin_backend(monkeypatch):
    """La interfaz apuntando a un backend que no existe.

    Si el backend esta caido la pagina tiene que seguir cargando y avisar, no
    romperse: es justo el estado en el que se la encuentra quien acaba de
    clonar el repo.
    """
    monkeypatch.setenv("BACKEND_URL", "http://127.0.0.1:9")  # puerto muerto
    st.cache_data.clear()
    st.cache_resource.clear()
    yield AppTest.from_file(APP, default_timeout=60).run()
    st.cache_data.clear()
    st.cache_resource.clear()


@pytest.fixture
def app(monkeypatch):
    """La interfaz ya dentro de una cuenta, contra un backend de mentira."""
    servidor = BackendDeMentira()
    monkeypatch.setenv("BACKEND_URL", servidor.url)
    # Las dos caches son globales del proceso: la de datos guarda respuestas y
    # la de recursos guarda el cliente HTTP, que apuntaria al servidor del test
    # anterior.
    st.cache_data.clear()
    st.cache_resource.clear()

    prueba = AppTest.from_file(APP, default_timeout=60)
    prueba.session_state["sesion"] = TESTIGO
    yield prueba.run()

    servidor.cerrar()
    st.cache_data.clear()
    st.cache_resource.clear()


def test_la_pagina_carga_sin_backend(sin_backend):
    """Sin servidor hay que avisar en el cuerpo, no romperse ni callar.

    El aviso no puede vivir solo en la barra lateral: en el movil arranca
    colapsada y el usuario se quedaria delante de una pantalla muerta.
    """
    assert not sin_backend.exception
    assert any("servidor" in error.value.lower() for error in sin_backend.error)


def test_sin_cuenta_no_se_llega_a_la_app(sin_backend):
    """La puerta de la interfaz. La de verdad esta en el backend.

    Con el servidor caido ni siquiera se ofrece el formulario, porque no habria
    con quien comprobar la contrasena: se explica el problema y se para.
    """
    assert not radio_de_opcional(sin_backend, "seccion")
    assert not sin_backend.get("file_uploader")


def test_sin_cuenta_pero_con_servidor_se_ofrece_entrar_o_registrarse(monkeypatch):
    servidor = BackendDeMentira()
    monkeypatch.setenv("BACKEND_URL", servidor.url)
    st.cache_data.clear()
    st.cache_resource.clear()

    prueba = AppTest.from_file(APP, default_timeout=60).run()

    assert not radio_de_opcional(prueba, "seccion")
    opciones = radio_de(prueba, "modo_de_entrada").options
    assert any("Entrar" in o for o in opciones)
    assert any("Crear" in o for o in opciones)
    servidor.cerrar()
    st.cache_data.clear()
    st.cache_resource.clear()


def test_con_cuenta_se_ve_la_app(app):
    assert radio_de(app, "seccion")
    assert not radio_de_opcional(app, "modo_de_entrada")


def test_se_puede_salir(app):
    assert [b for b in app.sidebar.button if b.key == "salir"]


def test_la_navegacion_esta_en_el_cuerpo(app):
    """En el movil la barra lateral se colapsa y se llevaria la navegacion."""
    navegacion = radio_de(app, "seccion")

    assert [o for o in navegacion.options if "Mis clases" in o]
    assert [o for o in navegacion.options if "Grupos" in o]


def test_ofrece_grabar_y_subir(app):
    """Las dos vias de carga tienen que estar disponibles."""
    opciones = radio_de(app, "modo_de_carga").options
    assert any("Grabar" in opcion for opcion in opciones)
    assert any("Subir" in opcion for opcion in opciones)


def test_el_modo_por_defecto_es_grabar(app):
    """Grabar es el caso principal: dejar el movil en el aula y olvidarse."""
    assert radio_de(app, "modo_de_carga").value == "grabar"
    assert app.get("audio_input"), "falta el widget de grabacion"


def test_al_elegir_fichero_aparece_el_selector(app):
    resultado = radio_de(app, "modo_de_carga").set_value("subir").run()

    assert not resultado.exception
    assert resultado.get("file_uploader"), "falta el selector de ficheros"
    assert not resultado.get("audio_input")
