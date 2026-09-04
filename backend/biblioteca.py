"""Persistencia de la biblioteca: grupos, temas, material y notas.

Vive en la misma base SQLite que los trabajos, pero en su propia clase: los
trabajos son cola de procesado y esto es organizacion del usuario. Mezclarlos
haria que `JobStore` creciera sin relacion con lo que hace.

Sobre el borrado: al eliminar un grupo se borra en cascada lo que cuelga de el
(temas, material y notas), pero **no las clases transcritas**. Una clase cuesta
dinero y tiempo de procesado; que desaparezca por reorganizar carpetas seria
desproporcionado. Se quedan sin archivar y siguen en "Mis clases".
"""

from __future__ import annotations

import secrets
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .models import Grupo, Material, Nota, Permiso, Tema, TipoMaterial

_SCHEMA = """
CREATE TABLE IF NOT EXISTS grupos (
    id             TEXT PRIMARY KEY,
    usuario_id     TEXT,
    nombre         TEXT NOT NULL,
    materia        TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    share_token    TEXT,
    share_permiso  TEXT NOT NULL DEFAULT 'lectura'
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_grupos_token
    ON grupos (share_token) WHERE share_token IS NOT NULL;

CREATE TABLE IF NOT EXISTS temas (
    id          TEXT PRIMARY KEY,
    grupo_id    TEXT NOT NULL,
    nombre      TEXT NOT NULL,
    orden       INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_temas_grupo ON temas (grupo_id, orden);

CREATE TABLE IF NOT EXISTS materiales (
    id          TEXT PRIMARY KEY,
    grupo_id    TEXT NOT NULL,
    tema_id     TEXT,
    filename    TEXT NOT NULL,
    tipo        TEXT NOT NULL,
    paginas     INTEGER,
    texto       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_materiales_grupo ON materiales (grupo_id);

CREATE TABLE IF NOT EXISTS notas (
    id          TEXT PRIMARY KEY,
    grupo_id    TEXT NOT NULL,
    tema_id     TEXT,
    titulo      TEXT NOT NULL,
    contenido   TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notas_grupo ON notas (grupo_id);
CREATE INDEX IF NOT EXISTS idx_grupos_usuario ON grupos (usuario_id);
"""


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id() -> str:
    return uuid.uuid4().hex[:12]


class Biblioteca:
    """Grupos, temas, material y notas sobre SQLite."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            # `CREATE TABLE IF NOT EXISTS` no toca una tabla que ya existe, asi
            # que a una base anterior a las cuentas hay que anadirle la columna
            # del dueno a mano. Los grupos que ya habia se quedan sin dueno
            # hasta que se cree la primera cuenta, que los adopta.
            columnas = {
                f["name"] for f in conn.execute("PRAGMA table_info(grupos)")
            }
            if columnas and "usuario_id" not in columnas:
                conn.execute("ALTER TABLE grupos ADD COLUMN usuario_id TEXT")
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    # -- Grupos -------------------------------------------------------------

    def crear_grupo(
        self, nombre: str, materia: str, usuario_id: str | None = None
    ) -> Grupo:
        grupo = Grupo(
            id=_id(),
            nombre=nombre.strip(),
            materia=materia.strip(),
            usuario_id=usuario_id,
        )
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO grupos (id, usuario_id, nombre, materia, created_at,"
                " updated_at, share_token, share_permiso)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    grupo.id, grupo.usuario_id, grupo.nombre, grupo.materia,
                    grupo.created_at.isoformat(), grupo.updated_at.isoformat(),
                    None, grupo.share_permiso.value,
                ),
            )
        return grupo

    def grupo(self, grupo_id: str) -> Grupo | None:
        with self._lock, self._connect() as conn:
            fila = conn.execute(
                "SELECT * FROM grupos WHERE id = ?", (grupo_id,)
            ).fetchone()
        return _a_grupo(fila) if fila else None

    def grupo_por_token(self, token: str) -> Grupo | None:
        """Resuelve un enlace compartido. Devuelve `None` si el token no vale."""
        if not token:
            return None
        with self._lock, self._connect() as conn:
            fila = conn.execute(
                "SELECT * FROM grupos WHERE share_token = ?", (token,)
            ).fetchone()
        return _a_grupo(fila) if fila else None

    def listar_grupos(self, usuario_id: str | None = None) -> list[Grupo]:
        """Los grupos de una persona. Sin dueno, los que no tienen ninguno."""
        with self._lock, self._connect() as conn:
            filas = conn.execute(
                "SELECT * FROM grupos WHERE usuario_id IS ? ORDER BY materia, nombre",
                (usuario_id,),
            ).fetchall()
        return [_a_grupo(f) for f in filas]

    def adoptar_huerfanos(self, usuario_id: str) -> int:
        """Da dueno a los grupos que se crearon antes de que hubiera cuentas."""
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "UPDATE grupos SET usuario_id = ? WHERE usuario_id IS NULL",
                (usuario_id,),
            )
        return cursor.rowcount

    def renombrar_grupo(self, grupo_id: str, nombre: str, materia: str) -> Grupo | None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE grupos SET nombre = ?, materia = ?, updated_at = ?"
                " WHERE id = ?",
                (nombre.strip(), materia.strip(), _ahora(), grupo_id),
            )
        return self.grupo(grupo_id)

    def compartir(self, grupo_id: str, permiso: Permiso) -> Grupo | None:
        """Genera (o reutiliza) el enlace y fija lo que permite hacer.

        El token se conserva al cambiar el permiso: quien ya tenga el enlace
        no se queda fuera solo porque el autor pase de lectura a escritura.
        """
        grupo = self.grupo(grupo_id)
        if grupo is None:
            return None

        token = grupo.share_token or secrets.token_urlsafe(16)
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE grupos SET share_token = ?, share_permiso = ?,"
                " updated_at = ? WHERE id = ?",
                (token, permiso.value, _ahora(), grupo_id),
            )
        return self.grupo(grupo_id)

    def dejar_de_compartir(self, grupo_id: str) -> Grupo | None:
        """Invalida el enlace. Quien lo tuviera deja de poder entrar."""
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE grupos SET share_token = NULL, updated_at = ? WHERE id = ?",
                (_ahora(), grupo_id),
            )
        return self.grupo(grupo_id)

    def borrar_grupo(self, grupo_id: str) -> bool:
        """Borra el grupo y lo que cuelga de el. Las clases no se tocan."""
        with self._lock, self._connect() as conn:
            cursor = conn.execute("DELETE FROM grupos WHERE id = ?", (grupo_id,))
            conn.execute("DELETE FROM temas WHERE grupo_id = ?", (grupo_id,))
            conn.execute("DELETE FROM materiales WHERE grupo_id = ?", (grupo_id,))
            conn.execute("DELETE FROM notas WHERE grupo_id = ?", (grupo_id,))
        return cursor.rowcount > 0

    # -- Temas --------------------------------------------------------------

    def crear_tema(self, grupo_id: str, nombre: str) -> Tema:
        siguiente = len(self.listar_temas(grupo_id))
        tema = Tema(id=_id(), grupo_id=grupo_id, nombre=nombre.strip(), orden=siguiente)
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO temas (id, grupo_id, nombre, orden, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (tema.id, tema.grupo_id, tema.nombre, tema.orden,
                 tema.created_at.isoformat()),
            )
        return tema

    def listar_temas(self, grupo_id: str) -> list[Tema]:
        with self._lock, self._connect() as conn:
            filas = conn.execute(
                "SELECT * FROM temas WHERE grupo_id = ? ORDER BY orden, created_at",
                (grupo_id,),
            ).fetchall()
        return [_a_tema(f) for f in filas]

    def tema(self, tema_id: str) -> Tema | None:
        with self._lock, self._connect() as conn:
            fila = conn.execute(
                "SELECT * FROM temas WHERE id = ?", (tema_id,)
            ).fetchone()
        return _a_tema(fila) if fila else None

    def borrar_tema(self, tema_id: str) -> bool:
        """Borra el tema. El material y las notas que colgaban quedan sueltos.

        Se prefiere dejarlos huerfanos en el grupo a borrarlos: quien elimina
        una seccion esta reorganizando, no tirando su material.
        """
        with self._lock, self._connect() as conn:
            cursor = conn.execute("DELETE FROM temas WHERE id = ?", (tema_id,))
            conn.execute(
                "UPDATE materiales SET tema_id = NULL WHERE tema_id = ?", (tema_id,)
            )
            conn.execute(
                "UPDATE notas SET tema_id = NULL WHERE tema_id = ?", (tema_id,)
            )
        return cursor.rowcount > 0

    # -- Material -----------------------------------------------------------

    def guardar_material(
        self,
        grupo_id: str,
        filename: str,
        texto: str,
        *,
        tema_id: str | None = None,
        tipo: TipoMaterial = TipoMaterial.MATERIAL,
        paginas: int | None = None,
    ) -> Material:
        material = Material(
            id=_id(), grupo_id=grupo_id, tema_id=tema_id, filename=filename,
            tipo=tipo, paginas=paginas, texto=texto,
        )
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO materiales (id, grupo_id, tema_id, filename, tipo,"
                " paginas, texto, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (material.id, material.grupo_id, material.tema_id,
                 material.filename, material.tipo.value, material.paginas,
                 material.texto, material.created_at.isoformat()),
            )
        return material

    def listar_materiales(
        self, grupo_id: str, *, tema_id: str | None = None
    ) -> list[Material]:
        """Material del grupo. Con `tema_id`, el de ese tema y el del grupo.

        El material general (el que no cuelga de ningun tema) es relevante en
        todos los temas: el programa de la materia vale para toda la cursada.
        """
        consulta = "SELECT * FROM materiales WHERE grupo_id = ?"
        parametros: list[object] = [grupo_id]
        if tema_id is not None:
            consulta += " AND (tema_id = ? OR tema_id IS NULL)"
            parametros.append(tema_id)
        consulta += " ORDER BY created_at"

        with self._lock, self._connect() as conn:
            filas = conn.execute(consulta, parametros).fetchall()
        return [_a_material(f) for f in filas]

    def material(self, material_id: str) -> Material | None:
        with self._lock, self._connect() as conn:
            fila = conn.execute(
                "SELECT * FROM materiales WHERE id = ?", (material_id,)
            ).fetchone()
        return _a_material(fila) if fila else None

    def borrar_material(self, material_id: str) -> bool:
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM materiales WHERE id = ?", (material_id,)
            )
        return cursor.rowcount > 0

    # -- Notas --------------------------------------------------------------

    def crear_nota(
        self, grupo_id: str, titulo: str, contenido: str = "",
        *, tema_id: str | None = None,
    ) -> Nota:
        nota = Nota(
            id=_id(), grupo_id=grupo_id, tema_id=tema_id,
            titulo=titulo.strip(), contenido=contenido,
        )
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO notas (id, grupo_id, tema_id, titulo, contenido,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (nota.id, nota.grupo_id, nota.tema_id, nota.titulo,
                 nota.contenido, nota.created_at.isoformat(),
                 nota.updated_at.isoformat()),
            )
        return nota

    def actualizar_nota(
        self, nota_id: str, *, titulo: str | None = None,
        contenido: str | None = None,
    ) -> Nota | None:
        nota = self.nota(nota_id)
        if nota is None:
            return None

        nuevo_titulo = titulo if titulo is not None else nota.titulo
        nuevo_contenido = contenido if contenido is not None else nota.contenido
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE notas SET titulo = ?, contenido = ?, updated_at = ?"
                " WHERE id = ?",
                (nuevo_titulo.strip(), nuevo_contenido, _ahora(), nota_id),
            )
        return self.nota(nota_id)

    def nota(self, nota_id: str) -> Nota | None:
        with self._lock, self._connect() as conn:
            fila = conn.execute(
                "SELECT * FROM notas WHERE id = ?", (nota_id,)
            ).fetchone()
        return _a_nota(fila) if fila else None

    def listar_notas(
        self, grupo_id: str, *, tema_id: str | None = None
    ) -> list[Nota]:
        consulta = "SELECT * FROM notas WHERE grupo_id = ?"
        parametros: list[object] = [grupo_id]
        if tema_id is not None:
            consulta += " AND tema_id = ?"
            parametros.append(tema_id)
        consulta += " ORDER BY updated_at DESC"

        with self._lock, self._connect() as conn:
            filas = conn.execute(consulta, parametros).fetchall()
        return [_a_nota(f) for f in filas]

    def borrar_nota(self, nota_id: str) -> bool:
        with self._lock, self._connect() as conn:
            cursor = conn.execute("DELETE FROM notas WHERE id = ?", (nota_id,))
        return cursor.rowcount > 0


# ---------------------------------------------------------------------------
# Conversion de filas a modelos
# ---------------------------------------------------------------------------


def _a_grupo(fila: sqlite3.Row) -> Grupo:
    return Grupo(
        id=fila["id"], nombre=fila["nombre"], materia=fila["materia"],
        usuario_id=fila["usuario_id"],
        created_at=fila["created_at"], updated_at=fila["updated_at"],
        share_token=fila["share_token"],
        share_permiso=Permiso(fila["share_permiso"]),
    )


def _a_tema(fila: sqlite3.Row) -> Tema:
    return Tema(
        id=fila["id"], grupo_id=fila["grupo_id"], nombre=fila["nombre"],
        orden=fila["orden"], created_at=fila["created_at"],
    )


def _a_material(fila: sqlite3.Row) -> Material:
    return Material(
        id=fila["id"], grupo_id=fila["grupo_id"], tema_id=fila["tema_id"],
        filename=fila["filename"], tipo=TipoMaterial(fila["tipo"]),
        paginas=fila["paginas"], texto=fila["texto"],
        created_at=fila["created_at"],
    )


def _a_nota(fila: sqlite3.Row) -> Nota:
    return Nota(
        id=fila["id"], grupo_id=fila["grupo_id"], tema_id=fila["tema_id"],
        titulo=fila["titulo"], contenido=fila["contenido"],
        created_at=fila["created_at"], updated_at=fila["updated_at"],
    )
