"""Cuanto ha consumido cada cuenta.

Esta decidido que se va a cobrar, y para cobrar hay que saber que gasto cada
persona. Con las cuentas ya se sabe de quien es cada clase, que era el requisito
previo; lo que faltaba es contar.

Se hace ahora, con una sola cuenta y ninguna factura, porque reconstruirlo hacia
atras es imposible: los minutos de audio de una clase que ya se proceso se
pueden deducir, pero cuantas peticiones costo redactar sus apuntes no lo sabe
nadie una vez ha pasado.

Dos propiedades que gobiernan el diseno:

* **Es un libro de cuentas, no un dato derivado.** Se apunta lo que ocurrio, con
  su fecha, y no se recalcula desde los trabajos. Por eso borrar una clase no
  borra lo que costo: el proveedor ya lo cobro.
* **Se apunta tambien lo que fallo.** Si la anotacion se cae a mitad de una
  clase larga, las peticiones que llegaron a salir se pagaron igual. Un
  contador que solo mira los exitos da de menos justo en los meses malos.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .models import Gasto, PeriodoDeConsumo, ResumenDeConsumo

TRANSCRIPCION = "transcripcion"
ANOTACION = "anotacion"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS consumo (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Nulo solo en las clases que existian antes de que hubiera cuentas. La
    -- fila se guarda igual: es gasto real, aunque todavia no tenga dueno.
    usuario_id          TEXT,
    job_id              TEXT,
    momento             TEXT NOT NULL,
    concepto            TEXT NOT NULL,
    proveedor           TEXT,
    modelo              TEXT,
    segundos_de_audio   REAL NOT NULL DEFAULT 0,
    peticiones          INTEGER NOT NULL DEFAULT 0,
    caracteres_entrada  INTEGER NOT NULL DEFAULT 0,
    caracteres_salida   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_consumo_usuario ON consumo (usuario_id, momento);
"""


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _principio_del_mes(momento: datetime) -> datetime:
    """El dia 1 a las cero horas.

    En UTC, como todas las fechas de este proyecto. Para quien vive en UTC-3 eso
    corre la frontera del mes tres horas; en un panel informativo no importa, y
    el dia que haya facturas de verdad habra que decidir la zona horaria de
    facturacion, que es una decision de negocio y no de codigo.
    """
    return momento.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


class Consumo:
    """El libro de cuentas, sobre el mismo fichero SQLite que lo demas."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    # -- Apuntar ------------------------------------------------------------

    def _apuntar(self, **campos) -> None:
        campos.setdefault("momento", _ahora().isoformat())
        columnas = ", ".join(campos)
        huecos = ", ".join("?" for _ in campos)
        with self._lock, self._connect() as conn:
            conn.execute(
                f"INSERT INTO consumo ({columnas}) VALUES ({huecos})",
                tuple(campos.values()),
            )

    def transcripcion(
        self,
        usuario_id: str | None,
        job_id: str,
        proveedor: str,
        segundos: float | None,
    ) -> None:
        """Apunta una clase transcrita. Se paga por minuto de audio."""
        self._apuntar(
            usuario_id=usuario_id,
            job_id=job_id,
            concepto=TRANSCRIPCION,
            proveedor=proveedor,
            segundos_de_audio=float(segundos or 0.0),
        )

    def anotacion(
        self,
        usuario_id: str | None,
        job_id: str,
        proveedor: str,
        modelo: str,
        gasto: Gasto,
    ) -> None:
        """Apunta lo que costo redactar unos apuntes.

        No apunta nada si no llego a salir ninguna peticion: una anotacion que
        fallo antes de empezar no costo nada, y una fila de ceros solo ensucia.
        """
        if not gasto.peticiones:
            return
        self._apuntar(
            usuario_id=usuario_id,
            job_id=job_id,
            concepto=ANOTACION,
            proveedor=proveedor,
            modelo=modelo,
            peticiones=gasto.peticiones,
            caracteres_entrada=gasto.caracteres_entrada,
            caracteres_salida=gasto.caracteres_salida,
        )

    # -- Consultar ----------------------------------------------------------

    def _periodo(
        self, usuario_id: str, desde: datetime | None, hasta: datetime
    ) -> PeriodoDeConsumo:
        condicion = "usuario_id = ?"
        parametros: list = [usuario_id]
        if desde is not None:
            condicion += " AND momento >= ?"
            parametros.append(desde.isoformat())

        with self._lock, self._connect() as conn:
            fila = conn.execute(
                "SELECT"
                " COUNT(*) FILTER (WHERE concepto = ?) AS clases,"
                " COALESCE(SUM(segundos_de_audio), 0) AS segundos,"
                " COALESCE(SUM(peticiones), 0) AS peticiones,"
                " COALESCE(SUM(caracteres_entrada), 0) AS entrada,"
                " COALESCE(SUM(caracteres_salida), 0) AS salida"
                f" FROM consumo WHERE {condicion}",
                (TRANSCRIPCION, *parametros),
            ).fetchone()

        return PeriodoDeConsumo(
            desde=desde,
            hasta=hasta,
            clases_transcritas=fila["clases"],
            segundos_de_audio=fila["segundos"],
            peticiones_al_modelo=fila["peticiones"],
            caracteres_entrada=fila["entrada"],
            caracteres_salida=fila["salida"],
        )

    def resumen(self, usuario_id: str) -> ResumenDeConsumo:
        """Lo gastado este mes y desde siempre.

        El mes es lo que interesa para una factura; el total, para saber si la
        cuenta merece la pena. Van juntos en la misma respuesta porque quien
        mira lo uno mira lo otro, y son dos sumas sobre una tabla pequena.
        """
        ahora = _ahora()
        return ResumenDeConsumo(
            mes=self._periodo(usuario_id, _principio_del_mes(ahora), ahora),
            total=self._periodo(usuario_id, None, ahora),
        )
