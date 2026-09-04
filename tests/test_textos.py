"""Guarda la ortografia de los textos que lee el usuario.

Todo el proyecto se escribio sin tildes ni enyes, comentarios y mensajes por
igual. En los comentarios da lo mismo; en lo que sale por pantalla no: `Anadir`
no es una palabra, y una herramienta de estudio que escribe mal resta
credibilidad justo donde mas la necesita.

Este test mira **solo** las cadenas que llegan al usuario. Se localizan por la
llamada que las recibe (`HTTPException(detail=...)`, `AnnotationError(...)`,
`st.error(...)`, etc.), asi que los comentarios y los docstrings quedan fuera y
pueden seguir escribiendose sin acentos.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

RAIZ = pathlib.Path(__file__).resolve().parent.parent

# Todo lo que produce texto para el usuario.
FICHEROS = [
    "frontend/app.py",
    "backend/main.py",
    "backend/models.py",
    "backend/pdf.py",
    "backend/annotator/gemini.py",
    "backend/annotator/claude.py",
    "backend/annotator/factory.py",
]

# Funciones cuyas cadenas ve el usuario.
LLAMADAS_VISIBLES = {
    "HTTPException", "AnnotationError", "TranscriptionError", "PdfSinTexto",
}

# Palabras que sin tilde no existen en espanol. Se deja fuera todo lo ambiguo
# ("esta", "mas", "fallo", "titulo") para que el test no de falsos positivos.
SIEMPRE_MAL = re.compile(
    r"\b("
    r"anadir|anade|anadela|anadido|"
    r"todavia|asi|aqui|despues|tambien|"
    r"numero|paginas|catedra|"
    r"deberia|podria|quedaria|seria|"
    r"ningun|"
    r"dia|dias"
    r")\b",
    re.IGNORECASE,
)

# Una palabra terminada en -cion o -sion siempre lleva tilde en espanol.
TERMINACION_SIN_TILDE = re.compile(r"\b\w*(cion|ciones|sion|siones)\b", re.IGNORECASE)


# Argumentos cuyo valor es un identificador tecnico, no un texto que se lea:
# la clave con la que Streamlit distingue un widget de otro, el tipo MIME, el
# nombre del fichero descargado...
ARGUMENTOS_TECNICOS = {"key", "mime", "file_name", "language", "type", "icon"}


def _claves_y_rutas(nodo: ast.AST) -> set[int]:
    """Cadenas del subarbol que no son prosa, sino codigo.

    Dentro de un mensaje conviven dos clases de cadena: la que se lee
    (`"páginas leídas"`) y la que solo sirve para programar, como la clave de
    un diccionario en `material['paginas']` o el `key="seccion"` de un widget.
    La segunda tiene que escribirse igual que en la API, sin tildes, asi que
    revisarla daria un falso aviso.
    """
    excluidas: set[int] = set()
    for hijo in ast.walk(nodo):
        if isinstance(hijo, ast.Subscript) and isinstance(hijo.slice, ast.Constant):
            excluidas.add(id(hijo.slice))
        if isinstance(hijo, ast.keyword) and hijo.arg in ARGUMENTOS_TECNICOS:
            for dentro in ast.walk(hijo.value):
                if isinstance(dentro, ast.Constant):
                    excluidas.add(id(dentro))
    return excluidas


def _es_prosa(texto: str) -> bool:
    """Distingue una frase de un identificador, una URL o un nombre de fichero."""
    if not texto.strip() or texto.isupper():
        return False
    if texto.startswith(("http", "/api", "application/", "_", ".")):
        return False
    # Sin espacios y con extension: es un nombre de fichero, no una frase.
    if " " not in texto.strip() and "." in texto:
        return False
    return True


def _cadenas_visibles(ruta: pathlib.Path) -> list[tuple[int, str]]:
    """Devuelve las cadenas que acaban en pantalla, con su numero de linea."""
    arbol = ast.parse(ruta.read_text(encoding="utf-8"))
    encontradas: list[tuple[int, str]] = []

    for nodo in ast.walk(arbol):
        if not isinstance(nodo, ast.Call):
            continue

        objetivo = nodo.func
        nombre = getattr(objetivo, "id", None) or getattr(objetivo, "attr", None)
        if nombre is None:
            continue

        es_visible = nombre in LLAMADAS_VISIBLES or (
            isinstance(objetivo, ast.Attribute)
            and getattr(objetivo.value, "id", None) == "st"
        )
        if not es_visible:
            continue

        excluidas = _claves_y_rutas(nodo)

        # `detail=` y los argumentos posicionales; se recorre el subarbol para
        # cazar tambien las cadenas concatenadas y las f-strings.
        for hijo in ast.walk(nodo):
            if not (isinstance(hijo, ast.Constant) and isinstance(hijo.value, str)):
                continue
            if id(hijo) in excluidas:
                continue
            encontradas.append((hijo.lineno, hijo.value))

    return encontradas


def _defectos(ruta: pathlib.Path) -> list[str]:
    problemas = []
    for linea, texto in _cadenas_visibles(ruta):
        if not _es_prosa(texto):
            continue
        for patron, motivo in (
            (SIEMPRE_MAL, "palabra sin tilde o sin ene"),
            (TERMINACION_SIN_TILDE, "terminacion -cion/-sion sin tilde"),
        ):
            for hallazgo in patron.findall(texto):
                palabra = hallazgo if isinstance(hallazgo, str) else hallazgo[0]
                problemas.append(
                    f"{ruta.name}:{linea} {motivo}: '{palabra}' en {texto[:70]!r}"
                )
    return problemas


@pytest.mark.parametrize("relativo", FICHEROS)
def test_los_mensajes_al_usuario_estan_bien_escritos(relativo):
    problemas = _defectos(RAIZ / relativo)

    assert not problemas, "Textos mal escritos:\n" + "\n".join(problemas)


def test_el_propio_detector_funciona():
    """Un test que no detecta nada no protege nada."""
    import tempfile

    fuente = (
        "from fastapi import HTTPException\n"
        'raise HTTPException(status_code=400, detail="Anadir una transcripcion")\n'
    )
    with tempfile.TemporaryDirectory() as carpeta:
        ruta = pathlib.Path(carpeta) / "ejemplo.py"
        ruta.write_text(fuente, encoding="utf-8")

        problemas = _defectos(ruta)

    assert len(problemas) == 2
    assert any("anadir" in p.lower() for p in problemas)
    assert any("transcripcion" in p.lower() for p in problemas)
