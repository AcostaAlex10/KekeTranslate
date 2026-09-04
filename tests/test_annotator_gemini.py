"""Tests del anotador de Gemini y de la eleccion de anotador.

Ninguno llama a la API: se comprueba la seleccion del proveedor y, sobre todo,
la lectura de respuestas anomalas, que es donde Gemini se diferencia de Claude
(puede devolver un 200 sin apuntes utiles).
"""

from __future__ import annotations

import pytest

from backend.annotator import (
    AnnotationError,
    clave_del_anotador,
    get_annotator,
    modelo_del_anotador,
)
from backend.annotator.gemini import (
    GeminiAnnotator,
    _es_temporal,
    _explicar_fallo,
    _extraer_texto,
)
from backend.config import Settings


# ---------------------------------------------------------------------------
# Dobles de la respuesta del SDK
# ---------------------------------------------------------------------------


class _Candidato:
    def __init__(self, finish_reason):
        self.finish_reason = finish_reason


class _Feedback:
    def __init__(self, block_reason):
        self.block_reason = block_reason


class _Respuesta:
    """Imita lo justo de la respuesta del SDK que lee el anotador."""

    def __init__(self, texto="", motivo="FinishReason.STOP", bloqueo=None,
                 con_candidatos=True):
        self.text = texto
        self.candidates = [_Candidato(motivo)] if con_candidatos else []
        self.prompt_feedback = _Feedback(bloqueo)


# ---------------------------------------------------------------------------
# Eleccion del anotador
# ---------------------------------------------------------------------------


def test_por_defecto_se_usa_gemini():
    """Quien clone el repo debe poder generar apuntes sin pagar."""
    ajustes = Settings(_env_file=None, gemini_api_key="clave")

    assert ajustes.annotator_provider == "gemini"
    assert isinstance(get_annotator(ajustes), GeminiAnnotator)


def test_se_puede_volver_a_claude():
    ajustes = Settings(
        _env_file=None, annotator_provider="anthropic", anthropic_api_key="clave"
    )

    assert type(get_annotator(ajustes)).__name__ == "ClaudeAnnotator"


def test_gemini_sin_clave_explica_donde_conseguirla():
    ajustes = Settings(_env_file=None, annotator_provider="gemini")

    with pytest.raises(AnnotationError) as error:
        get_annotator(ajustes)

    assert "GEMINI_API_KEY" in str(error.value)
    assert "aistudio.google.com" in str(error.value)


def test_la_clave_y_el_modelo_siguen_al_proveedor_activo():
    """La interfaz avisa de la clave que falta: debe ser la del anotador en uso."""
    ajustes = Settings(
        _env_file=None,
        annotator_provider="gemini",
        gemini_api_key="clave-gemini",
        anthropic_api_key="",
    )

    assert clave_del_anotador(ajustes) == "clave-gemini"
    assert modelo_del_anotador(ajustes) == ajustes.gemini_model


# ---------------------------------------------------------------------------
# Lectura de la respuesta
# ---------------------------------------------------------------------------


def test_respuesta_normal_devuelve_el_texto():
    assert _extraer_texto(_Respuesta(texto="# Apuntes\n\nContenido")) == (
        "# Apuntes\n\nContenido"
    )


def test_una_respuesta_cortada_por_longitud_no_pasa_por_buena():
    """Unos apuntes truncados parecen validos: hay que detectarlos."""
    with pytest.raises(AnnotationError) as error:
        _extraer_texto(
            _Respuesta(texto="# Apuntes a medio", motivo="FinishReason.MAX_TOKENS")
        )

    assert "GEMINI_MAX_TOKENS" in str(error.value)


def test_una_peticion_bloqueada_se_explica():
    with pytest.raises(AnnotationError) as error:
        _extraer_texto(_Respuesta(bloqueo="SAFETY"))

    assert "SAFETY" in str(error.value)


def test_respuesta_sin_candidatos_es_un_error():
    with pytest.raises(AnnotationError):
        _extraer_texto(_Respuesta(con_candidatos=False))


def test_respuesta_vacia_es_un_error():
    with pytest.raises(AnnotationError):
        _extraer_texto(_Respuesta(texto="   "))


# ---------------------------------------------------------------------------
# Traduccion de fallos
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mensaje, esperado",
    [
        ("API key not valid", "aistudio.google.com"),
        ("404 model not found", "GEMINI_MODEL"),
        ("429 RESOURCE_EXHAUSTED: quota", "cuota"),
    ],
)
def test_los_fallos_habituales_se_traducen_a_algo_accionable(mensaje, esperado):
    ajustes = Settings(_env_file=None)

    assert esperado in _explicar_fallo(Exception(mensaje), ajustes)


def test_una_clave_restringida_no_se_confunde_con_una_clave_mala():
    """Son dos problemas distintos y la solucion no se parece en nada.

    Con la clave restringida, decir "revisa la clave" manda a cambiar algo que
    esta bien. Lo que hay que tocar son las restricciones de la clave en la
    consola de Google Cloud.
    """
    ajustes = Settings(_env_file=None)
    fallo = (
        "403 PERMISSION_DENIED. Requests to this API "
        "generativelanguage.googleapis.com are blocked. "
        "reason: API_KEY_SERVICE_BLOCKED"
    )

    explicacion = _explicar_fallo(Exception(fallo), ajustes)

    assert "válida" in explicacion
    assert "Restricciones de API" in explicacion


def test_una_clave_del_formato_viejo_dice_que_formato_hace_falta():
    """Las claves `AQ.` que emite AI Studio a algunas cuentas no sirven aqui.

    Es un problema abierto de Google, no de esta app, y no se arregla volviendo
    a pegar la clave: hay que conseguir una `AIza...`.
    """
    ajustes = Settings(_env_file=None)
    fallo = "401 UNAUTHENTICATED. reason: ACCESS_TOKEN_TYPE_UNSUPPORTED"

    explicacion = _explicar_fallo(Exception(fallo), ajustes)

    assert "AQ." in explicacion
    assert "AIza" in explicacion


def test_un_fallo_desconocido_conserva_el_mensaje_original():
    ajustes = Settings(_env_file=None)

    assert "se cayo la red" in _explicar_fallo(Exception("se cayo la red"), ajustes)


# ---------------------------------------------------------------------------
# Fallos temporales
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mensaje",
    [
        "503 UNAVAILABLE. The model is overloaded",
        "This model is currently experiencing high demand",
    ],
)
def test_la_saturacion_del_servicio_se_considera_temporal(mensaje):
    """Merece reintento: la transcripcion ya esta hecha y pagada."""
    assert _es_temporal(Exception(mensaje))


@pytest.mark.parametrize(
    "mensaje",
    ["404 NOT_FOUND", "API key not valid", "429 quota exceeded"],
)
def test_los_fallos_de_configuracion_no_son_temporales(mensaje):
    """Reintentar una clave mal puesta solo hace perder tiempo."""
    assert not _es_temporal(Exception(mensaje))


def test_el_error_conserva_lo_que_dijo_google():
    """Google suele indicar el modelo sustituto: no hay que perder ese dato."""
    ajustes = Settings(_env_file=None)
    original = (
        "404 NOT_FOUND. This model models/gemini-2.5-flash is no longer "
        "available to new users. Please update your code to use "
        "models/gemini-3.6-flash"
    )

    explicacion = _explicar_fallo(Exception(original), ajustes)

    assert "GEMINI_MODEL" in explicacion
    assert "gemini-3.6-flash" in explicacion
