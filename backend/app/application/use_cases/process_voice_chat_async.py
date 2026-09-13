from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from app.application.use_cases.post_message_async import PostMessageAsyncUseCase
from app.domain.entities import Message
from app.domain.repositories import SpeechToTextPort

logger = logging.getLogger(__name__)


@dataclass
class VoiceChatAsyncResult:
    """Same role as VoiceChatResult (process_voice_chat.py), minus the audio:
    there is no reply_audio here because there is no reply text yet -- the
    real content, and its synthesized audio, only exist once
    PostMessageAsyncUseCase's background phase finishes and pushes them (see
    that class's `text_to_speech` param) over the session's live WS
    connection. The caller/router responds with just the transcript and a
    "pending" placeholder message."""

    transcribed_text: str
    reply_message: Message


class ProcessVoiceChatAsyncUseCase:
    """Voice-chat counterpart to PostMessageAsyncUseCase, wired unconditionally
    in production (see app/api/deps.py's get_process_voice_chat_use_case):
    STT still runs synchronously (fast; not the latency problem this exists
    for) and the transcript comes back in the same HTTP response as before,
    but the patient's reply (and its TTS audio) are generated in the
    background exactly like the text-chat path -- see PostMessageAsyncUseCase's
    module docstring for the full reasoning (concurrency policy, fallback
    behavior, etc.), all of which applies unchanged here since this only adds
    STT in front of it.

    Deliberately a separate class from ProcessVoiceChatUseCase (not a runtime
    branch inside it) for the same reason PostMessageAsyncUseCase is separate
    from PostMessageUseCase: composing PostMessageAsyncUseCase here and
    immediately calling text_to_speech.synthesize() on its return value would
    synthesize audio for the still-empty placeholder, not the real reply --
    the two use cases return meaningfully different things, not just
    different timings of the same thing.
    """

    def __init__(
        self,
        speech_to_text: SpeechToTextPort,
        post_message_async_use_case: PostMessageAsyncUseCase,
    ) -> None:
        self._speech_to_text = speech_to_text
        self._post_message_async_use_case = post_message_async_use_case

    async def execute(
        self, session_id: str, audio_bytes: bytes, filename: str | None = None
    ) -> VoiceChatAsyncResult:
        logger.info(
            "[voice_chat] flow_started | session_id=%s | audio_bytes=%d",
            session_id,
            len(audio_bytes),
        )
        start_time = time.monotonic()

        transcribed_text = await self._speech_to_text.transcribe(audio_bytes, filename)
        logger.info(
            "[voice_chat] stt_completed | session_id=%s | duration_s=%.1f | transcript_chars=%d",
            session_id,
            time.monotonic() - start_time,
            len(transcribed_text),
        )

        # Raises NotFoundError/SessionBusyError itself -- nothing extra here.
        # The patient-reply + TTS phases are logged by PostMessageAsyncUseCase
        # itself (see post_message_async.py's [post_message] checkpoints).
        reply_message = await self._post_message_async_use_case.execute(session_id, transcribed_text)

        return VoiceChatAsyncResult(transcribed_text=transcribed_text, reply_message=reply_message)
