"""Proveedor de transcripcion basado en AssemblyAI.

Es el proveedor por defecto de KekeTranslate porque sus limites encajan con el
caso de uso sin necesidad de trocear el audio:

  * Hasta 5 GB por fichero enviado a `/v2/transcript`.
  * Hasta 2.2 GB si se sube el fichero local a `/v2/upload`.
  * Hasta 10 horas de duracion.

Una clase de 4 horas cabe entera, asi que se envia en una sola peticion y se
sondea el estado de forma asincrona. La diarizacion se activa con
`speaker_labels`, que devuelve el array `utterances` con orador y tiempos.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from ..models import TranscriptionResult, Utterance
from .base import ProgressCallback, TranscriptionError, TranscriptionProvider

API_BASE = "https://api.assemblyai.com/v2"

# Tamano de los bloques que se leen del disco al subir. Mantenerlo acotado
# permite subir ficheros de varios GB sin cargarlos en memoria.
UPLOAD_CHUNK_BYTES = 5 * 1024 * 1024


class AssemblyAIProvider(TranscriptionProvider):
    """Cliente asincrono del endpoint de transcripcion pre-grabada."""

    name = "assemblyai"

    def __init__(
        self,
        api_key: str,
        *,
        poll_interval: float = 15.0,
        timeout_seconds: float = 4 * 60 * 60,
    ) -> None:
        if not api_key:
            raise TranscriptionError(
                "Falta ASSEMBLYAI_API_KEY. Anadela a tu fichero .env."
            )
        self._api_key = api_key
        self._poll_interval = poll_interval
        self._timeout_seconds = timeout_seconds

    @property
    def supports_diarization(self) -> bool:
        return True

    @property
    def _headers(self) -> dict[str, str]:
        return {"authorization": self._api_key}

    async def transcribe(
        self,
        audio_path: Path,
        *,
        language: str | None = None,
        diarize: bool = True,
        expected_speakers: int | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> TranscriptionResult:
        # Timeout generoso: la subida de un fichero de varios GB puede tardar,
        # y el sondeo posterior se prolonga durante toda la transcripcion.
        timeout = httpx.Timeout(connect=30.0, read=600.0, write=None, pool=30.0)

        async with httpx.AsyncClient(timeout=timeout) as client:
            await self._report(on_progress, "Subiendo el audio a AssemblyAI")
            upload_url = await self._upload(client, audio_path)

            await self._report(on_progress, "Encolando la transcripcion")
            transcript_id = await self._create_transcript(
                client,
                upload_url,
                language=language,
                diarize=diarize,
                expected_speakers=expected_speakers,
            )

            await self._report(on_progress, "Transcribiendo la clase")
            payload = await self._poll(client, transcript_id, on_progress)

        return self._to_result(payload)

    async def _upload(self, client: httpx.AsyncClient, audio_path: Path) -> str:
        """Sube el fichero local y devuelve la URL temporal de AssemblyAI."""

        async def file_chunks():
            # `open` sincrono dentro de un generador asincrono: la lectura por
            # bloques es rapida y evita depender de aiofiles solo para esto.
            with audio_path.open("rb") as handle:
                while chunk := handle.read(UPLOAD_CHUNK_BYTES):
                    yield chunk

        response = await client.post(
            f"{API_BASE}/upload", headers=self._headers, content=file_chunks()
        )
        if response.status_code >= 400:
            raise TranscriptionError(
                f"Fallo al subir el audio ({response.status_code}): {response.text}"
            )
        return response.json()["upload_url"]

    async def _create_transcript(
        self,
        client: httpx.AsyncClient,
        audio_url: str,
        *,
        language: str | None,
        diarize: bool,
        expected_speakers: int | None,
    ) -> str:
        """Crea el trabajo de transcripcion y devuelve su id."""
        body: dict[str, object] = {
            "audio_url": audio_url,
            "speech_model": "universal",
            "punctuate": True,
            "format_text": True,
            "speaker_labels": diarize,
        }

        if language:
            body["language_code"] = language
        else:
            # Sin idioma explicito, dejamos que AssemblyAI lo detecte.
            body["language_detection"] = True

        # `speakers_expected` solo es valido junto a la diarizacion.
        if diarize and expected_speakers:
            body["speakers_expected"] = expected_speakers

        response = await client.post(
            f"{API_BASE}/transcript", headers=self._headers, json=body
        )
        if response.status_code >= 400:
            raise TranscriptionError(
                f"AssemblyAI rechazo la peticion ({response.status_code}): {response.text}"
            )
        return response.json()["id"]

    async def _poll(
        self,
        client: httpx.AsyncClient,
        transcript_id: str,
        on_progress: ProgressCallback | None,
    ) -> dict:
        """Sondea hasta que la transcripcion termina, falla o expira el plazo."""
        deadline = asyncio.get_running_loop().time() + self._timeout_seconds

        while True:
            response = await client.get(
                f"{API_BASE}/transcript/{transcript_id}", headers=self._headers
            )
            if response.status_code >= 400:
                raise TranscriptionError(
                    f"Error consultando el estado ({response.status_code}): {response.text}"
                )

            payload = response.json()
            status = payload.get("status")

            if status == "completed":
                return payload
            if status == "error":
                raise TranscriptionError(
                    f"AssemblyAI devolvio un error: {payload.get('error')}"
                )

            if asyncio.get_running_loop().time() > deadline:
                raise TranscriptionError(
                    "Se agoto el tiempo de espera de la transcripcion "
                    f"(id={transcript_id}, ultimo estado={status})."
                )

            await self._report(
                on_progress, f"Transcripcion en curso (estado: {status})"
            )
            await asyncio.sleep(self._poll_interval)

    def _to_result(self, payload: dict) -> TranscriptionResult:
        """Normaliza la respuesta de AssemblyAI al modelo interno."""
        utterances = [
            Utterance(
                speaker=f"Orador {item['speaker']}",
                start_ms=int(item.get("start", 0)),
                end_ms=int(item.get("end", 0)),
                text=(item.get("text") or "").strip(),
            )
            for item in (payload.get("utterances") or [])
            if (item.get("text") or "").strip()
        ]

        duration = payload.get("audio_duration")
        return TranscriptionResult(
            provider=self.name,
            text=(payload.get("text") or "").strip(),
            utterances=utterances,
            audio_duration_seconds=float(duration) if duration else None,
            language_code=payload.get("language_code"),
            provider_job_id=payload.get("id"),
        )
