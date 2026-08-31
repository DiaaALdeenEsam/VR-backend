from __future__ import annotations

from dataclasses import dataclass

from app.application.use_cases.post_message import PostMessageUseCase
from app.domain.entities import Message
from app.domain.repositories import SpeechToTextPort, TextToSpeechPort


@dataclass
class VoiceChatResult:
    """Application-layer DTO bundling one voice-chat turn's outputs.

    Not a domain entity -- same reasoning as SessionReview in
    get_session_review.py: exists purely to hand the API layer everything it
    needs (transcript, the persisted reply message, and the synthesized reply
    audio) in one call.
    """

    transcribed_text: str
    reply_message: Message
    reply_audio: bytes


class ProcessVoiceChatUseCase:
    """One voice-chat turn: audio in -> transcribe -> chat turn -> synthesize reply.

    Composes PostMessageUseCase directly rather than reimplementing chat
    persistence against the uow itself -- the first use case in this project
    to depend on another use case rather than only on ports/uow. This is
    deliberate: it's the same persisted chat turn the text-chat endpoint
    uses, so there is exactly one place that logic lives; PostMessageUseCase
    already owns its own uow transaction internally, so this use case adds no
    transaction handling of its own around it.
    """

    def __init__(
        self,
        speech_to_text: SpeechToTextPort,
        post_message_use_case: PostMessageUseCase,
        text_to_speech: TextToSpeechPort,
    ) -> None:
        self._speech_to_text = speech_to_text
        self._post_message_use_case = post_message_use_case
        self._text_to_speech = text_to_speech

    async def execute(
        self, session_id: str, audio_bytes: bytes, filename: str | None = None
    ) -> VoiceChatResult:
        transcribed_text = await self._speech_to_text.transcribe(audio_bytes, filename)

        # PostMessageUseCase.execute() raises NotFoundError itself for an
        # unknown session/scenario -- nothing extra to check here.
        reply_message = await self._post_message_use_case.execute(session_id, transcribed_text)

        reply_audio = await self._text_to_speech.synthesize(reply_message.content)

        return VoiceChatResult(
            transcribed_text=transcribed_text,
            reply_message=reply_message,
            reply_audio=reply_audio,
        )
