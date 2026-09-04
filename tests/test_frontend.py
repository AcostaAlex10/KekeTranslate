"""Comprobaciones basicas de la interfaz de Streamlit.

No sustituyen a probarla a mano, pero cazan lo que mas duele: que la pagina
reviente al abrirse. La interfaz se ejecuta con `AppTest`, el arnes propio de
Streamlit, sin navegador ni backend.
"""

from __future__ import annotations

import pytest

from streamlit.testing.v1 import AppTest

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


@pytest.fixture
def app(monkeypatch):
    """Arranca la interfaz apuntando a un backend que no existe.

    Si el backend esta caido la pagina tiene que seguir cargando y avisar, no
    romperse: es justo el estado en el que se la encuentra quien acaba de
    clonar el repo.
    """
    monkeypatch.setenv("BACKEND_URL", "http://127.0.0.1:9")  # puerto muerto
    return AppTest.from_file(APP, default_timeout=60).run()


def test_la_pagina_carga_sin_backend(app):
    """Sin servidor hay que avisar en el cuerpo, no romperse ni callar.

    El aviso no puede vivir solo en la barra lateral: en el movil arranca
    colapsada y el usuario se quedaria delante de una pantalla muerta.
    """
    assert not app.exception
    assert any("servidor" in error.value.lower() for error in app.error)


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
    assert "Grabar" in radio_de(app, "modo_de_carga").value
    assert app.get("audio_input"), "falta el widget de grabacion"


def test_al_elegir_fichero_aparece_el_selector(app):
    resultado = radio_de(app, "modo_de_carga").set_value("📁 Subir un fichero").run()

    assert not resultado.exception
    assert resultado.get("file_uploader"), "falta el selector de ficheros"
    assert not resultado.get("audio_input")
