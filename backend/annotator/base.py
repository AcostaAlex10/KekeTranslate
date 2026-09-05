"""Contrato comun a los anotadores y la logica que comparten.

Cambiar de modelo (Claude, Gemini, el que venga) solo deberia afectar a *como*
se hace la llamada. Todo lo demas —decidir entre una pasada y map-reduce,
trocear sin partir intervenciones, preparar los metadatos, limpiar la salida—
es identico y vive aqui.

Un anotador concreto solo tiene que implementar `_complete()`.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from datetime import datetime

from ..config import Settings
from ..models import ContextoMateria, TranscriptionResult
from ..pdf import recortar
from . import prompts


class AnnotationError(RuntimeError):
    """Fallo al generar los apuntes con el LLM."""


class BaseAnnotator(ABC):
    """Convierte una transcripcion en apuntes estructurados en Markdown."""

    #: Nombre del proveedor, para los mensajes de error que ve el usuario.
    nombre: str = "El modelo"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @abstractmethod
    async def _complete(self, system_prompt: str, user_prompt: str) -> str:
        """Envia una peticion al modelo y devuelve el texto de la respuesta."""
        raise NotImplementedError

    async def annotate(
        self,
        transcription: TranscriptionResult,
        *,
        filename: str,
        contexto: ContextoMateria | None = None,
    ) -> str:
        """Devuelve los apuntes en Markdown de la clase transcrita.

        `contexto` trae la materia y el material (programa, guias) del grupo al
        que pertenece la clase, si esta archivada en uno.
        """
        transcript = transcription.to_diarized_text()
        if not transcript.strip():
            raise AnnotationError(
                "La transcripcion esta vacia; no hay nada que anotar."
            )

        metadata = _build_metadata(transcription, filename)
        metadata.update(self._contexto_de_la_materia(contexto))

        if len(transcript) <= self._settings.annotation_single_pass_char_limit:
            return await self._single_pass(transcript, metadata)
        return await self._map_reduce(transcript, metadata)

    def _contexto_de_la_materia(
        self, contexto: ContextoMateria | None
    ) -> dict[str, str]:
        """Convierte el contexto del grupo en los bloques del prompt.

        Devuelve cadenas vacias cuando la clase no pertenece a ningun grupo,
        de modo que el prompt queda exactamente igual que antes: una clase
        suelta no debe pagar el coste de unas secciones vacias.
        """
        if contexto is None or contexto.vacio:
            return {"materia": "", "material": ""}

        materia = ""
        if contexto.materia:
            tema = f" — {contexto.tema}" if contexto.tema else ""
            materia = prompts.MATERIA_TEMPLATE.format(
                materia=contexto.materia, tema=tema
            )

        material = ""
        if contexto.materiales:
            # El presupuesto se reparte entre los documentos: uno muy largo no
            # puede dejar sin sitio al resto ni desplazar a la transcripcion,
            # que es lo que de verdad hay que anotar.
            por_documento = max(
                1_000,
                self._settings.annotation_material_char_limit
                // len(contexto.materiales),
            )
            documentos = "\n\n".join(
                f"### {doc.tipo.value.capitalize()}: {doc.filename}\n\n"
                f"{recortar(doc.texto, por_documento)}"
                for doc in contexto.materiales
                if doc.texto.strip()
            )
            if documentos:
                material = prompts.MATERIAL_TEMPLATE.format(documentos=documentos)

        return {"materia": materia, "material": material}

    # -- Pasada unica -------------------------------------------------------

    async def _single_pass(self, transcript: str, metadata: dict[str, str]) -> str:
        """Genera los apuntes en una sola llamada al modelo."""
        user_prompt = prompts.USER_PROMPT_TEMPLATE.format(
            output_template=prompts.OUTPUT_TEMPLATE,
            transcript=transcript,
            **metadata,
        )
        return await self._complete(prompts.SYSTEM_PROMPT, user_prompt)

    # -- Map-reduce ---------------------------------------------------------

    async def _map_reduce(self, transcript: str, metadata: dict[str, str]) -> str:
        """Procesa la transcripcion por bloques y despues los fusiona.

        Solo se activa por encima de `annotation_single_pass_char_limit`, es
        decir, para grabaciones que exceden con mucho las 4 horas objetivo.
        """
        chunks = _split_on_line_boundaries(
            transcript, self._settings.annotation_chunk_chars
        )

        # Los fragmentos son independientes entre si, pero **no** se lanzan
        # todos a la vez. El nivel gratuito de Gemini admite muy pocas
        # peticiones por minuto, asi que dispararlas juntas hacia fallar casi
        # todas con un 503 que ni siquiera parece un limite de ritmo. El orden
        # se restaura al recomponer la lista.
        partials = await self._con_ritmo(
            [
                (
                    prompts.MAP_SYSTEM_PROMPT,
                    prompts.MAP_USER_PROMPT_TEMPLATE.format(
                        index=index + 1, total=len(chunks), transcript=chunk
                    ),
                )
                for index, chunk in enumerate(chunks)
            ]
        )

        joined = "\n\n---\n\n".join(
            f"## Extracto {index + 1} de {len(partials)}\n\n{partial}"
            for index, partial in enumerate(partials)
        )

        reduce_prompt = prompts.REDUCE_USER_PROMPT_TEMPLATE.format(
            output_template=prompts.OUTPUT_TEMPLATE,
            partials=joined,
            **metadata,
        )
        # La fusion es una peticion mas, y llega pisandole los talones a la
        # ultima del reparto: tambien le toca esperar su turno.
        await self._respirar()
        return await self._complete(prompts.SYSTEM_PROMPT, reduce_prompt)

    async def _con_ritmo(self, peticiones: list[tuple[str, str]]) -> list[str]:
        """Ejecuta las peticiones sin superar el ritmo que aguanta el proveedor.

        `annotation_concurrency` marca cuantas pueden estar en vuelo a la vez y
        `annotation_pause_seconds` cuanto se espera antes de cada una salvo la
        primera.

        Recibe los prompts y no las corrutinas ya creadas: si una peticion
        falla, `gather` abandona las demas, y una corrutina creada y nunca
        esperada deja un aviso y trabajo a medias. Creandolas aqui, la que no
        llega a ejecutarse tampoco llega a existir.
        """
        semaforo = asyncio.Semaphore(max(1, self._settings.annotation_concurrency))

        async def _por_turno(posicion: int, prompts_de_la_peticion) -> str:
            async with semaforo:
                if posicion:
                    await self._respirar()
                return await self._complete(*prompts_de_la_peticion)

        return list(
            await asyncio.gather(
                *(
                    _por_turno(posicion, par)
                    for posicion, par in enumerate(peticiones)
                )
            )
        )

    async def _respirar(self) -> None:
        """Pausa entre peticiones. Aislada para que los tests no esperen."""
        pausa = max(0.0, self._settings.annotation_pause_seconds)
        if pausa:
            await asyncio.sleep(pausa)


# ---------------------------------------------------------------------------
# Auxiliares
# ---------------------------------------------------------------------------


def _build_metadata(
    transcription: TranscriptionResult, filename: str
) -> dict[str, str]:
    """Prepara los metadatos que se inyectan en el prompt."""
    speakers = transcription.speakers
    return {
        "filename": filename,
        "duration": _format_duration(transcription.audio_duration_seconds),
        "speakers": ", ".join(speakers) if speakers else "no identificados",
        "processed_at": datetime.now().strftime("%d/%m/%Y"),
    }


def _format_duration(seconds: float | None) -> str:
    """Formatea una duracion en un texto legible del tipo `3 h 12 min`."""
    if not seconds:
        return "desconocida"
    total_minutes = int(seconds // 60)
    hours, minutes = divmod(total_minutes, 60)
    if hours:
        return f"{hours} h {minutes:02d} min"
    return f"{minutes} min"


def _dividir_lineas_enormes(lineas: list[str], max_chars: int):
    """Devuelve las lineas, partiendo las que no caben en un bloque.

    El corte busca el final de frase mas cercano al limite para no cortar una
    idea a mitad; si no hay ninguno, se corta por el ultimo espacio disponible.
    """
    for linea in lineas:
        while len(linea) > max_chars:
            ventana = linea[:max_chars]
            corte = max(
                ventana.rfind(". "), ventana.rfind("? "), ventana.rfind("! ")
            )
            if corte <= 0:
                corte = ventana.rfind(" ")
            if corte <= 0:
                corte = max_chars - 1
            yield linea[: corte + 1].strip()
            linea = linea[corte + 1 :].lstrip()
        yield linea


def _split_on_line_boundaries(text: str, max_chars: int) -> list[str]:
    """Trocea el texto sin partir ninguna linea por la mitad.

    Cada linea de la transcripcion es una intervencion completa con su orador y
    su marca de tiempo, asi que cortar por lineas mantiene intacto el contexto
    de cada fragmento.

    Una linea puede, aun asi, superar por si sola el tamano del bloque: pasa
    cuando el proveedor devuelve la clase entera como una sola intervencion.
    En ese caso se parte tambien por dentro, porque devolver un bloque mas
    grande que el limite deja el map-reduce sin efecto justo en las clases
    largas, que son las unicas que lo necesitan.
    """
    chunks: list[str] = []
    current: list[str] = []
    current_length = 0

    for line in _dividir_lineas_enormes(text.splitlines(), max_chars):
        line_length = len(line) + 1
        if current and current_length + line_length > max_chars:
            chunks.append("\n".join(current))
            current, current_length = [], 0
        current.append(line)
        current_length += line_length

    if current:
        chunks.append("\n".join(current))
    return chunks


def _strip_code_fence(text: str) -> str:
    """Quita el bloque de codigo envolvente si el modelo lo anadio."""
    if not text.startswith("```"):
        return text

    lines = text.splitlines()
    if len(lines) >= 2 and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return text
