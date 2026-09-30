"""La interfaz tiene que guardar el vinculo de Google y devolverlo a la vuelta.

El backend ya rechaza una vuelta de Google sin su vinculo (`test_google.py`).
Eso no sirve de nada si la interfaz no lo guarda al pulsar el boton, o no lo
devuelve al volver: entrar con Google dejaria de funcionar para todo el mundo.
Estos tests cubren esa mitad.

Lo que mas facil se rompe sin que se note es el orden: la cookie tiene que
quedar escrita **antes** de que el navegador se vaya a Google. Por eso las dos
cosas van en un mismo guion, y hay un test que lo mira.
"""

from __future__ import annotations

import pytest
import streamlit as st
from streamlit.runtime.context import ContextProxy, StreamlitCookies
from streamlit.testing.v1 import AppTest

from .backend_de_mentira import ESTADO, VINCULO, BackendDeMentira

APP = "frontend/app.py"
COOKIE = "keke_google"


@pytest.fixture
def navegador(monkeypatch):
    """Las cookies que traeria la peticion inicial. Ver `test_cookie_de_sesion`."""

    def con(**galletas: str) -> None:
        monkeypatch.setattr(
            ContextProxy,
            "cookies",
            property(lambda self: StreamlitCookies(dict(galletas))),
        )

    return con


@pytest.fixture
def backend(monkeypatch):
    servidores: list[BackendDeMentira] = []

    def levantar(**opciones) -> BackendDeMentira:
        servidor = BackendDeMentira(clases=1, google_activo=True, **opciones)
        monkeypatch.setenv("BACKEND_URL", servidor.url)
        servidores.append(servidor)
        return servidor

    st.cache_data.clear()
    st.cache_resource.clear()
    yield levantar
    for servidor in servidores:
        servidor.cerrar()
    st.cache_data.clear()
    st.cache_resource.clear()


def _volviendo_de_google() -> AppTest:
    """La pagina tal y como la carga Google al devolver el navegador."""
    app = AppTest.from_file(APP, default_timeout=90)
    app.query_params["code"] = "codigo-que-dio-google"
    app.query_params["state"] = ESTADO
    app.run()
    assert not app.exception, app.exception
    return app


def _pulsar_google() -> AppTest:
    app = AppTest.from_file(APP, default_timeout=90)
    app.run()
    assert not app.exception, app.exception
    [boton] = [b for b in app.button if b.label == "Entrar con Google"]
    boton.click().run()
    assert not app.exception, app.exception
    return app


def _guiones(app: AppTest) -> list[str]:
    return [marco.proto.srcdoc for marco in app.get("iframe")]


# --- A la vuelta -------------------------------------------------------------


def test_la_vuelta_manda_el_vinculo_de_la_cookie(backend, navegador):
    servidor = backend()
    navegador(**{COOKIE: VINCULO})

    app = _volviendo_de_google()

    [enviado] = servidor.recibidos["/api/auth/google"]
    assert enviado["state"] == ESTADO
    assert enviado["vinculo"] == VINCULO
    assert any(b.key == "salir" for b in app.button), "deberia haber entrado"


def test_sin_la_cookie_se_manda_vacio_y_se_explica_el_rechazo(backend, navegador):
    """Es lo que ve quien abre un enlace fabricado por otro.

    La interfaz no decide nada: manda lo que hay y es el backend quien
    rechaza. Lo que se comprueba es que la persona se quede en la pantalla de
    entrar con el motivo a la vista, y no en una app a medias.
    """
    servidor = backend(google_rechaza_la_vuelta=True)
    navegador()

    app = _volviendo_de_google()

    assert servidor.recibidos["/api/auth/google"][0]["vinculo"] == ""
    assert any("no vale" in e.value for e in app.error)
    assert any(b.key.startswith("FormSubmitter:entrar") for b in app.button)


# --- Al pulsar el boton ------------------------------------------------------


def test_el_boton_guarda_el_vinculo_antes_de_irse_a_google(backend, navegador):
    backend()
    navegador()

    app = _pulsar_google()

    [guion] = [g for g in _guiones(app) if COOKIE in g]
    assert f"{COOKIE}={VINCULO}; Max-Age=600; Path=/; SameSite=Lax" in guion
    assert "accounts.google.com" in guion
    # En otro orden, la pagina se iria a Google sin haber guardado nada.
    assert guion.index("document.cookie") < guion.index(".click()")


def test_la_navegacion_no_sale_del_iframe_del_componente(backend, navegador):
    """El iframe de Streamlit no tiene `allow-top-navigation`: si el guion
    cambia `location` desde ahi, Chrome lo bloquea y el boton no hace nada.
    Se navega con un enlace del documento principal. Lo que de verdad lo
    demuestra es una prueba en navegador; esto impide volver al patron roto."""
    backend()
    navegador()

    [guion] = [g for g in _guiones(_pulsar_google()) if COOKIE in g]

    assert "location.href" not in guion
    assert "window.parent.document.createElement('a')" in guion


def test_sin_vinculo_en_la_respuesta_no_se_manda_a_google(backend, navegador):
    """Un backend anterior al vinculo no lo entrega. Ir a Google igualmente
    llevaria a un rechazo seguro a la vuelta: mejor decirlo aqui."""
    backend(inicio_sin_vinculo=True)
    navegador()

    app = _pulsar_google()

    assert not any("accounts.google.com" in g for g in _guiones(app))
    assert any("Google" in e.value for e in app.error)
