"""Tests de los auxiliares del anotador (troceado y limpieza de la salida)."""

from backend.annotator.claude import (
    _format_duration,
    _split_on_line_boundaries,
    _strip_code_fence,
)


def test_split_no_parte_lineas_por_la_mitad():
    lineas = [f"[00:00:0{i}] Orador A: frase numero {i}" for i in range(10)]
    texto = "\n".join(lineas)

    trozos = _split_on_line_boundaries(texto, max_chars=80)

    assert len(trozos) > 1
    # Ninguna linea original debe haberse perdido ni partido.
    reconstruido = "\n".join(trozos)
    assert reconstruido.splitlines() == lineas


def test_split_devuelve_un_solo_trozo_si_cabe():
    texto = "linea uno\nlinea dos"
    assert _split_on_line_boundaries(texto, max_chars=10_000) == [texto]


def test_strip_code_fence_quita_el_bloque_envolvente():
    entrada = "```markdown\n# Titulo\n\nContenido\n```"
    assert _strip_code_fence(entrada) == "# Titulo\n\nContenido"


def test_strip_code_fence_respeta_markdown_normal():
    entrada = "# Titulo\n\n```python\nprint('hola')\n```"
    assert _strip_code_fence(entrada) == entrada


def test_format_duration():
    assert _format_duration(None) == "desconocida"
    assert _format_duration(600) == "10 min"
    assert _format_duration(11_520) == "3 h 12 min"
