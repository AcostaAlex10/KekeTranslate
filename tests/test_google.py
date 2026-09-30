"""Entrar con Google, con las credenciales puestas.

Hasta ahora solo se probaba el caso apagado. El flujo encendido no tenia ni un
test, y tenia un agujero: el `state` garantizaba que el flujo empezo en este
servidor, pero no **que navegador** lo empezo. Alguien podia pulsar el boton el
mismo, entrar en Google con su cuenta, quedarse con la URL de vuelta sin
abrirla y mandarsela a otra persona. Al abrirla, esa persona entraba en la
cuenta del atacante sin darse cuenta, y las clases que subiera despues las
leia el.

Ahora cada `state` lleva un vinculo que se queda el navegador que pulso el
boton, en una cookie, y que nunca viaja en una URL. Estos tests fijan esa
regla, y de paso el resto del flujo, que nunca se habia comprobado.

Google no se llama de verdad: se sustituye el canje del codigo. Eso modela bien
el ataque, porque en la realidad el codigo del atacante **es** valido —Google
no sabe nada de nuestro `state`— y lo unico que puede frenarlo es lo nuestro.
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

import backend.usuarios as usuarios_mod
from backend.usuarios import Usuarios

VUELTA = "http://localhost:8501"

# Que cuenta de Google corresponde a cada codigo. En la realidad lo decide
# Google segun quien eligio cuenta en su pantalla.
CUENTAS = {
    "codigo-de-ana": {"sub": "google-ana", "email": "ana@unam.edu.ar", "nombre": "Ana"},
    "codigo-del-atacante": {
        "sub": "google-atacante",
        "email": "atacante@ejemplo.com",
        "nombre": "Atacante",
    },
}


@pytest.fixture
def canjes():
    """Los codigos que se llegaron a canjear con Google, en orden."""
    return []


@pytest.fixture
def client(tmp_path, monkeypatch, canjes):
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path))
    monkeypatch.setenv("GEMINI_API_KEY", "clave-de-prueba-con-largo-realista")
    monkeypatch.setenv("ASSEMBLYAI_API_KEY", "clave-de-prueba-con-largo-realista")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cliente.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secreto-de-prueba")

    from backend.config import get_settings

    get_settings.cache_clear()

    import backend.main as main

    async def canjear(code, redirect_uri, settings):
        canjes.append(code)
        return CUENTAS[code]

    monkeypatch.setattr(main, "_canjear_codigo_de_google", canjear)
    monkeypatch.setattr(main, "_store", None)
    monkeypatch.setattr(main, "_biblioteca", None)
    monkeypatch.setattr(main, "_usuarios", None)

    with TestClient(main.app) as test_client:
        yield test_client

    get_settings.cache_clear()


def _empezar(client) -> dict:
    """Lo que pasa al pulsar el boton: el backend entrega state y vinculo."""
    respuesta = client.post("/api/auth/google/inicio", json={"redirect_uri": VUELTA})
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


def _volver(client, code: str, state: str, vinculo: str):
    """Lo que pasa cuando Google devuelve el navegador a la app."""
    return client.post(
        "/api/auth/google",
        json={"code": code, "redirect_uri": VUELTA, "state": state, "vinculo": vinculo},
    )


def _quien(client, token: str) -> dict:
    respuesta = client.get("/api/auth/yo", headers={"Authorization": f"Bearer {token}"})
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


# --- El camino normal --------------------------------------------------------


def test_entrar_con_google_de_punta_a_punta(client):
    inicio = _empezar(client)

    respuesta = _volver(client, "codigo-de-ana", inicio["state"], inicio["vinculo"])

    assert respuesta.status_code == 200, respuesta.text
    assert _quien(client, respuesta.json()["token"])["email"] == "ana@unam.edu.ar"


def test_el_vinculo_no_viaja_en_la_url_de_google(client):
    """Es lo que lo hace util: el `state` lo tiene cualquiera con el enlace."""
    inicio = _empezar(client)

    assert inicio["state"] in inicio["url"]
    assert inicio["vinculo"] not in inicio["url"]


def test_el_inicio_dice_cuanto_vive_el_vinculo(client):
    """Para que la cookie no sobreviva al estado que acompana."""
    assert _empezar(client)["segundos"] == usuarios_mod.MINUTOS_DE_ESTADO * 60


def test_mismo_correo_entra_en_la_cuenta_que_ya_existia(client):
    """Quien ya tenia cuenta con contrasena no acaba con dos cuentas."""
    alta = client.post(
        "/api/auth/registro",
        json={"email": "ana@unam.edu.ar", "password": "una-frase-larga"},
    ).json()

    inicio = _empezar(client)
    token = _volver(client, "codigo-de-ana", inicio["state"], inicio["vinculo"]).json()[
        "token"
    ]

    assert _quien(client, token)["id"] == alta["usuario"]["id"]
    assert _quien(client, token)["tiene_google"] is True


# --- El ataque ---------------------------------------------------------------


def test_un_enlace_fabricado_por_otro_no_mete_en_su_cuenta(client, canjes):
    """El caso entero por el que existe el vinculo.

    El atacante empieza el flujo y entra en Google con su cuenta. La URL de
    vuelta lleva su codigo y un `state` valido. Quien abre ese enlace no tiene
    la cookie, asi que su navegador vuelve sin vinculo.
    """
    del_atacante = _empezar(client)

    respuesta = _volver(client, "codigo-del-atacante", del_atacante["state"], vinculo="")

    assert respuesta.status_code == 400
    assert "token" not in respuesta.json()
    assert "no empezó en este navegador" in respuesta.json()["detail"]
    # Se rechaza antes de hablar con Google: ni se llega a saber de quien es.
    assert canjes == []


def test_el_vinculo_de_otro_intento_tampoco_sirve(client):
    """La victima pudo haber pulsado el boton antes, y tener su propia cookie.

    Lleva un vinculo, pero es el de su intento, no el del enlace que abrio.
    """
    de_la_victima = _empezar(client)
    del_atacante = _empezar(client)

    respuesta = _volver(
        client, "codigo-del-atacante", del_atacante["state"], de_la_victima["vinculo"]
    )

    assert respuesta.status_code == 400


def test_un_intento_fallido_gasta_el_estado(client):
    """Un solo uso es un solo intento, no un solo acierto.

    Si el estado sobreviviera al fallo, quien fabrico el enlace podria seguir
    probandolo hasta que caduque.
    """
    inicio = _empezar(client)
    assert _volver(client, "codigo-de-ana", inicio["state"], "otro-vinculo").status_code == 400

    reintento = _volver(client, "codigo-de-ana", inicio["state"], inicio["vinculo"])

    assert reintento.status_code == 400


def test_el_estado_es_de_un_solo_uso(client):
    inicio = _empezar(client)
    assert _volver(client, "codigo-de-ana", inicio["state"], inicio["vinculo"]).status_code == 200

    repetido = _volver(client, "codigo-de-ana", inicio["state"], inicio["vinculo"])

    assert repetido.status_code == 400


def test_un_estado_caducado_no_vale(client, monkeypatch):
    inicio = _empezar(client)
    despues = usuarios_mod._ahora() + timedelta(minutes=usuarios_mod.MINUTOS_DE_ESTADO + 1)
    monkeypatch.setattr(usuarios_mod, "_ahora", lambda: despues)

    respuesta = _volver(client, "codigo-de-ana", inicio["state"], inicio["vinculo"])

    assert respuesta.status_code == 400


def test_un_vinculo_con_caracteres_raros_se_rechaza_sin_reventar(client):
    """El vinculo lo manda el cliente. `compare_digest` con texto no ASCII
    lanza TypeError; sin cuidado, eso era un 500 a voluntad."""
    inicio = _empezar(client)

    respuesta = _volver(client, "codigo-de-ana", inicio["state"], "ñandú-€")

    assert respuesta.status_code == 400


# --- Bases anteriores al vinculo --------------------------------------------


def test_una_base_de_antes_del_vinculo_se_pone_al_dia(tmp_path):
    """La tabla ya existia sin la columna, y `CREATE TABLE IF NOT EXISTS` no
    la toca. Los estados viejos no se sabe que navegador los pidio: no valen."""
    ruta = tmp_path / "vieja.db"
    with sqlite3.connect(ruta) as conn:
        conn.execute(
            "CREATE TABLE estados_oauth (estado TEXT PRIMARY KEY, created_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO estados_oauth VALUES (?, ?)",
            ("estado-viejo", usuarios_mod._ahora().isoformat()),
        )

    usuarios = Usuarios(ruta)

    assert usuarios.consumir_estado("estado-viejo", "") is False
    estado, vinculo = usuarios.nuevo_estado()
    assert usuarios.consumir_estado(estado, vinculo) is True
