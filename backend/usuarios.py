"""Cuentas y sesiones.

Hasta ahora la app no sabia quien era nadie: cualquiera que alcanzara el backend
podia leer cualquier grupo. Con el alcance publico eso dejo de ser una
limitacion aceptable y paso a ser un bloqueante, porque choca de frente con la
unica restriccion innegociable del producto: los apuntes son privados por
defecto.

Dos formas de entrar, y una cuenta puede tener las dos:

* **Correo y contrasena.** La contrasena se guarda con Argon2id, que es lo que
  recomienda OWASP, y nunca en claro ni reversible.
* **Google.** No guardamos contrasena ninguna; guardamos el `sub`, el
  identificador estable que Google da a esa persona. No se usa el correo como
  llave para esto: Google permite cambiarlo y el `sub` no cambia nunca.

La sesion es un testigo opaco guardado en una tabla, no un JWT. Es mas simple y,
sobre todo, se puede revocar: cerrar sesion borra la fila y el testigo deja de
valer en el acto. Un JWT firmado seguiria siendo valido hasta que caduque.
"""

from __future__ import annotations

import re
import secrets
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError

from .models import Usuario

# Sin usar, la sesion caduca al mes. Es un equilibrio: mas corto obliga a
# entrar cada dos por tres en un producto que se usa una vez por semana, y mas
# largo deja testigos vivos demasiado tiempo en ordenadores compartidos.
DIAS_DE_SESION = 30

MINIMO_DE_CONTRASENA = 10

# Deliberadamente laxo. Validar correos con una expresion regular estricta
# rechaza direcciones validas; lo unico que se comprueba aqui es que la forma
# sea plausible. Quien de verdad valida el correo es el correo mismo, el dia que
# haya envio.
_CORREO = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS usuarios (
    id             TEXT PRIMARY KEY,
    email          TEXT NOT NULL,
    nombre         TEXT NOT NULL DEFAULT '',
    password_hash  TEXT,
    google_sub     TEXT,
    created_at     TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_usuarios_email ON usuarios (email);
CREATE UNIQUE INDEX IF NOT EXISTS idx_usuarios_google
    ON usuarios (google_sub) WHERE google_sub IS NOT NULL;

CREATE TABLE IF NOT EXISTS sesiones (
    token       TEXT PRIMARY KEY,
    usuario_id  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    ultimo_uso  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sesiones_usuario ON sesiones (usuario_id);

CREATE TABLE IF NOT EXISTS estados_oauth (
    estado      TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL
);
"""


class ErrorDeCuenta(Exception):
    """Fallo que se le puede contar al usuario tal cual."""


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def normalizar_correo(email: str) -> str:
    return email.strip().lower()


class Usuarios:
    """Cuentas y sesiones sobre la misma base SQLite que todo lo demas."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._hasher = PasswordHasher()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    # -- Alta ---------------------------------------------------------------

    def hay_cuentas(self) -> bool:
        with self._lock, self._connect() as conn:
            return conn.execute("SELECT 1 FROM usuarios LIMIT 1").fetchone() is not None

    def crear(
        self,
        email: str,
        *,
        password: str | None = None,
        nombre: str = "",
        google_sub: str | None = None,
    ) -> Usuario:
        """Da de alta una cuenta. Falla si el correo ya existe."""
        correo = normalizar_correo(email)
        if not _CORREO.match(correo):
            raise ErrorDeCuenta("Ese correo no tiene forma de correo.")
        if password is not None:
            _comprobar_contrasena(password)

        usuario = Usuario(
            id=uuid.uuid4().hex[:12],
            email=correo,
            nombre=nombre.strip() or correo.split("@")[0],
            tiene_password=password is not None,
            tiene_google=google_sub is not None,
        )
        cifrada = self._hasher.hash(password) if password is not None else None

        with self._lock, self._connect() as conn:
            try:
                conn.execute(
                    "INSERT INTO usuarios (id, email, nombre, password_hash,"
                    " google_sub, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        usuario.id, usuario.email, usuario.nombre, cifrada,
                        google_sub, usuario.created_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ErrorDeCuenta(
                    "Ya hay una cuenta con ese correo. Entra con ella."
                ) from exc
        return usuario

    # -- Consulta -----------------------------------------------------------

    def por_id(self, usuario_id: str) -> Usuario | None:
        return self._uno("SELECT * FROM usuarios WHERE id = ?", (usuario_id,))

    def por_email(self, email: str) -> Usuario | None:
        return self._uno(
            "SELECT * FROM usuarios WHERE email = ?", (normalizar_correo(email),)
        )

    def por_google(self, google_sub: str) -> Usuario | None:
        return self._uno(
            "SELECT * FROM usuarios WHERE google_sub = ?", (google_sub,)
        )

    def _uno(self, consulta: str, parametros: tuple) -> Usuario | None:
        with self._lock, self._connect() as conn:
            fila = conn.execute(consulta, parametros).fetchone()
        return _a_usuario(fila) if fila else None

    # -- Credenciales -------------------------------------------------------

    def verificar(self, email: str, password: str) -> Usuario | None:
        """Devuelve la cuenta si la contrasena es correcta, o `None`.

        Nunca distingue "no existe esa cuenta" de "la contrasena no es esa": si
        lo hiciera, cualquiera podria averiguar quien tiene cuenta probando
        correos.
        """
        with self._lock, self._connect() as conn:
            fila = conn.execute(
                "SELECT * FROM usuarios WHERE email = ?", (normalizar_correo(email),)
            ).fetchone()

        if fila is None or not fila["password_hash"]:
            # Se gasta el mismo tiempo que en una comprobacion real para no
            # delatar por el reloj que ese correo no tiene cuenta.
            self._hasher.hash(password)
            return None

        try:
            self._hasher.verify(fila["password_hash"], password)
        except (VerifyMismatchError, VerificationError):
            return None

        if self._hasher.check_needs_rehash(fila["password_hash"]):
            self._guardar_hash(fila["id"], self._hasher.hash(password))
        return _a_usuario(fila)

    def cambiar_contrasena(self, usuario_id: str, password: str) -> None:
        _comprobar_contrasena(password)
        self._guardar_hash(usuario_id, self._hasher.hash(password))

    def _guardar_hash(self, usuario_id: str, cifrada: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE usuarios SET password_hash = ? WHERE id = ?",
                (cifrada, usuario_id),
            )

    def vincular_google(self, usuario_id: str, google_sub: str) -> None:
        """Ata una cuenta de Google a una cuenta que ya existe.

        Es lo que ocurre cuando alguien se registro con correo y contrasena y
        despues entra con Google usando el mismo correo: en vez de crear una
        segunda cuenta con los mismos apuntes divididos, se unen las dos formas
        de entrar en la que ya tenia.
        """
        with self._lock, self._connect() as conn:
            try:
                conn.execute(
                    "UPDATE usuarios SET google_sub = ? WHERE id = ?",
                    (google_sub, usuario_id),
                )
            except sqlite3.IntegrityError as exc:
                raise ErrorDeCuenta(
                    "Esa cuenta de Google ya esta ligada a otro usuario."
                ) from exc

    # -- Sesiones -----------------------------------------------------------

    def abrir_sesion(self, usuario_id: str) -> str:
        token = secrets.token_urlsafe(32)
        marca = _ahora().isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO sesiones (token, usuario_id, created_at, ultimo_uso)"
                " VALUES (?, ?, ?, ?)",
                (token, usuario_id, marca, marca),
            )
        return token

    def usuario_de_sesion(self, token: str) -> Usuario | None:
        """Resuelve un testigo. Renueva su vigencia si sigue vivo."""
        if not token:
            return None
        limite = (_ahora() - timedelta(days=DIAS_DE_SESION)).isoformat()
        with self._lock, self._connect() as conn:
            # Las caducadas se van en cuanto se tropieza con ellas: no hace
            # falta una tarea de limpieza para una tabla de este tamano.
            conn.execute("DELETE FROM sesiones WHERE ultimo_uso < ?", (limite,))
            fila = conn.execute(
                "SELECT u.* FROM sesiones s JOIN usuarios u ON u.id = s.usuario_id"
                " WHERE s.token = ?",
                (token,),
            ).fetchone()
            if fila is None:
                return None
            conn.execute(
                "UPDATE sesiones SET ultimo_uso = ? WHERE token = ?",
                (_ahora().isoformat(), token),
            )
        return _a_usuario(fila)

    def cerrar_sesion(self, token: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM sesiones WHERE token = ?", (token,))

    def cerrar_todas(self, usuario_id: str) -> None:
        """Echa a la cuenta de todos los dispositivos."""
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM sesiones WHERE usuario_id = ?", (usuario_id,))

    # -- Estado del flujo de Google ----------------------------------------

    def nuevo_estado(self) -> str:
        """Crea el `state` que viaja a Google y vuelve con el codigo.

        Sirve para que el backend solo acepte codigos de un flujo que empezo
        aqui, en vez de cualquier cosa que llegue con la forma correcta. Es de
        un solo uso y caduca.
        """
        estado = secrets.token_urlsafe(24)
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO estados_oauth (estado, created_at) VALUES (?, ?)",
                (estado, _ahora().isoformat()),
            )
        return estado

    def consumir_estado(self, estado: str) -> bool:
        """Gasta un estado. Devuelve `False` si no existia o ya caduco."""
        if not estado:
            return False
        limite = (_ahora() - timedelta(minutes=MINUTOS_DE_ESTADO)).isoformat()
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM estados_oauth WHERE created_at < ?", (limite,))
            cursor = conn.execute(
                "DELETE FROM estados_oauth WHERE estado = ?", (estado,)
            )
        return cursor.rowcount > 0


# Cuanto vive un estado de OAuth sin usarse. Es el tiempo que tarda alguien en
# elegir cuenta en la pantalla de Google, no mas.
MINUTOS_DE_ESTADO = 10


def _comprobar_contrasena(password: str) -> None:
    """Un solo requisito: longitud.

    Exigir mayuscula, numero y simbolo produce contrasenas peores y mas dificiles
    de recordar; la longitud es lo que de verdad cuesta de romper. Es tambien lo
    que recomienda el NIST desde 2017.
    """
    if len(password) < MINIMO_DE_CONTRASENA:
        raise ErrorDeCuenta(
            f"La contrasena necesita al menos {MINIMO_DE_CONTRASENA} caracteres. "
            "Una frase que recuerdes vale mas que un jeroglifico corto."
        )


def _a_usuario(fila: sqlite3.Row) -> Usuario:
    return Usuario(
        id=fila["id"],
        email=fila["email"],
        nombre=fila["nombre"],
        created_at=datetime.fromisoformat(fila["created_at"]),
        tiene_password=bool(fila["password_hash"]),
        tiene_google=bool(fila["google_sub"]),
    )
