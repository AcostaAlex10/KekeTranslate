"""Proveedor de transcripcion basado en la API de audio de OpenAI.

Advertencia sobre los limites: el endpoint `/v1/audio/transcriptions` acepta
como maximo **25 MB por peticion**, tanto con `whisper-1` como con los modelos
`gpt-4o-transcribe`. Una clase de 4 horas supera ese tope con holgura, asi que
este proveedor:

  1. Segmenta el audio con ffmpeg en bloques de 10 minutos (MP3 mono a 64 kbps,
     ~4.8 MB por bloque).
  2. Transcribe cada bloque por separado.
  3. Concatena los resultados desplazando las marcas de tiempo de cada bloque.

Ademas, la API de OpenAI **no ofrece diarizacion**, por lo que todas las
intervenciones se atribuyen a un unico orador. Si necesitas identificar
oradores, usa `assemblyai` o `deepgram`.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import httpx

from ..media import split_audio
from ..models import TranscriptionResult, Utterance
from .base import ProgressCallback, TranscriptionError, TranscriptionProvider

API_URL = "https://api.openai.com/v1/audio/transcriptions"

# Duracion de cada segmento. 10 minutos deja un margen amplio frente al tope
# de 25 MB por peticion.
SEGMENT_SECONDS = 600


class OpenAIWhisperProvider(TranscriptionProvider):
    """Transcribe audios largos troceandolos para respetar el tope de 25 MB."""

    name = "openai"

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "whisper-1",
        timeout_seconds: float = 1800.0,
    ) -> None:
        if not api_key:
            raise TranscriptionError(
                "Falta OPENAI_API_KEY. Anadela a tu fichero .env."
            )
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds

    @property
    def supports_diarization(self) -> bool:
        return False

    async def transcribe(
        self,
        audio_path: Path,
        *,
        language: str | None = None,
        diarize: bool = True,
        expected_speakers: int | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> TranscriptionResult:
        with tempfile.TemporaryDirectory(prefix="keke_chunks_") as tmp_dir:
            await self._report(on_progress, "Segmentando el audio con ffmpeg")
            segments = await split_audio(
                audio_path, Path(tmp_dir), segment_seconds=SEGMENT_SECONDS
            )

            utterances: list[Utterance] = []
            texts: list[str] = []

            timeout = httpx.Timeout(
                connect=30.0, read=self._timeout_seconds, write=None, pool=30.0
            )
            async with httpx.AsyncClient(timeout=timeout) as client:
                for index, segment in enumerate(segments):
                    await self._report(
                        on_progress,
                        f"Transcribiendo segmento {index + 1} de {len(segments)}",
                    )
                    # Desplazamiento temporal del segmento respecto al original.
                    offset_ms = index * SEGMENT_SECONDS * 1000
                    chunk_text, chunk_utterances = await self._transcribe_segment(
                        client, segment, language, offset_ms
                    )
                    texts.append(chunk_text)
                    utterances.extend(chunk_utterances)

        total_ms = utterances[-1].end_ms if utterances else 0
        return TranscriptionResult(
            provider=self.name,
            text=" ".join(t for t in texts if t).strip(),
            utterances=utterances,
            audio_duration_seconds=total_ms / 1000 if total_ms else None,
            language_code=language,
        )

    async def _transcribe_segment(
        self,
        client: httpx.AsyncClient,
        segment: Path,
        language: str | None,
        offset_ms: int,
    ) -> tuple[str, list[Utterance]]:
        """Transcribe un segmento y reubica sus tiempos en la linea global."""
        data: dict[str, str] = {
            "model": self._model,
            "response_format": "verbose_json",
        }
        if language:
            data["language"] = language

        with segment.open("rb") as handle:
            response = await client.post(
                API_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                data=data,
                files={"file": (segment.name, handle, "audio/mpeg")},
            )

        if response.status_code >= 400:
            raise TranscriptionError(
                f"OpenAI devolvio un error ({response.status_code}): {response.text}"
            )

        payload = response.json()
        text = (payload.get("text") or "").strip()

        utterances: list[Utterance] = []
        for item in payload.get("segments") or []:
            content = (item.get("text") or "").strip()
            if not content:
                continue
            utterances.append(
                Utterance(
                    # Sin diarizacion: se atribuye todo a un mismo orador.
                    speaker="Orador 1",
                    start_ms=offset_ms + int(float(item.get("start", 0)) * 1000),
                    end_ms=offset_ms + int(float(item.get("end", 0)) * 1000),
                    text=content,
                )
            )

        # `verbose_json` puede no traer segmentos en algunos modelos; en ese
        # caso se registra el bloque completo como una unica intervencion.
        if not utterances and text:
            utterances.append(
                Utterance(
                    speaker="Orador 1",
                    start_ms=offset_ms,
                    end_ms=offset_ms + SEGMENT_SECONDS * 1000,
                    text=text,
                )
            )

        return text, utterances
