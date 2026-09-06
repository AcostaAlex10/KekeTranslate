"""La sesion tiene que sobrevivir a recargar la pagina.

Hasta ahora el testigo vivia solo en `st.session_state`, que Streamlit tira en
cada F5: cerrar sin querer la pestana, o simplemente recargar, obligaba a
escribir la contrasena otra vez. Ahora el testigo se guarda ademas en una
cookie.

Lo que estos tests protegen, por orden de lo dificil que fue verlo:

1. Que la cookie se lea **una sola vez** por sesion. `st.context.cookies` no
   devuelve las cookies de ahora, sino las de la peticion con la que se abrio
   la pestana, y no cambia en toda la sesion. Releyendola en cada pasada,
   pulsar *Salir* no serviria de nada: la cookie recien borrada seguiria ahi y
   volveria a meter a la persona en su cuenta.
2. Que la cookie nunca dure mas que la sesion del servidor que lleva dentro.
3. Que una cookie que ya no vale devuelva a la pantalla de entrar, y no deje la
   app a medias lanzando 401 por todos lados.
"""

from __future__ import annotations

import pytest
import streamlit as st
from streamlit.runtime.context import ContextProxy, StreamlitCookies
from streamlit.testing.v1 import AppTest

from .backend_de_mentira import TESTIGO, BackendDeMentira

APP = "frontend/app.py"
COOKIE = "keke_sesion"
SEGUNDOS_DE_UN_DIA = 86_400


@pytest.fixture
def navegador(monkeypatch):
    """Deja poner las cookies que traeria la peticion inicial del navegador.

    `st.context.cookies` es una propiedad de solo lectura sobre la peticion del
    websocket, que en un test no existe: fuera de `streamlit run` devuelve
    siempre vacio. Se sustituye la propiedad entera, que es la unica forma de
    que el codigo bajo prueba sea el mismo que corre de verdad.
    """

    def con(**galletas: str) -> None:
        monkeypatch.setattr(
            ContextProxy,
            "cookies",
            property(lambda self: StreamlitCookies(dict(galletas))),
        )

    return con


@pytest.fixture
def backend(monkeypatch):
    def levantar(**opciones) -> BackendDeMentira:
        servidor = BackendDeMentira(clases=3, **opciones)
        monkeypatch.setenv("BACKEND_URL", servidor.url)
        servidores.append(servidor)
        return servidor

    servidores: list[BackendDeMentira] = []
    # Las dos caches son globales del servidor de Streamlit, no una por test:
    # la de datos guarda respuestas y la de recursos guarda el cliente HTTP,
    # que apuntaria al backend ya apagado del test anterior.
    st.cache_data.clear()
    st.cache_resource.clear()
    yield levantar
    for servidor in servidores:
        servidor.cerrar()
    st.cache_data.clear()
    st.cache_resource.clear()


def _abrir() -> AppTest:
    app = AppTest.from_file(APP, default_timeout=90)
    app.run()
    assert not app.exception, app.exception
    return app


def _guiones(app: AppTest) -> list[str]:
    """El JavaScript de los componentes de altura cero de la pagina."""
    return [marco.proto.srcdoc for marco in app.get("iframe")]


def _guion_de_la_cookie(app: AppTest) -> str:
    encontrados = [g for g in _guiones(app) if COOKIE in g]
    assert len(encontrados) == 1, f"se esperaba un guion de cookie: {encontrados}"
    return encontrados[0]


def _esta_dentro(app: AppTest) -> bool:
    """Se distingue por el boton de salir, que solo existe con la sesion abierta."""
    return any(b.key == "salir" for b in app.button)


def _pide_entrar(app: AppTest) -> bool:
    """Si se ve el formulario de entrar, se esta fuera.

    Se mira esto y no la ausencia del boton de salir porque tras un `st.rerun()`
    AppTest conserva en el arbol los elementos de la pasada que se abandono: el
    boton viejo sigue apareciendo aunque el navegador ya no lo muestre. La
    presencia del formulario, en cambio, solo puede venir de la pasada nueva.
    """
    return any(b.key.startswith("FormSubmitter:entrar") for b in app.button)


# --- Entrar sin escribir la contrasena --------------------------------------


def test_una_cookie_valida_entra_sin_pedir_la_contrasena(backend, navegador):
    """Es el motivo entero de la funcionalidad: recargar y seguir dentro."""
    backend()
    navegador(**{COOKIE: TESTIGO})

    app = _abrir()

    assert _esta_dentro(app)


def test_sin_cookie_se_pide_entrar(backend, navegador):
    backend()
    navegador()

    app = _abrir()

    assert not _esta_dentro(app)


def test_una_cookie_que_ya_no_vale_devuelve_a_la_pantalla_de_entrar(
    backend, navegador
):
    """El peor caso tiene que ser volver a entrar, nunca quedarse fuera.

    El testigo puede haber caducado o haberse revocado desde otro dispositivo.
    La cookie se cree al arrancar y es `/api/auth/yo` quien lo desmiente.
    """
    backend()
    navegador(**{COOKIE: "un-testigo-que-el-servidor-ya-no-reconoce"})

    app = _abrir()

    assert not _esta_dentro(app)
    assert any("caducado" in w.value for w in app.warning)


@pytest.mark.parametrize(
    "valor",
    [
        "corto",
        "con espacio en medio pero bastante largo",
        "esto; Path=/; Max-Age=99999999; y-cuela-atributos",
    ],
)
def test_una_cookie_con_forma_rara_se_ignora(backend, navegador, valor):
    """Un testigo es `token_urlsafe`: letras, digitos, guion y guion bajo.

    Se comprueba antes de usarlo para que un valor con `;` no pueda inventarse
    atributos de cookie cuando se reescriba.
    """
    backend()
    navegador(**{COOKIE: valor})

    app = _abrir()

    assert not _esta_dentro(app)


# --- Guardar y borrar la cookie ---------------------------------------------


def test_estando_dentro_se_guarda_el_testigo_en_la_cookie(backend, navegador):
    backend()
    navegador(**{COOKIE: TESTIGO})

    guion = _guion_de_la_cookie(_abrir())

    assert f"{COOKIE}={TESTIGO}" in guion
    assert "Path=/" in guion
    assert "SameSite=Lax" in guion


def test_estando_fuera_se_borra_la_cookie(backend, navegador):
    """Caducada o recien cerrada, una cookie que no sirve no se deja puesta."""
    backend()
    navegador(**{COOKIE: "un-testigo-que-el-servidor-ya-no-reconoce"})

    guion = _guion_de_la_cookie(_abrir())

    assert f"{COOKIE}=; Max-Age=0" in guion


def test_secure_lo_decide_el_navegador_y_no_el_servidor(backend, navegador):
    """Puesto siempre, la cookie se perderia en `http://localhost`.

    Y no se puede decidir aqui: el servidor de Streamlit no sabe por que
    esquema le llego la pagina. Lo sabe el navegador, y ahi se mira.
    """
    backend()
    navegador(**{COOKIE: TESTIGO})

    guion = _guion_de_la_cookie(_abrir())

    assert "location.protocol === 'https:'" in guion
    assert "Secure" in guion


# --- Cuanto dura ------------------------------------------------------------


def test_la_cookie_dura_lo_que_dura_la_sesion_en_el_servidor(backend, navegador):
    backend(dias_de_sesion=30)
    navegador(**{COOKIE: TESTIGO})

    guion = _guion_de_la_cookie(_abrir())

    assert f"Max-Age={30 * SEGUNDOS_DE_UN_DIA}" in guion


def test_si_el_servidor_no_lo_dice_la_cookie_dura_poco(backend, navegador):
    """La regla es igual o antes que el testigo, nunca despues.

    Ante la duda lo unico seguro es quedarse corto: lo peor que pasa entonces
    es tener que volver a entrar.
    """
    backend(dias_de_sesion=None)
    navegador(**{COOKIE: TESTIGO})

    guion = _guion_de_la_cookie(_abrir())

    assert f"Max-Age={SEGUNDOS_DE_UN_DIA}" in guion


# --- Salir ------------------------------------------------------------------


def test_salir_no_deja_que_la_cookie_vuelva_a_entrar(backend, navegador):
    """El fallo que este test impide, y que no se ve a simple vista.

    `st.context.cookies` sigue devolviendo la cookie despues de borrarla,
    porque lee la peticion con la que se abrio la pestana y esa ya paso. Si la
    app la releyera en cada pasada, *Salir* dejaria de funcionar: la persona
    volveria a su cuenta sola, sin haber escrito nada.
    """
    backend()
    navegador(**{COOKIE: TESTIGO})
    app = _abrir()
    assert _esta_dentro(app)

    app.button(key="salir").click().run()

    assert not app.exception, app.exception
    assert _pide_entrar(app)
    assert "sesion" not in app.session_state
    assert f"{COOKIE}=; Max-Age=0" in _guion_de_la_cookie(app)
