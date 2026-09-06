"""Un backend de mentira para probar la interfaz sin levantar el de verdad.

Responde la forma de la API real y lleva la cuenta de lo que se le pide, que es
lo que permite medir cuanto cuesta dibujar una pantalla. Lo comparten los tests
de interfaz y los de coste.
"""

from __future__ import annotations

import json
import threading
from collections import Counter
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

TESTIGO = "testigo-de-prueba"

# Lo que se puede pedir sin haber entrado, igual que `RUTAS_ABIERTAS` en el
# backend de verdad. `/api/auth/yo` queda fuera a proposito: alli tambien pide
# identidad, y es justo la ruta que decide si un testigo guardado sigue
# valiendo.
RUTAS_SIN_SESION = ("/api/health", "/api/auth/entrar", "/api/auth/registro",
                    "/api/auth/google", "/api/compartido/")

USUARIO = {
    "id": "u1",
    "email": "alumno@unam.edu.ar",
    "nombre": "Alumno",
    "created_at": "2026-09-01T00:00:00+00:00",
    "tiene_password": True,
    "tiene_google": False,
}

RUTAS_CONOCIDAS = {
    "api", "jobs", "grupos", "temas", "materiales", "notas", "health",
    "notes", "transcript", "compartido", "ubicacion", "titulo", "clases",
    "auth", "yo", "google", "entrar", "registro", "salir",
}


def normalizar(ruta: str) -> str:
    """Convierte `/api/jobs/abc123` en `/api/jobs/{id}` para poder contarla."""
    partes = ruta.strip("/").split("/")
    return "/" + "/".join(p if p in RUTAS_CONOCIDAS else "{id}" for p in partes)


class BackendDeMentira:
    """Sirve la API en un puerto libre y cuenta las peticiones que recibe."""

    def __init__(
        self,
        clases: int = 12,
        grupos: int = 1,
        testigo: str = TESTIGO,
        dias_de_sesion: int | None = 30,
    ) -> None:
        self.peticiones: Counter = Counter()
        self.testigo = testigo
        # `None` imita a un backend viejo que todavia no informa de esto.
        self.dias_de_sesion = dias_de_sesion
        ahora = datetime.now(timezone.utc)

        self.grupos = [
            {
                "id": f"g{i}",
                "nombre": f"Comision {i + 1}",
                "materia": "Analisis Matematico I" if i == 0 else f"Materia {i}",
                "created_at": ahora.isoformat(),
                "updated_at": ahora.isoformat(),
                "usuario_id": USUARIO["id"],
                "share_token": "un-token" if i == 0 else None,
                "share_permiso": "lectura",
            }
            for i in range(grupos)
        ]
        self.trabajos = [
            {
                "id": f"j{i}",
                "filename": "clase_larga.wav",
                "titulo": None,
                "status": "completed",
                "created_at": (ahora - timedelta(days=i)).isoformat(),
                "audio_duration_seconds": 5400.0,
                "error": None,
                "usuario_id": USUARIO["id"],
                "grupo_id": "g0" if i % 2 else None,
                "tema_id": None,
                # Solo la primera lleva los apuntes traducidos: asi un test
                # puede comprobar que se distingue de las demas en la lista.
                "idioma_apuntes": "en" if i == 0 else None,
            }
            for i in range(clases)
        ]

        contador = self.peticiones
        cuerpo = self._cuerpo
        testigo_bueno = self.testigo

        class Manejador(BaseHTTPRequestHandler):
            def log_message(self, *_args):  # sin ruido en la salida del test
                pass

            def _servir(self):
                ruta = urlparse(self.path).path
                contador[normalizar(ruta)] += 1
                if ruta.startswith(RUTAS_SIN_SESION):
                    self._responder(200, cuerpo(ruta))
                elif self._testigo() == testigo_bueno:
                    self._responder(200, cuerpo(ruta))
                else:
                    self._responder(401, {"detail": "Necesitas entrar en tu cuenta."})

            def _testigo(self) -> str:
                tipo, _, valor = self.headers.get("Authorization", "").partition(" ")
                return valor.strip() if tipo.lower() == "bearer" else ""

            def _responder(self, codigo: int, cuerpo_json) -> None:
                datos = json.dumps(cuerpo_json).encode()
                self.send_response(codigo)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(datos)))
                self.end_headers()
                self.wfile.write(datos)

            do_GET = _servir
            do_POST = _servir

        self._servidor = ThreadingHTTPServer(("127.0.0.1", 0), Manejador)
        threading.Thread(target=self._servidor.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self._servidor.server_port}"

    def _cuerpo(self, ruta: str):
        if ruta == "/api/health":
            dias = (
                {"dias_de_sesion": self.dias_de_sesion}
                if self.dias_de_sesion is not None
                else {}
            )
            return {
                **dias,
                "status": "ok",
                "transcription_provider": "assemblyai",
                "transcription_key_configured": True,
                "annotator_provider": "gemini",
                "annotator_model": "gemini-flash-latest",
                "annotator_key_configured": True,
                "diarization_enabled": True,
                "max_upload_bytes": 5_000_000_000,
                "idiomas_de_apuntes": [
                    {"codigo": "es", "nombre": "español", "endonimo": "español"},
                    {"codigo": "en", "nombre": "inglés", "endonimo": "English"},
                ],
            }
        if ruta == "/api/auth/yo":
            return USUARIO
        if ruta == "/api/auth/google":
            return {"activo": False, "client_id": "", "url_de_autorizacion": ""}
        if ruta == "/api/jobs":
            return self.trabajos
        if ruta == "/api/grupos":
            return self.grupos
        if ruta.startswith("/api/compartido/"):
            return self._compartido(ruta)
        if ruta.startswith("/api/jobs/"):
            return self._detalle(ruta.rsplit("/", 1)[-1])
        return []

    def _compartido(self, ruta: str):
        partes = ruta.strip("/").split("/")   # api compartido <token> [...]
        cola = partes[3:]
        if not cola:
            return {**self.grupos[0], "usuario_id": None}
        if cola[0] == "clases":
            if len(cola) > 1:
                return self._detalle(cola[1])
            return [t for t in self.trabajos if t["grupo_id"] == self.grupos[0]["id"]]
        return []

    def _detalle(self, job_id: str):
        base = next(t for t in self.trabajos if t["id"] == job_id)
        return {
            **base,
            "speakers": ["Orador A"],
            "transcript_diarized": "[00:00:00] Orador A: " + "palabra " * 50,
            "notes_markdown": "# Apuntes\n\n" + "Texto. " * 50,
            "notes_editadas": None,
        }

    def cerrar(self) -> None:
        self._servidor.shutdown()
