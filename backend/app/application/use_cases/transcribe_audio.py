from __future__ import annotations

from app.domain.repositories import SpeechToTextPort


class TranscribeAudioUseCase:
    """Transcribes an uploaded audio clip to text.

    No persistence involved -- this never touches the database, so unlike
    every other use case in this package it takes the port directly rather
    than an AbstractUnitOfWork (there's nothing to inject a uow for).
    """

    def __init__(self, speech_to_text: SpeechToTextPort) -> None:
        self._speech_to_text = speech_to_text

    async def execute(self, audio_bytes: bytes, filename: str | None = None) -> str:
        return await self._speech_to_text.transcribe(audio_bytes, filename)
