"""Persistencia de trabajos en SQLite.

Se usa SQLite (y no memoria) para que una transcripcion de 4 horas sobreviva
a un reinicio del servidor: los trabajos largos son la norma, no la excepcion.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .models import Job, JobStatus, JobSummary

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id           TEXT PRIMARY KEY,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    status       TEXT NOT NULL,
    payload      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs (created_at DESC);
"""


class JobStore:
    """Almacen de trabajos con acceso serializado mediante un lock.

    SQLite tolera bien este patron: las escrituras son pequenas y poco
    frecuentes (un punado por trabajo), mientras que el trabajo pesado ocurre
    fuera del proceso, en el proveedor de transcripcion.
    """

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

    def create(self, job: Job) -> Job:
        """Inserta un trabajo nuevo."""
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO jobs (id, created_at, updated_at, status, payload)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    job.id,
                    job.created_at.isoformat(),
                    job.updated_at.isoformat(),
                    job.status.value,
                    job.model_dump_json(),
                ),
            )
        return job

    def get(self, job_id: str) -> Job | None:
        """Devuelve un trabajo por su id, o `None` si no existe."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return Job.model_validate_json(row["payload"]) if row else None

    def update(self, job_id: str, **fields) -> Job:
        """Actualiza campos de un trabajo y devuelve la version resultante.

        Lee, modifica y reescribe dentro del mismo lock para evitar que dos
        etapas del pipeline se pisen entre si.
        """
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"El trabajo {job_id} no existe")

            job = Job.model_validate_json(row["payload"])
            for key, value in fields.items():
                setattr(job, key, value)
            job.updated_at = datetime.now(timezone.utc)

            conn.execute(
                "UPDATE jobs SET updated_at = ?, status = ?, payload = ? WHERE id = ?",
                (
                    job.updated_at.isoformat(),
                    job.status.value,
                    job.model_dump_json(),
                    job_id,
                ),
            )
        return job

    def set_status(self, job_id: str, status: JobStatus, error: str | None = None) -> Job:
        """Atajo para cambiar el estado (y opcionalmente registrar un error)."""
        return self.update(job_id, status=status, error=error)

    def list(self, limit: int = 50) -> list[JobSummary]:
        """Lista los trabajos mas recientes."""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM jobs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()

        summaries = []
        for row in rows:
            data = json.loads(row["payload"])
            summaries.append(
                JobSummary(
                    id=data["id"],
                    filename=data["filename"],
                    status=data["status"],
                    created_at=data["created_at"],
                    audio_duration_seconds=data.get("audio_duration_seconds"),
                    error=data.get("error"),
                )
            )
        return summaries

    def delete(self, job_id: str) -> bool:
        """Borra un trabajo. Devuelve `True` si existia."""
        with self._lock, self._connect() as conn:
            cursor = conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        return cursor.rowcount > 0
