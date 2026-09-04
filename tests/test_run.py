"""Tests del lanzador.

Lo delicado aqui es escribir en el `.env`: es el fichero donde estan las claves
del usuario, y una escritura torpe puede borrarle configuracion.
"""

from __future__ import annotations

import pytest

import run


@pytest.fixture
def proyecto(tmp_path, monkeypatch):
    """Apunta el lanzador a un directorio de mentira, no al proyecto real."""
    monkeypatch.setattr(run, "RAIZ", tmp_path)
    monkeypatch.delenv("ASSEMBLYAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ANNOTATOR_PROVIDER", raising=False)
    monkeypatch.delenv("TRANSCRIPTION_PROVIDER", raising=False)
    return tmp_path


def test_escribir_conserva_comentarios_y_otras_variables(proyecto):
    """El .env del usuario no puede quedar mutilado al guardar una clave."""
    (proyecto / ".env").write_text(
        "# Configuracion de KekeTranslate\n"
        "TRANSCRIPTION_LANGUAGE=es\n"
        "ASSEMBLYAI_API_KEY=\n"
        "\n"
        "# Ajustes internos\n"
        "MAX_UPLOAD_MB=5120\n",
        encoding="utf-8",
    )

    run.escribir_en_env({"ASSEMBLYAI_API_KEY": "clave-nueva"})

    resultado = (proyecto / ".env").read_text(encoding="utf-8")
    assert "ASSEMBLYAI_API_KEY=clave-nueva" in resultado
    assert "# Configuracion de KekeTranslate" in resultado
    assert "# Ajustes internos" in resultado
    assert "TRANSCRIPTION_LANGUAGE=es" in resultado
    assert "MAX_UPLOAD_MB=5120" in resultado
    # No debe duplicar la variable que ya existia.
    assert resultado.count("ASSEMBLYAI_API_KEY") == 1


def test_escribir_anade_las_variables_que_no_estaban(proyecto):
    """Un .env viejo no tiene las claves nuevas: hay que anadirlas."""
    (proyecto / ".env").write_text("ASSEMBLYAI_API_KEY=vieja\n", encoding="utf-8")

    run.escribir_en_env(
        {"ANNOTATOR_PROVIDER": "gemini", "GEMINI_API_KEY": "clave-gemini"}
    )

    valores = run.leer_env()
    assert valores["ANNOTATOR_PROVIDER"] == "gemini"
    assert valores["GEMINI_API_KEY"] == "clave-gemini"
    assert valores["ASSEMBLYAI_API_KEY"] == "vieja"


def test_leer_env_ignora_comentarios_y_lineas_sueltas(proyecto):
    (proyecto / ".env").write_text(
        "# un comentario\n\nGEMINI_API_KEY=abc\nlinea suelta sin igual\n",
        encoding="utf-8",
    )

    valores = run.leer_env()

    assert valores["GEMINI_API_KEY"] == "abc"
    assert "# un comentario" not in valores


def test_leer_env_quita_comillas(proyecto):
    """Pegar la clave entre comillas es un error facil de cometer."""
    (proyecto / ".env").write_text('GEMINI_API_KEY="abc"\n', encoding="utf-8")

    assert run.leer_env()["GEMINI_API_KEY"] == "abc"


def test_solo_reclama_las_claves_del_proveedor_elegido(proyecto):
    """Con Gemini configurado no debe pedir la clave de Anthropic."""
    (proyecto / ".env").write_text(
        "TRANSCRIPTION_PROVIDER=assemblyai\n"
        "ASSEMBLYAI_API_KEY=algo\n"
        "ANNOTATOR_PROVIDER=gemini\n"
        "GEMINI_API_KEY=algo\n",
        encoding="utf-8",
    )

    assert run.claves_que_faltan() == []


def test_avisa_de_la_clave_que_falta(proyecto):
    (proyecto / ".env").write_text(
        "ANNOTATOR_PROVIDER=gemini\nGEMINI_API_KEY=algo\n", encoding="utf-8"
    )

    assert run.claves_que_faltan() == ["ASSEMBLYAI_API_KEY"]


def test_una_variable_con_otra_mayuscula_no_se_duplica(proyecto):
    """El fallo real: `Gemini_API_KEY` y `GEMINI_API_KEY` convivian en el .env.

    Pydantic no distingue mayusculas, asi que la app arrancaba igual, pero
    quedaban dos lineas para la misma variable: editar la equivocada no tenia
    ningun efecto y no habia forma de saber por que.
    """
    (proyecto / ".env").write_text("Gemini_API_KEY=clave-vieja\n", encoding="utf-8")

    run.escribir_en_env({"GEMINI_API_KEY": "clave-nueva"})

    resultado = (proyecto / ".env").read_text(encoding="utf-8")
    assert resultado.upper().count("GEMINI_API_KEY") == 1
    assert "GEMINI_API_KEY=clave-nueva" in resultado


def test_leer_env_normaliza_el_nombre_de_la_variable(proyecto):
    (proyecto / ".env").write_text("Gemini_API_KEY=una-clave\n", encoding="utf-8")

    assert run.leer_env()["GEMINI_API_KEY"] == "una-clave"


def test_una_variable_ya_duplicada_se_deja_en_una_sola_linea(proyecto):
    """Hay que poder limpiar un .env que ya venia con la duplicidad."""
    (proyecto / ".env").write_text(
        "GEMINI_API_KEY=clave\nGemini_API_KEY=clave\nSTORAGE_DIR=./storage\n",
        encoding="utf-8",
    )

    run.escribir_en_env({"STORAGE_DIR": "./storage"})

    resultado = (proyecto / ".env").read_text(encoding="utf-8")
    assert resultado.upper().count("GEMINI_API_KEY") == 1
    assert "GEMINI_API_KEY=clave" in resultado
