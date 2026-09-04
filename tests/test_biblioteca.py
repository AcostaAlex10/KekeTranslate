"""Tests de la biblioteca: grupos, temas, material y notas.

El foco esta en lo que puede hacer perder trabajo al usuario: que borrar algo
se lleve por delante mas de lo que deberia, y que compartir un grupo deje de
funcionar al cambiar de opinion sobre los permisos.
"""

from __future__ import annotations

import pytest

from backend.biblioteca import Biblioteca
from backend.models import Permiso, TipoMaterial


@pytest.fixture
def biblioteca(tmp_path) -> Biblioteca:
    return Biblioteca(tmp_path / "prueba.db")


@pytest.fixture
def grupo(biblioteca):
    return biblioteca.crear_grupo("Comision 3", "Analisis Matematico I")


# ---------------------------------------------------------------------------
# Grupos
# ---------------------------------------------------------------------------


def test_un_grupo_nace_privado(grupo):
    """Compartir tiene que ser siempre un acto explicito."""
    assert grupo.share_token is None
    assert grupo.compartido is False


def test_se_recupera_por_id(biblioteca, grupo):
    assert biblioteca.grupo(grupo.id).nombre == "Comision 3"


def test_los_grupos_se_listan_por_materia(biblioteca):
    biblioteca.crear_grupo("Teoria", "Fisica II")
    biblioteca.crear_grupo("Practica", "Algebra")

    materias = [g.materia for g in biblioteca.listar_grupos()]

    assert materias == ["Algebra", "Fisica II"]


# ---------------------------------------------------------------------------
# Compartir
# ---------------------------------------------------------------------------


def test_compartir_genera_un_enlace_y_su_permiso(biblioteca, grupo):
    compartido = biblioteca.compartir(grupo.id, Permiso.ESCRITURA)

    assert compartido.share_token
    assert compartido.share_permiso is Permiso.ESCRITURA
    assert compartido.compartido is True


def test_cambiar_el_permiso_no_invalida_el_enlace(biblioteca, grupo):
    """Pasar de lectura a escritura no puede dejar fuera a quien ya lo tenia."""
    token = biblioteca.compartir(grupo.id, Permiso.LECTURA).share_token

    despues = biblioteca.compartir(grupo.id, Permiso.ESCRITURA)

    assert despues.share_token == token
    assert despues.share_permiso is Permiso.ESCRITURA


def test_el_enlace_resuelve_al_grupo(biblioteca, grupo):
    token = biblioteca.compartir(grupo.id, Permiso.LECTURA).share_token

    assert biblioteca.grupo_por_token(token).id == grupo.id


def test_dejar_de_compartir_invalida_el_enlace(biblioteca, grupo):
    token = biblioteca.compartir(grupo.id, Permiso.LECTURA).share_token

    biblioteca.dejar_de_compartir(grupo.id)

    assert biblioteca.grupo_por_token(token) is None
    assert biblioteca.grupo(grupo.id).compartido is False


def test_un_token_inventado_no_abre_nada(biblioteca, grupo):
    biblioteca.compartir(grupo.id, Permiso.LECTURA)

    assert biblioteca.grupo_por_token("token-inventado") is None
    assert biblioteca.grupo_por_token("") is None


# ---------------------------------------------------------------------------
# Temas
# ---------------------------------------------------------------------------


def test_los_temas_conservan_el_orden_de_creacion(biblioteca, grupo):
    for nombre in ("Unidad 1", "Unidad 2", "Unidad 3"):
        biblioteca.crear_tema(grupo.id, nombre)

    assert [t.nombre for t in biblioteca.listar_temas(grupo.id)] == [
        "Unidad 1", "Unidad 2", "Unidad 3"
    ]


def test_borrar_un_tema_no_borra_su_material_ni_sus_notas(biblioteca, grupo):
    """Quien elimina una seccion esta reorganizando, no tirando su material."""
    tema = biblioteca.crear_tema(grupo.id, "Unidad 1")
    biblioteca.guardar_material(grupo.id, "guia.pdf", "ejercicios", tema_id=tema.id)
    biblioteca.crear_nota(grupo.id, "Dudas", "repasar limites", tema_id=tema.id)

    biblioteca.borrar_tema(tema.id)

    materiales = biblioteca.listar_materiales(grupo.id)
    notas = biblioteca.listar_notas(grupo.id)
    assert len(materiales) == 1 and materiales[0].tema_id is None
    assert len(notas) == 1 and notas[0].tema_id is None


# ---------------------------------------------------------------------------
# Material
# ---------------------------------------------------------------------------


def test_el_material_general_vale_para_todos_los_temas(biblioteca, grupo):
    """El programa de la materia es relevante en cualquier unidad."""
    tema = biblioteca.crear_tema(grupo.id, "Unidad 3")
    biblioteca.guardar_material(
        grupo.id, "programa.pdf", "plan de la materia", tipo=TipoMaterial.PROGRAMA
    )
    biblioteca.guardar_material(
        grupo.id, "guia3.pdf", "ejercicios de la 3", tema_id=tema.id
    )

    nombres = [m.filename for m in biblioteca.listar_materiales(grupo.id, tema_id=tema.id)]

    assert set(nombres) == {"programa.pdf", "guia3.pdf"}


def test_el_material_de_otro_tema_no_se_cuela(biblioteca, grupo):
    uno = biblioteca.crear_tema(grupo.id, "Unidad 1")
    dos = biblioteca.crear_tema(grupo.id, "Unidad 2")
    biblioteca.guardar_material(grupo.id, "guia1.pdf", "ejercicios", tema_id=uno.id)

    materiales = biblioteca.listar_materiales(grupo.id, tema_id=dos.id)

    assert [m.filename for m in materiales] == []


def test_el_resumen_del_material_no_se_desborda(biblioteca, grupo):
    material = biblioteca.guardar_material(grupo.id, "largo.pdf", "palabra " * 500)

    assert len(material.resumen) <= 203


# ---------------------------------------------------------------------------
# Notas
# ---------------------------------------------------------------------------


def test_una_nota_se_edita_y_conserva_lo_no_tocado(biblioteca, grupo):
    nota = biblioteca.crear_nota(grupo.id, "Dudas del parcial", "revisar la 4.2")

    editada = biblioteca.actualizar_nota(nota.id, contenido="revisar la 4.2 y la 4.7")

    assert editada.titulo == "Dudas del parcial"
    assert editada.contenido == "revisar la 4.2 y la 4.7"


def test_editar_una_nota_inexistente_no_revienta(biblioteca):
    assert biblioteca.actualizar_nota("noexiste", titulo="x") is None


# ---------------------------------------------------------------------------
# Borrado en cascada
# ---------------------------------------------------------------------------


def test_borrar_el_grupo_se_lleva_temas_material_y_notas(biblioteca, grupo):
    tema = biblioteca.crear_tema(grupo.id, "Unidad 1")
    biblioteca.guardar_material(grupo.id, "programa.pdf", "plan", tema_id=tema.id)
    biblioteca.crear_nota(grupo.id, "Dudas", "algo")

    assert biblioteca.borrar_grupo(grupo.id) is True

    assert biblioteca.grupo(grupo.id) is None
    assert biblioteca.listar_temas(grupo.id) == []
    assert biblioteca.listar_materiales(grupo.id) == []
    assert biblioteca.listar_notas(grupo.id) == []


def test_borrar_un_grupo_que_no_existe_devuelve_false(biblioteca):
    assert biblioteca.borrar_grupo("noexiste") is False


def test_los_datos_sobreviven_a_reabrir_la_base(tmp_path):
    """Los grupos tienen que aguantar un reinicio del servidor."""
    ruta = tmp_path / "prueba.db"
    primera = Biblioteca(ruta)
    grupo = primera.crear_grupo("Comision 3", "Analisis Matematico I")
    primera.crear_tema(grupo.id, "Unidad 1")

    segunda = Biblioteca(ruta)

    assert segunda.grupo(grupo.id).materia == "Analisis Matematico I"
    assert len(segunda.listar_temas(grupo.id)) == 1
