"""Comprueba que la interfaz no anida contenedores que Streamlit prohibe.

Streamlit lanza `StreamlitAPIException` al abrir un `expander` (o un `popover`)
dentro de otro, y lo hace **en tiempo de ejecucion**: el fichero importa bien,
los tests de widgets pasan, y la seccion revienta en cuanto se abre con datos
reales. Ya ocurrio dos veces en este proyecto.

Lo dificil de cazarlo a ojo es que los dos contenedores rara vez estan juntos:
el de fuera se abre en el bucle de "Mis clases" y el de dentro, varias funciones
mas alla. Por eso el test sigue las llamadas: marca que funciones abren un
contenedor —directamente o a traves de las funciones que llaman— y luego busca
un `with st.expander(...)` que invoque a una de ellas.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

RAIZ = pathlib.Path(__file__).resolve().parent.parent

FICHEROS = ["frontend/app.py"]

# Contenedores que Streamlit se niega a anidar uno dentro de otro.
CONTENEDORES = {"expander", "popover"}


def _abre_contenedor(nodo: ast.AST) -> bool:
    """Indica si la llamada es `st.expander(...)` o `st.popover(...)`."""
    return (
        isinstance(nodo, ast.Call)
        and isinstance(nodo.func, ast.Attribute)
        and nodo.func.attr in CONTENEDORES
    )


def _llamadas(nodo: ast.AST) -> set[str]:
    """Nombres de las funciones invocadas dentro de un subarbol."""
    nombres = set()
    for hijo in ast.walk(nodo):
        if isinstance(hijo, ast.Call):
            objetivo = hijo.func
            nombre = getattr(objetivo, "id", None) or getattr(objetivo, "attr", None)
            if nombre:
                nombres.add(nombre)
    return nombres


def _funciones_que_abren(arbol: ast.AST) -> set[str]:
    """Funciones que abren un contenedor, propagando por las que llaman.

    Se repite hasta que deja de crecer, porque una funcion puede abrirlo a
    traves de dos o tres saltos.
    """
    definiciones = {
        n.name: n
        for n in ast.walk(arbol)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    abren = {
        nombre
        for nombre, definicion in definiciones.items()
        if any(_abre_contenedor(h) for h in ast.walk(definicion))
    }

    creciendo = True
    while creciendo:
        creciendo = False
        for nombre, definicion in definiciones.items():
            if nombre in abren:
                continue
            if _llamadas(definicion) & abren:
                abren.add(nombre)
                creciendo = True

    return abren


def _anidamientos(ruta: pathlib.Path) -> list[str]:
    arbol = ast.parse(ruta.read_text(encoding="utf-8"))
    abren = _funciones_que_abren(arbol)
    problemas: list[str] = []

    for nodo in ast.walk(arbol):
        if not isinstance(nodo, ast.With):
            continue
        if not any(_abre_contenedor(i.context_expr) for i in nodo.items):
            continue

        # Otro contenedor abierto directamente dentro de este.
        for hijo in ast.walk(nodo):
            if hijo is nodo or not isinstance(hijo, ast.With):
                continue
            for item in hijo.items:
                if _abre_contenedor(item.context_expr):
                    problemas.append(
                        f"{ruta.name}:{hijo.lineno} contenedor anidado dentro "
                        f"del abierto en la linea {nodo.lineno}"
                    )

        # O abierto por una funcion llamada desde aqui.
        for llamada in sorted(_llamadas(nodo) & abren):
            problemas.append(
                f"{ruta.name}:{nodo.lineno} llama a '{llamada}()', que abre un "
                "contenedor, dentro de otro contenedor"
            )

    return problemas


@pytest.mark.parametrize("relativo", FICHEROS)
def test_no_se_anidan_contenedores(relativo):
    problemas = _anidamientos(RAIZ / relativo)

    assert not problemas, (
        "Streamlit reventara al renderizar esto:\n" + "\n".join(problemas)
    )


def test_el_detector_ve_el_anidamiento_directo(tmp_path):
    """Un test que no detecta nada no protege nada."""
    ruta = tmp_path / "directo.py"
    ruta.write_text(
        "import streamlit as st\n"
        "with st.expander('fuera'):\n"
        "    with st.expander('dentro'):\n"
        "        st.write('hola')\n",
        encoding="utf-8",
    )

    assert _anidamientos(ruta)


def test_el_detector_ve_el_anidamiento_a_traves_de_una_funcion(tmp_path):
    """El caso real: los dos contenedores viven en sitios distintos."""
    ruta = tmp_path / "indirecto.py"
    ruta.write_text(
        "import streamlit as st\n"
        "def mostrar():\n"
        "    with st.expander('dentro'):\n"
        "        st.write('hola')\n"
        "with st.expander('fuera'):\n"
        "    mostrar()\n",
        encoding="utf-8",
    )

    problemas = _anidamientos(ruta)

    assert problemas
    assert "mostrar()" in problemas[0]


def test_un_contenedor_suelto_no_da_falso_positivo(tmp_path):
    ruta = tmp_path / "correcto.py"
    ruta.write_text(
        "import streamlit as st\n"
        "def mostrar():\n"
        "    with st.expander('uno'):\n"
        "        st.write('hola')\n"
        "mostrar()\n"
        "with st.expander('otro'):\n"
        "    st.write('adios')\n",
        encoding="utf-8",
    )

    assert not _anidamientos(ruta)
