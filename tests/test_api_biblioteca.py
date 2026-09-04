"""Tests de la API de la biblioteca.

Lo que mas importa aqui es lo ultimo del fichero: comprobar que el material que
se sube **llega de verdad al prompt** del anotador. Adjuntar un PDF que la IA no
mira seria peor que no tenerlo, porque el usuario creeria que lo esta usando.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import backend.pipeline as pipeline
from backend.models import JobStatus, TranscriptionResult, Utterance

from .pdf_de_prueba import pdf_con_texto, pdf_escaneado

PROGRAMA = pdf_con_texto(
    [
        [
            "PROGRAMA DE ANALISIS MATEMATICO I",
            "Unidad 1: Limites y continuidad",
            "Unidad 3: Integracion por partes",
        ],
        ["Bibliografia: Stewart, Calculo", "Evaluacion: dos parciales"],
    ]
)


class _Proveedor:
    name = "fake"
    supports_diarization = True

    async def transcribe(self, path, **kwargs) -> TranscriptionResult:
        return TranscriptionResult(
            provider="fake",
            text="Hoy vemos integracion por partes.",
            utterances=[
                Utterance(speaker="Orador A", start_ms=0, end_ms=3000,
                          text="Hoy vemos integracion por partes."),
            ],
            audio_duration_seconds=3600.0,
            language_code="es",
        )


class _AnotadorEspia:
    """Guarda el contexto que recibe, para poder mirarlo en el test."""

    ultimo_contexto = None
    ultimo_prompt = ""

    def __init__(self, settings):
        self._settings = settings

    async def annotate(self, transcription, *, filename, contexto=None):
        _AnotadorEspia.ultimo_contexto = contexto

        # Se reconstruye el prompt igual que lo haria un anotador real, para
        # comprobar que el material acaba dentro del texto que ve el modelo.
        from backend.annotator import prompts
        from backend.annotator.base import BaseAnnotator, _build_metadata

        class _Concreto(BaseAnnotator):
            async def _complete(self, system_prompt, user_prompt):  # pragma: no cover
                return ""

        metadata = _build_metadata(transcription, filename)
        metadata.update(_Concreto(self._settings)._contexto_de_la_materia(contexto))
        _AnotadorEspia.ultimo_prompt = prompts.USER_PROMPT_TEMPLATE.format(
            output_template=prompts.OUTPUT_TEMPLATE,
            transcript=transcription.to_diarized_text(),
            **metadata,
        )
        return "# Apuntes\n\n## Resumen ejecutivo\n\nIntegracion por partes."


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

    _AnotadorEspia.ultimo_contexto = None
    _AnotadorEspia.ultimo_prompt = ""

    with TestClient(main.app) as test_client:
        entrar_como(test_client)
        yield test_client

    get_settings.cache_clear()


def entrar_como(client, email="alumno@unam.edu.ar", password="una-frase-larga"):
    """Crea una cuenta y deja el cliente autenticado.

    Desde que la API exige sesion, un test sin cuenta solo comprueba que el
    backend responde 401. La cuenta se crea aqui para que cada test siga
    hablando del comportamiento que le interesa.
    """
    respuesta = client.post(
        "/api/auth/registro", json={"email": email, "password": password}
    )
    assert respuesta.status_code == 201, respuesta.text
    token = respuesta.json()["token"]
    client.headers["Authorization"] = f"Bearer {token}"
    client.usuario = respuesta.json()["usuario"]
    return client.usuario


def _grupo(client, nombre="Comision 3", materia="Analisis Matematico I"):
    return client.post(
        "/api/grupos", json={"nombre": nombre, "materia": materia}
    ).json()


def _audio():
    return {"file": ("clase.mp3", b"\x00" * 4096, "audio/mpeg")}


# ---------------------------------------------------------------------------
# Grupos y temas
# ---------------------------------------------------------------------------


def test_se_crea_y_se_lista_un_grupo(client):
    creado = _grupo(client)

    assert creado["materia"] == "Analisis Matematico I"
    assert creado["share_token"] is None
    assert [g["id"] for g in client.get("/api/grupos").json()] == [creado["id"]]


def test_un_grupo_sin_nombre_se_rechaza(client):
    respuesta = client.post("/api/grupos", json={"nombre": "  ", "materia": "Fisica"})

    assert respuesta.status_code == 400


def test_el_enlace_compartido_resuelve_al_grupo(client):
    grupo = _grupo(client)

    compartido = client.post(
        f"/api/grupos/{grupo['id']}/compartir", params={"permiso": "escritura"}
    ).json()
    resuelto = client.get(f"/api/compartido/{compartido['share_token']}").json()

    assert resuelto["id"] == grupo["id"]
    assert resuelto["share_permiso"] == "escritura"


def test_un_enlace_revocado_deja_de_funcionar(client):
    grupo = _grupo(client)
    token = client.post(f"/api/grupos/{grupo['id']}/compartir").json()["share_token"]

    client.delete(f"/api/grupos/{grupo['id']}/compartir")

    assert client.get(f"/api/compartido/{token}").status_code == 404


def test_los_temas_cuelgan_del_grupo(client):
    grupo = _grupo(client)

    client.post(f"/api/grupos/{grupo['id']}/temas", json={"nombre": "Unidad 3"})

    temas = client.get(f"/api/grupos/{grupo['id']}/temas").json()
    assert [t["nombre"] for t in temas] == ["Unidad 3"]


# ---------------------------------------------------------------------------
# Material
# ---------------------------------------------------------------------------


def test_se_sube_un_pdf_y_se_extrae_su_texto(client):
    grupo = _grupo(client)

    respuesta = client.post(
        f"/api/grupos/{grupo['id']}/materiales",
        files={"file": ("programa.pdf", PROGRAMA, "application/pdf")},
        data={"tipo": "programa"},
    )

    assert respuesta.status_code == 201
    material = respuesta.json()
    assert material["paginas"] == 2
    assert "Integracion por partes" in material["texto"]


def test_un_pdf_escaneado_se_rechaza_explicando_por_que(client):
    """Guardarlo en silencio haria creer que la IA lo esta leyendo."""
    grupo = _grupo(client)

    respuesta = client.post(
        f"/api/grupos/{grupo['id']}/materiales",
        files={"file": ("escaneo.pdf", pdf_escaneado(), "application/pdf")},
    )

    assert respuesta.status_code == 422
    assert "escaneo" in respuesta.json()["detail"]


def test_solo_se_aceptan_pdf(client):
    grupo = _grupo(client)

    respuesta = client.post(
        f"/api/grupos/{grupo['id']}/materiales",
        files={"file": ("apuntes.docx", b"cualquier cosa", "application/msword")},
    )

    assert respuesta.status_code == 415


def test_no_se_puede_subir_material_a_un_grupo_inexistente(client):
    respuesta = client.post(
        "/api/grupos/noexiste/materiales",
        files={"file": ("programa.pdf", PROGRAMA, "application/pdf")},
    )

    assert respuesta.status_code == 404


# ---------------------------------------------------------------------------
# Archivar clases
# ---------------------------------------------------------------------------


def test_una_clase_se_archiva_en_un_grupo(client):
    grupo = _grupo(client)
    job_id = client.post("/api/jobs", files=_audio()).json()["id"]

    client.patch(
        f"/api/jobs/{job_id}/ubicacion", params={"grupo_id": grupo["id"]}
    )

    assert client.get(f"/api/jobs/{job_id}").json()["grupo_id"] == grupo["id"]


def test_el_listado_se_puede_filtrar_por_grupo(client):
    grupo = _grupo(client)
    dentro = client.post("/api/jobs", files=_audio()).json()["id"]
    client.post("/api/jobs", files=_audio())  # queda suelta
    client.patch(f"/api/jobs/{dentro}/ubicacion", params={"grupo_id": grupo["id"]})

    filtrados = client.get("/api/jobs", params={"grupo_id": grupo["id"]}).json()

    assert [t["id"] for t in filtrados] == [dentro]


def test_borrar_el_grupo_no_borra_las_clases(client):
    """Una clase cuesta dinero: reorganizar carpetas no puede tirarla."""
    grupo = _grupo(client)
    job_id = client.post(
        "/api/jobs", files=_audio(), params={"grupo_id": grupo["id"]}
    ).json()["id"]

    client.delete(f"/api/grupos/{grupo['id']}")

    trabajo = client.get(f"/api/jobs/{job_id}").json()
    assert trabajo["status"] == JobStatus.COMPLETED.value
    assert trabajo["grupo_id"] is None


# ---------------------------------------------------------------------------
# Apuntes editados a mano
# ---------------------------------------------------------------------------


def test_las_correcciones_propias_no_pisan_lo_que_genero_la_ia(client):
    job_id = client.post("/api/jobs", files=_audio()).json()["id"]

    client.put(f"/api/jobs/{job_id}/notes", json={"contenido": "# Mi version"})

    trabajo = client.get(f"/api/jobs/{job_id}").json()
    assert trabajo["notes_editadas"] == "# Mi version"
    assert "Integracion por partes" in trabajo["notes_markdown"]


def test_se_puede_volver_a_los_apuntes_de_la_ia(client):
    job_id = client.post("/api/jobs", files=_audio()).json()["id"]
    client.put(f"/api/jobs/{job_id}/notes", json={"contenido": "# Mi version"})

    client.delete(f"/api/jobs/{job_id}/notes")

    assert client.get(f"/api/jobs/{job_id}").json()["notes_editadas"] is None


def test_rehacer_los_apuntes_no_borra_las_correcciones(client):
    """Reintentar con la IA no puede llevarse por delante lo escrito a mano."""
    job_id = client.post("/api/jobs", files=_audio()).json()["id"]
    client.put(f"/api/jobs/{job_id}/notes", json={"contenido": "# Mi version"})

    client.post(f"/api/jobs/{job_id}/reanotar")

    assert client.get(f"/api/jobs/{job_id}").json()["notes_editadas"] == "# Mi version"


# ---------------------------------------------------------------------------
# Lo esencial: el material llega al prompt
# ---------------------------------------------------------------------------


def test_una_clase_suelta_no_arrastra_contexto(client):
    """Sin grupo, el prompt debe quedar igual que antes de existir la biblioteca."""
    client.post("/api/jobs", files=_audio())

    assert _AnotadorEspia.ultimo_contexto is None
    assert "MATERIAL DE LA MATERIA" not in _AnotadorEspia.ultimo_prompt
    assert "- Materia:" not in _AnotadorEspia.ultimo_prompt


def test_la_materia_del_grupo_llega_al_prompt(client):
    grupo = _grupo(client)
    tema = client.post(
        f"/api/grupos/{grupo['id']}/temas", json={"nombre": "Unidad 3"}
    ).json()

    client.post(
        "/api/jobs",
        files=_audio(),
        params={"grupo_id": grupo["id"], "tema_id": tema["id"]},
    )

    assert "Analisis Matematico I" in _AnotadorEspia.ultimo_prompt
    assert "Unidad 3" in _AnotadorEspia.ultimo_prompt


def test_el_texto_del_pdf_llega_al_prompt(client):
    """La prueba de fondo: adjuntar el programa cambia lo que ve el modelo."""
    grupo = _grupo(client)
    client.post(
        f"/api/grupos/{grupo['id']}/materiales",
        files={"file": ("programa.pdf", PROGRAMA, "application/pdf")},
        data={"tipo": "programa"},
    )

    client.post("/api/jobs", files=_audio(), params={"grupo_id": grupo["id"]})

    prompt = _AnotadorEspia.ultimo_prompt
    assert "MATERIAL DE LA MATERIA" in prompt
    assert "Unidad 1: Limites y continuidad" in prompt
    assert "programa.pdf" in prompt


def test_el_prompt_advierte_de_que_el_material_no_es_la_fuente(client):
    """Sin esa instruccion, el modelo puede anotar temas que no se explicaron."""
    grupo = _grupo(client)
    client.post(
        f"/api/grupos/{grupo['id']}/materiales",
        files={"file": ("programa.pdf", PROGRAMA, "application/pdf")},
    )

    client.post("/api/jobs", files=_audio(), params={"grupo_id": grupo["id"]})

    prompt = _AnotadorEspia.ultimo_prompt
    assert "no debes anadir contenido del programa" in prompt
    assert "manda la clase" in prompt


def test_el_material_tambien_llega_al_rehacer_los_apuntes(client):
    grupo = _grupo(client)
    job_id = client.post(
        "/api/jobs", files=_audio(), params={"grupo_id": grupo["id"]}
    ).json()["id"]
    client.post(
        f"/api/grupos/{grupo['id']}/materiales",
        files={"file": ("programa.pdf", PROGRAMA, "application/pdf")},
        data={"tipo": "programa"},
    )

    client.post(f"/api/jobs/{job_id}/reanotar")

    assert "Unidad 3: Integracion por partes" in _AnotadorEspia.ultimo_prompt


def test_el_material_no_desplaza_a_la_transcripcion(client, monkeypatch):
    """Un PDF enorme no puede comerse el sitio de lo que hay que anotar."""
    from backend.config import get_settings

    grupo = _grupo(client)
    gigante = pdf_con_texto([["relleno " * 40] * 40 for _ in range(6)])
    client.post(
        f"/api/grupos/{grupo['id']}/materiales",
        files={"file": ("enorme.pdf", gigante, "application/pdf")},
    )
    monkeypatch.setattr(
        get_settings(), "annotation_material_char_limit", 500
    )

    client.post("/api/jobs", files=_audio(), params={"grupo_id": grupo["id"]})

    prompt = _AnotadorEspia.ultimo_prompt
    assert "documento recortado por longitud" in prompt
    assert "Hoy vemos integracion por partes." in prompt


# ---------------------------------------------------------------------------
# Notas propias
# ---------------------------------------------------------------------------


def test_las_notas_propias_se_crean_y_se_editan(client):
    grupo = _grupo(client)

    nota = client.post(
        f"/api/grupos/{grupo['id']}/notas",
        json={"titulo": "Dudas", "contenido": "revisar 4.2"},
    ).json()
    client.put(f"/api/notas/{nota['id']}", json={"contenido": "revisar 4.2 y 4.7"})

    notas = client.get(f"/api/grupos/{grupo['id']}/notas").json()
    assert notas[0]["titulo"] == "Dudas"
    assert notas[0]["contenido"] == "revisar 4.2 y 4.7"


def test_borrar_una_nota_que_no_existe_da_404(client):
    assert client.delete("/api/notas/noexiste").status_code == 404


def test_la_salud_publica_el_tope_de_los_pdf(client):
    """La interfaz lo necesita: sin el, anuncia el tope global de Streamlit."""
    salud = client.get("/api/health").json()

    assert salud["max_material_mb"] > 0
    assert salud["max_material_mb"] < salud["max_upload_mb"]


def test_un_pdf_demasiado_grande_se_rechaza(client, monkeypatch):
    from backend.config import get_settings

    monkeypatch.setattr(get_settings(), "max_material_mb", 0)
    grupo = _grupo(client)

    respuesta = client.post(
        f"/api/grupos/{grupo['id']}/materiales",
        files={"file": ("programa.pdf", PROGRAMA, "application/pdf")},
    )

    assert respuesta.status_code == 413


# ---------------------------------------------------------------------------
# Nombre de la clase
# ---------------------------------------------------------------------------


def test_una_clase_se_puede_renombrar(client):
    """El nombre del fichero no identifica nada: el movil las llama igual."""
    job_id = client.post("/api/jobs", files=_audio()).json()["id"]

    client.patch(
        f"/api/jobs/{job_id}/titulo", json={"titulo": "Clase 1 - Limites"}
    )

    clase = client.get(f"/api/jobs/{job_id}").json()
    assert clase["titulo"] == "Clase 1 - Limites"
    assert clase["filename"] == "clase.mp3"  # el original no se pierde


def test_el_nombre_aparece_en_el_listado(client):
    """El indice se dibuja con el listado, no pidiendo cada clase entera."""
    job_id = client.post("/api/jobs", files=_audio()).json()["id"]
    client.patch(f"/api/jobs/{job_id}/titulo", json={"titulo": "Clase 2"})

    listado = client.get("/api/jobs").json()

    assert [c["titulo"] for c in listado if c["id"] == job_id] == ["Clase 2"]


def test_un_nombre_vacio_devuelve_la_clase_a_su_fichero(client):
    job_id = client.post("/api/jobs", files=_audio()).json()["id"]
    client.patch(f"/api/jobs/{job_id}/titulo", json={"titulo": "Algo"})

    client.patch(f"/api/jobs/{job_id}/titulo", json={"titulo": "   "})

    assert client.get(f"/api/jobs/{job_id}").json()["titulo"] is None


def test_un_nombre_kilometrico_se_rechaza(client):
    job_id = client.post("/api/jobs", files=_audio()).json()["id"]

    respuesta = client.patch(
        f"/api/jobs/{job_id}/titulo", json={"titulo": "x" * 121}
    )

    assert respuesta.status_code == 422
    assert "120" in respuesta.json()["detail"]


def test_renombrar_una_clase_que_no_existe_avisa(client):
    respuesta = client.patch("/api/jobs/no-existe/titulo", json={"titulo": "X"})

    assert respuesta.status_code == 404
