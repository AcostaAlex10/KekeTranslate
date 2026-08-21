"""Proveedor de transcripcion basado en Deepgram (modelo nova-3).

Deepgram acepta el audio pre-grabado como cuerpo binario de una unica peticion
a `/v1/listen`. Para clases de 2-4 horas hay dos consideraciones:

  * La respuesta puede tardar varios minutos, asi que se usa un timeout de
    lectura amplio en lugar del valor por defecto de httpx.
  * `diarize=true` devuelve un campo `speaker` por palabra; aqui se agrupan las
    palabras consecutivas del mismo orador para reconstruir intervenciones.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from ..models import TranscriptionResult, Utterance
from .base import ProgressCallback, TranscriptionError, TranscriptionProvider

API_URL = "https://api.deepgram.com/v1/listen"

UPLOAD_CHUNK_BYTES = 5 * 1024 * 1024


class DeepgramProvider(TranscriptionProvider):
    """Cliente asincrono del endpoint de audio pre-grabado de Deepgram."""

    name = "deepgram"

    def __init__(self, api_key: str, *, timeout_seconds: float = 4 * 60 * 60) -> None:
        if not api_key:
            raise TranscriptionError(
                "Falta DEEPGRAM_API_KEY. Anadela a tu fichero .env."
            )
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds

    @property
    def supports_diarization(self) -> bool:
        return True

    async def transcribe(
        self,
        audio_path: Path,
        *,
        language: str | None = None,
        diarize: bool = True,
        expected_speakers: int | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> TranscriptionResult:
        params: dict[str, str] = {
            "model": "nova-3",
            "smart_format": "true",
            "punctuate": "true",
            "paragraphs": "true",
            "utterances": "true",
            "diarize": "true" if diarize else "false",
        }
        if language:
            params["language"] = language
        else:
            params["detect_language"] = "true"

        headers = {
            "Authorization": f"Token {self._api_key}",
            "Content-Type": "application/octet-stream",
        }

        async def file_chunks():
            with audio_path.open("rb") as handle:
                while chunk := handle.read(UPLOAD_CHUNK_BYTES):
                    yield chunk

        await self._report(on_progress, "Enviando el audio a Deepgram")

        timeout = httpx.Timeout(
            connect=30.0, read=self._timeout_seconds, write=None, pool=30.0
        )
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                API_URL, params=params, headers=headers, content=file_chunks()
            )

        if response.status_code >= 400:
            raise TranscriptionError(
                f"Deepgram devolvio un error ({response.status_code}): {response.text}"
            )

        return self._to_result(response.json())

    def _to_result(self, payload: dict) -> TranscriptionResult:
        """Normaliza la respuesta de Deepgram al modelo interno."""
        channels = payload.get("results", {}).get("channels", [])
        alternative = channels[0]["alternatives"][0] if channels else {}

        text = (alternative.get("transcript") or "").strip()
        utterances: list[Utterance] = []

        # Deepgram devuelve `utterances` de primer nivel cuando se pide
        # `utterances=true`; es la fuente mas fiel a las intervenciones reales.
        for item in payload.get("results", {}).get("utterances", []) or []:
            content = (item.get("transcript") or "").strip()
            if not content:
                continue
            speaker = item.get("speaker")
            utterances.append(
                Utterance(
                    speaker=f"Orador {speaker}" if speaker is not None else "Orador",
                    start_ms=int(float(item.get("start", 0)) * 1000),
                    end_ms=int(float(item.get("end", 0)) * 1000),
                    text=content,
                )
            )

        # Si no hubo `utterances`, reconstruimos agrupando palabras por orador.
        if not utterances:
            utterances = _group_words_by_speaker(alternative.get("words") or [])

        duration = payload.get("metadata", {}).get("duration")
        return TranscriptionResult(
            provider=self.name,
            text=text,
            utterances=utterances,
            audio_duration_seconds=float(duration) if duration else None,
            language_code=alternative.get("language"),
            provider_job_id=payload.get("metadata", {}).get("request_id"),
        )


def _group_words_by_speaker(words: list[dict]) -> list[Utterance]:
    """Agrupa palabras consecutivas del mismo orador en intervenciones."""
    grouped: list[Utterance] = []
    current: Utterance | None = None

    for word in words:
        speaker = word.get("speaker")
        label = f"Orador {speaker}" if speaker is not None else "Orador"
        token = word.get("punctuated_word") or word.get("word") or ""
        start_ms = int(float(word.get("start", 0)) * 1000)
        end_ms = int(float(word.get("end", 0)) * 1000)

        if current is None or current.speaker != label:
            if current is not None:
                grouped.append(current)
            current = Utterance(
                speaker=label, start_ms=start_ms, end_ms=end_ms, text=token
            )
        else:
            current.text = f"{current.text} {token}".strip()
            current.end_ms = end_ms

    if current is not None:
        grouped.append(current)
    return grouped
