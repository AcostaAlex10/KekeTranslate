"""Tests de la carga de ajustes desde el entorno.

El caso que cubren de verdad es el de quien acaba de clonar el repo: copia
`.env.example` a `.env` y arranca. Ese fichero deja variables sin valor, y eso
llego a impedir que el backend arrancara.
"""

from __future__ import annotations

import pytest

from backend.config import Settings


def _ajustes(monkeypatch, **entorno) -> Settings:
    """Construye los ajustes con un entorno controlado, ignorando el .env real."""
    for clave, valor in entorno.items():
        monkeypatch.setenv(clave, valor)
    return Settings(_env_file=None)


def test_numerico_vacio_no_rompe_el_arranque(monkeypatch):
    """`EXPECTED_SPEAKERS=` viene asi en .env.example: debe valer como 'sin valor'."""
    ajustes = _ajustes(monkeypatch, EXPECTED_SPEAKERS="")

    assert ajustes.expected_speakers is None


def test_numerico_vacio_cae_al_valor_por_defecto(monkeypatch):
    """Vaciar una variable equivale a no ponerla, no a poner cero."""
    ajustes = _ajustes(monkeypatch, MAX_UPLOAD_MB="", ANTHROPIC_MAX_TOKENS="")

    assert ajustes.max_upload_mb == 5120
    assert ajustes.anthropic_max_tokens == 32_000


def test_el_idioma_vacio_se_respeta(monkeypatch):
    """En texto el vacio si significa algo: idioma vacio pide deteccion automatica."""
    ajustes = _ajustes(monkeypatch, TRANSCRIPTION_LANGUAGE="")

    assert ajustes.transcription_language == ""


def test_los_valores_numericos_reales_siguen_funcionando(monkeypatch):
    ajustes = _ajustes(monkeypatch, EXPECTED_SPEAKERS="3", MAX_UPLOAD_MB="200")

    assert ajustes.expected_speakers == 3
    assert ajustes.max_upload_mb == 200
    assert ajustes.max_upload_bytes == 200 * 1024 * 1024


def test_un_numero_invalido_sigue_siendo_un_error(monkeypatch):
    """Vaciar es 'no configurado'; escribir cualquier cosa sigue siendo un fallo."""
    with pytest.raises(Exception):
        _ajustes(monkeypatch, EXPECTED_SPEAKERS="dos")
