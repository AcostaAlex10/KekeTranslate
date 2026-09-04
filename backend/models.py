"""Esquemas de datos compartidos entre el pipeline, la API y el frontend."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    """Estados por los que pasa un trabajo de principio a fin."""

    PENDING = "pending"            # Creado, aun sin procesar
    UPLOADING = "uploading"        # Subiendo el audio al proveedor
    TRANSCRIBING = "transcribing"  # El proveedor esta transcribiendo
    ANNOTATING = "annotating"      # El anotador IA esta redactando los apuntes
    COMPLETED = "completed"
    FAILED = "failed"


class Utterance(BaseModel):
    """Intervencion continua de un mismo orador."""

    speaker: str = Field(description="Etiqueta del orador, p. ej. 'A' o 'Orador 1'.")
    start_ms: int = Field(description="Inicio de la intervencion en milisegundos.")
    end_ms: int = Field(description="Fin de la intervencion en milisegundos.")
    text: str

    @property
    def timestamp(self) -> str:
        """Marca de tiempo legible `HH:MM:SS` del inicio de la intervencion."""
        total_seconds, _ = divmod(self.start_ms, 1000)
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


class TranscriptionResult(BaseModel):
    """Resultado crudo devuelto por el proveedor de transcripcion."""

    provider: str
    text: str = Field(description="Transcripcion completa en texto plano.")
    utterances: list[Utterance] = Field(default_factory=list)
    audio_duration_seconds: float | None = None
    language_code: str | None = None
    provider_job_id: str | None = None

    # Los dos campos siguientes solo se rellenan al reconstruir un resultado ya
    # guardado, para volver a generar los apuntes sin transcribir otra vez. De
    # un trabajo se guarda el texto ya formateado, no las intervenciones
    # sueltas, asi que hay que poder partir de el.
    diarized_text: str | None = None
    speaker_names: list[str] = Field(default_factory=list)

    def to_diarized_text(self) -> str:
        """Formatea la transcripcion con orador y marca de tiempo por linea.

        Este es el formato que se envia al modelo: conservar los tiempos
        permite que los apuntes referencien momentos concretos de la clase.
        """
        if self.diarized_text:
            return self.diarized_text
        if not self.utterances:
            return self.text
        return "\n".join(
            f"[{u.timestamp}] {u.speaker}: {u.text}" for u in self.utterances
        )

    @property
    def speakers(self) -> list[str]:
        """Lista ordenada de oradores detectados."""
        if self.utterances:
            return sorted({u.speaker for u in self.utterances})
        return sorted(self.speaker_names)


class Job(BaseModel):
    """Trabajo de transcripcion + anotacion, tal y como se persiste."""

    id: str
    filename: str

    # Nombre puesto a mano. El del fichero no sirve para identificar una clase:
    # el movil las llama a todas igual y la grabadora integrada las nombra por
    # la fecha, asi que una lista de veinte clases sale con veinte etiquetas
    # indistinguibles. Se guarda aparte del nombre del fichero para no perder
    # de que grabacion salio.
    titulo: str | None = None

    status: JobStatus = JobStatus.PENDING
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    provider: str | None = None
    provider_job_id: str | None = None
    file_size_bytes: int | None = None
    audio_duration_seconds: float | None = None
    speakers: list[str] = Field(default_factory=list)

    transcript_text: str | None = None
    transcript_diarized: str | None = None
    notes_markdown: str | None = None

    # Version retocada a mano de los apuntes. Se guarda aparte para no pisar lo
    # que genero la IA: si mas adelante se rehacen los apuntes, las
    # correcciones propias no desaparecen sin avisar.
    notes_editadas: str | None = None

    # Quien la subio. Nulo solo en las clases que existian antes de que hubiera
    # cuentas; la primera que se cree se las queda.
    usuario_id: str | None = None

    # Ubicacion dentro de la biblioteca. Ambos pueden ser nulos: una clase
    # suelta, sin archivar, sigue siendo valida.
    grupo_id: str | None = None
    tema_id: str | None = None

    error: str | None = None

    @property
    def nombre_visible(self) -> str:
        """Como se llama esta clase en pantalla: el titulo si lo tiene."""
        return self.titulo or self.filename

    @property
    def apuntes_visibles(self) -> str | None:
        """Los apuntes que debe ver el usuario: los suyos si los edito."""
        return self.notes_editadas or self.notes_markdown

    @property
    def progress_label(self) -> str:
        """Descripcion en lenguaje natural del estado actual."""
        return {
            JobStatus.PENDING: "En cola",
            JobStatus.UPLOADING: "Subiendo el audio",
            JobStatus.TRANSCRIBING: "Transcribiendo la clase",
            JobStatus.ANNOTATING: "Escribiendo los apuntes",
            JobStatus.COMPLETED: "Lista",
            JobStatus.FAILED: "Falló",
        }[self.status]


class JobSummary(BaseModel):
    """Vista reducida de un trabajo, para el listado."""

    id: str
    filename: str
    titulo: str | None = None
    status: JobStatus
    created_at: datetime
    audio_duration_seconds: float | None = None
    error: str | None = None
    grupo_id: str | None = None
    tema_id: str | None = None

    @property
    def nombre_visible(self) -> str:
        """Como se llama esta clase en pantalla: el titulo si lo tiene."""
        return self.titulo or self.filename


# ---------------------------------------------------------------------------
# Biblioteca: grupos, temas, material y notas
# ---------------------------------------------------------------------------


class Usuario(BaseModel):
    """Una persona con cuenta. Nunca lleva la contrasena ni su hash."""

    id: str
    email: str
    nombre: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Como puede entrar. Se muestra en la interfaz para que quien entro con
    # Google sepa que todavia no tiene contrasena, y al reves.
    tiene_password: bool = False
    tiene_google: bool = False


class Permiso(str, Enum):
    """Lo que puede hacer quien entra con el enlace compartido."""

    LECTURA = "lectura"
    ESCRITURA = "escritura"


class TipoMaterial(str, Enum):
    """Para que sirve un PDF dentro del grupo.

    No es decorativo: el anotador trata el programa de la materia distinto que
    un practico, porque el programa es lo que permite situar la clase dentro
    de la planificacion.
    """

    PROGRAMA = "programa"       # Planificacion / programa de la materia
    MATERIAL = "material"       # Apuntes o lecturas del docente
    PRACTICO = "practico"       # Guia de ejercicios


class Grupo(BaseModel):
    """Carpeta de una materia: agrupa clases, temas, material y notas."""

    id: str
    nombre: str
    materia: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Quien lo creo. Es el unico que lo ve en su lista; los demas solo llegan
    # por un enlace compartido, y solo a ese grupo.
    usuario_id: str | None = None

    # Mientras no haya token, el grupo es privado. Compartir es siempre un acto
    # explicito de quien lo creo.
    share_token: str | None = None
    share_permiso: Permiso = Permiso.LECTURA

    @property
    def compartido(self) -> bool:
        return self.share_token is not None


class Tema(BaseModel):
    """Seccion dentro de un grupo, del estilo 'Unidad 3: Derivadas'."""

    id: str
    grupo_id: str
    nombre: str
    orden: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Material(BaseModel):
    """PDF adjunto a un grupo o a uno de sus temas."""

    id: str
    grupo_id: str
    tema_id: str | None = None
    filename: str
    tipo: TipoMaterial = TipoMaterial.MATERIAL
    paginas: int | None = None
    # Texto extraido del PDF. Es lo que lee la IA; el fichero original se
    # conserva aparte para poder descargarlo.
    texto: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def resumen(self) -> str:
        """Primeras lineas del texto, para mostrar de que va sin abrirlo."""
        limpio = " ".join(self.texto.split())
        return limpio[:200] + ("..." if len(limpio) > 200 else "")


class ContextoMateria(BaseModel):
    """Marco de la asignatura que acompana a una clase al generar los apuntes.

    Es lo que convierte los PDFs en algo mas que un adjunto descargable: con el
    programa delante, los apuntes pueden decir a que unidad corresponde la
    clase y usar la notacion de la catedra.
    """

    materia: str = ""
    tema: str = ""
    materiales: list[Material] = Field(default_factory=list)

    @property
    def vacio(self) -> bool:
        return not (self.materia or self.tema or self.materiales)


class Nota(BaseModel):
    """Nota escrita por la propia persona, no generada por la IA."""

    id: str
    grupo_id: str
    tema_id: str | None = None
    titulo: str
    contenido: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
