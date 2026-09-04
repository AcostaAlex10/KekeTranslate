"""Cuentas, sesiones y —lo que de verdad importa— aislamiento entre personas.

Los tests de mas abajo no comprueban que el login funcione: comprueban que los
apuntes de una persona no se puedan leer desde la cuenta de otra. Es la unica
restriccion que `PRODUCT.md` marca como innegociable, y hasta que hubo cuentas
no se cumplia: quien alcanzara el backend podia pedir cualquier clase.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import backend.pipeline as pipeline
from backend.models import TranscriptionResult, Utterance


class _Proveedor:
    name = "fake"
    supports_diarization = True

    async def transcribe(self, path, **kwargs) -> TranscriptionResult:
        return TranscriptionResult(
            provider="fake",
            text="Hola clase.",
            utterances=[
                Utterance(speaker="Orador A", start_ms=0, end_ms=1000, text="Hola.")
            ],
            audio_duration_seconds=60.0,
        )


class _Anotador:
    def __init__(self, settings):
        pass

    async def annotate(self, transcription, *, filename, contexto=None):
        return "# Apuntes"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path))
    monkeypatch.setenv("GEMINI_API_KEY", "clave-de-prueba-con-largo-realista")
    monkeypatch.setenv("ASSEMBLYAI_API_KEY", "clave-de-prueba-con-largo-realista")

    from backend.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(pipeline, "get_provider", lambda settings: _Proveedor())
    monkeypatch.setattr(pipeline, "get_annotator", lambda s: _Anotador())

    import backend.main as main

    monkeypatch.setattr(main, "_store", None)
    monkeypatch.setattr(main, "_biblioteca", None)
    monkeypatch.setattr(main, "_usuarios", None)

    with TestClient(main.app) as test_client:
        yield test_client

    get_settings.cache_clear()


def _registrar(client, email, password="una-frase-larga"):
    respuesta = client.post(
        "/api/auth/registro", json={"email": email, "password": password}
    )
    assert respuesta.status_code == 201, respuesta.text
    return respuesta.json()["token"]


def _cabecera(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _audio():
    return {"file": ("clase.mp3", b"\x00" * 4096, "audio/mpeg")}


# ---------------------------------------------------------------------------
# La puerta
# ---------------------------------------------------------------------------


def test_sin_sesion_la_api_no_contesta_nada(client):
    """Negacion por defecto: lo que no esta abierto a proposito, esta cerrado."""
    cerradas = [
        ("get", "/api/jobs"),
        ("get", "/api/grupos"),
        ("post", "/api/grupos"),
        ("get", "/api/jobs/lo-que-sea"),
        ("get", "/api/grupos/lo-que-sea/notas"),
        ("delete", "/api/jobs/lo-que-sea"),
    ]
    for metodo, ruta in cerradas:
        respuesta = getattr(client, metodo)(ruta)
        assert respuesta.status_code == 401, f"{metodo.upper()} {ruta}"


def test_lo_que_si_esta_abierto_sigue_estandolo(client):
    """Sin esto, la pantalla de entrar no podria ni comprobar el servidor."""
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/auth/google").status_code == 200


def test_un_testigo_inventado_no_vale(client):
    respuesta = client.get("/api/jobs", headers=_cabecera("me-lo-invento"))

    assert respuesta.status_code == 401


def test_al_salir_el_testigo_deja_de_valer(client):
    """Una sesion revocable es la razon de no usar un JWT firmado."""
    token = _registrar(client, "alguien@unam.edu.ar")
    assert client.get("/api/jobs", headers=_cabecera(token)).status_code == 200

    client.post("/api/auth/salir", headers=_cabecera(token))

    assert client.get("/api/jobs", headers=_cabecera(token)).status_code == 401


# ---------------------------------------------------------------------------
# Aislamiento
# ---------------------------------------------------------------------------


def test_una_persona_no_ve_las_clases_de_otra(client):
    ana = _registrar(client, "ana@unam.edu.ar")
    beto = _registrar(client, "beto@unam.edu.ar")
    clase = client.post("/api/jobs", files=_audio(), headers=_cabecera(ana)).json()

    assert client.get("/api/jobs", headers=_cabecera(beto)).json() == []
    # 404 y no 403: un 403 confirmaria que ese identificador existe.
    respuesta = client.get(f"/api/jobs/{clase['id']}", headers=_cabecera(beto))
    assert respuesta.status_code == 404


def test_una_persona_no_puede_borrar_la_clase_de_otra(client):
    ana = _registrar(client, "ana@unam.edu.ar")
    beto = _registrar(client, "beto@unam.edu.ar")
    clase = client.post("/api/jobs", files=_audio(), headers=_cabecera(ana)).json()

    assert client.delete(
        f"/api/jobs/{clase['id']}", headers=_cabecera(beto)
    ).status_code == 404
    assert client.get(f"/api/jobs/{clase['id']}", headers=_cabecera(ana)).status_code == 200


def test_una_persona_no_ve_ni_toca_los_grupos_de_otra(client):
    ana = _registrar(client, "ana@unam.edu.ar")
    beto = _registrar(client, "beto@unam.edu.ar")
    grupo = client.post(
        "/api/grupos",
        json={"nombre": "Comision 3", "materia": "Analisis"},
        headers=_cabecera(ana),
    ).json()

    assert client.get("/api/grupos", headers=_cabecera(beto)).json() == []
    for metodo, ruta in [
        ("get", f"/api/grupos/{grupo['id']}"),
        ("get", f"/api/grupos/{grupo['id']}/temas"),
        ("get", f"/api/grupos/{grupo['id']}/notas"),
        ("get", f"/api/grupos/{grupo['id']}/materiales"),
        ("delete", f"/api/grupos/{grupo['id']}"),
    ]:
        respuesta = getattr(client, metodo)(ruta, headers=_cabecera(beto))
        assert respuesta.status_code == 404, ruta


def test_no_se_puede_archivar_una_clase_en_el_grupo_de_otra_persona(client):
    """El agujero menos evidente: colar contenido propio en la carpeta ajena."""
    ana = _registrar(client, "ana@unam.edu.ar")
    beto = _registrar(client, "beto@unam.edu.ar")
    grupo = client.post(
        "/api/grupos",
        json={"nombre": "Comision 3", "materia": "Analisis"},
        headers=_cabecera(ana),
    ).json()
    clase = client.post("/api/jobs", files=_audio(), headers=_cabecera(beto)).json()

    respuesta = client.patch(
        f"/api/jobs/{clase['id']}/ubicacion",
        params={"grupo_id": grupo["id"]},
        headers=_cabecera(beto),
    )

    assert respuesta.status_code == 404


def test_no_se_puede_subir_una_clase_al_grupo_de_otra_persona(client):
    ana = _registrar(client, "ana@unam.edu.ar")
    beto = _registrar(client, "beto@unam.edu.ar")
    grupo = client.post(
        "/api/grupos",
        json={"nombre": "Comision 3", "materia": "Analisis"},
        headers=_cabecera(ana),
    ).json()

    respuesta = client.post(
        "/api/jobs",
        files=_audio(),
        params={"grupo_id": grupo["id"]},
        headers=_cabecera(beto),
    )

    assert respuesta.status_code == 404


# ---------------------------------------------------------------------------
# El enlace compartido
# ---------------------------------------------------------------------------


def _grupo_compartido(client, token_sesion, permiso="lectura"):
    grupo = client.post(
        "/api/grupos",
        json={"nombre": "Comision 3", "materia": "Analisis"},
        headers=_cabecera(token_sesion),
    ).json()
    compartido = client.post(
        f"/api/grupos/{grupo['id']}/compartir",
        params={"permiso": permiso},
        headers=_cabecera(token_sesion),
    ).json()
    return compartido


def test_el_enlace_abre_el_grupo_sin_cuenta(client):
    """Pedirle a un companero que se registre para leer unos apuntes no cuela."""
    ana = _registrar(client, "ana@unam.edu.ar")
    grupo = _grupo_compartido(client, ana)

    respuesta = client.get(f"/api/compartido/{grupo['share_token']}")

    assert respuesta.status_code == 200
    assert respuesta.json()["nombre"] == "Comision 3"


def test_el_enlace_no_dice_de_quien_es_el_grupo(client):
    ana = _registrar(client, "ana@unam.edu.ar")
    grupo = _grupo_compartido(client, ana)

    visto = client.get(f"/api/compartido/{grupo['share_token']}").json()

    assert visto["usuario_id"] is None


def test_el_enlace_solo_abre_su_grupo(client):
    """Un enlace no es una llave maestra: abre uno y nada mas."""
    ana = _registrar(client, "ana@unam.edu.ar")
    compartido = _grupo_compartido(client, ana)
    privado = client.post(
        "/api/grupos",
        json={"nombre": "Privado", "materia": "Algebra"},
        headers=_cabecera(ana),
    ).json()
    token = compartido["share_token"]

    # Con el enlace no se llega al grupo privado ni por la puerta de siempre...
    assert client.get(f"/api/grupos/{privado['id']}").status_code == 401
    # ...ni por la del enlace.
    assert client.get(f"/api/compartido/{token}/notas").status_code == 200
    notas_ajenas = client.post(
        f"/api/compartido/{token}/notas", json={"titulo": "x"}
    )
    assert notas_ajenas.status_code == 403  # este enlace es de solo lectura


def test_un_enlace_de_lectura_no_deja_escribir(client):
    ana = _registrar(client, "ana@unam.edu.ar")
    grupo = _grupo_compartido(client, ana, permiso="lectura")

    respuesta = client.post(
        f"/api/compartido/{grupo['share_token']}/notas",
        json={"titulo": "Mia", "contenido": "..."},
    )

    assert respuesta.status_code == 403


def test_un_enlace_de_escritura_deja_escribir_solo_en_su_grupo(client):
    ana = _registrar(client, "ana@unam.edu.ar")
    compartido = _grupo_compartido(client, ana, permiso="escritura")
    otro = client.post(
        "/api/grupos",
        json={"nombre": "Otro", "materia": "Algebra"},
        headers=_cabecera(ana),
    ).json()
    nota_ajena = client.post(
        f"/api/grupos/{otro['id']}/notas",
        json={"titulo": "De otro grupo"},
        headers=_cabecera(ana),
    ).json()
    token = compartido["share_token"]

    assert client.post(
        f"/api/compartido/{token}/notas", json={"titulo": "Mia"}
    ).status_code == 201
    # La nota existe, pero es de otro grupo: el enlace no llega hasta ella.
    assert client.put(
        f"/api/compartido/{token}/notas/{nota_ajena['id']}",
        json={"contenido": "pisado"},
    ).status_code == 404


def test_un_enlace_revocado_deja_de_abrir_nada(client):
    ana = _registrar(client, "ana@unam.edu.ar")
    grupo = _grupo_compartido(client, ana)
    token = grupo["share_token"]

    client.delete(f"/api/grupos/{grupo['id']}/compartir", headers=_cabecera(ana))

    assert client.get(f"/api/compartido/{token}").status_code == 404
    assert client.get(f"/api/compartido/{token}/temas").status_code == 404
    assert client.get(f"/api/compartido/{token}/clases").status_code == 404


# ---------------------------------------------------------------------------
# Alta y contrasenas
# ---------------------------------------------------------------------------


def test_la_primera_cuenta_adopta_lo_que_ya_existia(client):
    """Una clase transcrita cuesta dinero: no puede quedar huerfana."""
    import backend.main as main
    from backend.models import Job, JobStatus

    main.get_store().create(
        Job(id="vieja", filename="clase_larga.wav", status=JobStatus.COMPLETED)
    )
    main.get_biblioteca().crear_grupo("Comision 3", "Analisis")

    primera = _registrar(client, "ana@unam.edu.ar")

    assert [c["id"] for c in client.get("/api/jobs", headers=_cabecera(primera)).json()] == [
        "vieja"
    ]
    assert len(client.get("/api/grupos", headers=_cabecera(primera)).json()) == 1


def test_la_segunda_cuenta_no_hereda_nada(client):
    import backend.main as main
    from backend.models import Job, JobStatus

    main.get_store().create(
        Job(id="vieja", filename="clase_larga.wav", status=JobStatus.COMPLETED)
    )
    _registrar(client, "ana@unam.edu.ar")

    segunda = _registrar(client, "beto@unam.edu.ar")

    assert client.get("/api/jobs", headers=_cabecera(segunda)).json() == []


def test_no_se_puede_repetir_el_correo(client):
    _registrar(client, "ana@unam.edu.ar")

    respuesta = client.post(
        "/api/auth/registro",
        json={"email": "ANA@unam.edu.ar", "password": "otra-frase-larga"},
    )

    assert respuesta.status_code == 422
    assert "ya hay una cuenta" in respuesta.json()["detail"].lower()


def test_una_contrasena_corta_se_rechaza_diciendo_cuanto_falta(client):
    respuesta = client.post(
        "/api/auth/registro", json={"email": "ana@unam.edu.ar", "password": "corta"}
    )

    assert respuesta.status_code == 422
    assert "10" in respuesta.json()["detail"]


def test_entrar_con_la_contrasena_correcta_abre_sesion(client):
    _registrar(client, "ana@unam.edu.ar", password="una-frase-larga")

    respuesta = client.post(
        "/api/auth/entrar",
        json={"email": "ana@unam.edu.ar", "password": "una-frase-larga"},
    )

    assert respuesta.status_code == 200
    token = respuesta.json()["token"]
    assert client.get("/api/jobs", headers=_cabecera(token)).status_code == 200


def test_el_error_al_entrar_no_delata_si_la_cuenta_existe(client):
    """Distinguirlos permitiria averiguar quien tiene cuenta probando correos."""
    _registrar(client, "ana@unam.edu.ar", password="una-frase-larga")

    sin_cuenta = client.post(
        "/api/auth/entrar",
        json={"email": "nadie@unam.edu.ar", "password": "una-frase-larga"},
    )
    mala = client.post(
        "/api/auth/entrar",
        json={"email": "ana@unam.edu.ar", "password": "no-es-esta-frase"},
    )

    assert sin_cuenta.status_code == mala.status_code == 401
    assert sin_cuenta.json()["detail"] == mala.json()["detail"]


def test_cambiar_la_contrasena_echa_al_resto_de_dispositivos(client):
    """Si se cambia porque alguien mas la sabia, dejarle la sesion no arregla nada."""
    movil = _registrar(client, "ana@unam.edu.ar", password="una-frase-larga")
    portatil = client.post(
        "/api/auth/entrar",
        json={"email": "ana@unam.edu.ar", "password": "una-frase-larga"},
    ).json()["token"]

    client.post(
        "/api/auth/contrasena",
        json={"password": "otra-frase-bien-larga"},
        headers=_cabecera(movil),
    )

    assert client.get("/api/jobs", headers=_cabecera(portatil)).status_code == 401


def test_google_apagado_lo_dice_en_vez_de_fallar_raro(client):
    """Sin credenciales configuradas, la opcion no existe y se explica."""
    configuracion = client.get("/api/auth/google").json()
    assert configuracion["activo"] is False

    respuesta = client.post(
        "/api/auth/google", json={"code": "x", "redirect_uri": "http://localhost"}
    )

    assert respuesta.status_code == 503
    assert "correo y la contraseña" in respuesta.json()["detail"]
