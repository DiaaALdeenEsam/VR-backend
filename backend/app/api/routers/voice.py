"""Voice pipeline endpoints: POST /transcribe, POST /sessions/{id}/chat-voice, WS /ws/voice.

Routing note: the feature plan named the chat-voice route `/chat-voice`
(flat, session id implied by the request body). Implemented here as
`/sessions/{session_id}/chat-voice` instead, matching every other
session-scoped mutation in this API (.../messages, .../tests, .../answers,
.../evaluate) rather than introducing a different convention for this one
route. /transcribe stays flat as specified -- it genuinely isn't
session-scoped. /ws/voice also stays flat as specified, with session_id
passed as a query parameter at connect time (a WS route can't express a path
parameter the same way a REST resource nesting does without a matching
`{session_id}` segment, and the plan didn't ask for one here).

Async reply generation (the sole PatientReplyGenerator is the RAG API, 6-48s
observed per call -- see app/infrastructure/rag_patient_generator.py and
app/application/use_cases/post_message_async.py): both `chat_voice` and this
WS handler register the live connection with app/infrastructure/ws_hub.py
(via the ReplyPushPort domain port) so a reply generated in the background
can be pushed to it once ready, however the turn that triggered it arrived --
REST chat-voice, REST /messages, or a WS binary frame all land on the same
push channel, keyed only by session_id.

`use_case` below is typed as a Union (ProcessVoiceChatUseCase |
ProcessVoiceChatAsyncUseCase) even though get_process_voice_chat_use_case
(app/api/deps.py) unconditionally returns the async one in production: tests
override that exact dependency to the synchronous ProcessVoiceChatUseCase
(wired with fast stub ports) for fixtures that don't want to exercise the
async orchestration layer -- see tests/conftest.py. Both branches below
(`isinstance(result, VoiceChatResult)`) are real, live code paths, not
dead code left over from a removed toggle.
"""

from __future__ import annotations

import base64

from fastapi import APIRouter, Depends, File, UploadFile, WebSocket, WebSocketDisconnect

from app.api.deps import (
    get_process_voice_chat_use_case,
    get_transcribe_audio_use_case,
    get_tts_content_type,
    get_ws_hub,
)
from app.api.schemas.message import MessageRead
from app.api.schemas.voice import ChatVoiceResponse, TranscribeResponse
from app.application.use_cases.process_voice_chat import ProcessVoiceChatUseCase, VoiceChatResult
from app.application.use_cases.process_voice_chat_async import ProcessVoiceChatAsyncUseCase
from app.application.use_cases.transcribe_audio import TranscribeAudioUseCase
from app.domain.exceptions import DomainError, SessionBusyError
from app.infrastructure.ws_hub import WebSocketPushHub

router = APIRouter(tags=["voice"])


@router.post("/transcribe", response_model=TranscribeResponse)
async def transcribe(
    file: UploadFile = File(...),
    use_case: TranscribeAudioUseCase = Depends(get_transcribe_audio_use_case),
) -> TranscribeResponse:
    audio_bytes = await file.read()
    text = await use_case.execute(audio_bytes, filename=file.filename)
    return TranscribeResponse(text=text)


@router.post("/sessions/{session_id}/chat-voice", response_model=ChatVoiceResponse)
async def chat_voice(
    session_id: str,
    file: UploadFile = File(...),
    use_case: ProcessVoiceChatUseCase | ProcessVoiceChatAsyncUseCase = Depends(get_process_voice_chat_use_case),
    content_type: str = Depends(get_tts_content_type),
) -> ChatVoiceResponse:
    """reply_audio_base64/reply_audio_content_type are null in the response
    in production: STT still runs synchronously (fast), so `transcribed_text`
    is always real, but the reply itself comes back as a "pending" placeholder
    (no audio yet) -- the real text and its audio are pushed later over WS
    /ws/voice?session_id=... via app/infrastructure/ws_hub.py, the same push
    channel described in this module's docstring. (Tests that override
    get_process_voice_chat_use_case to the synchronous ProcessVoiceChatUseCase
    still get the reply and its audio already final in this response --
    see this module's docstring.)
    """

    audio_bytes = await file.read()
    result = await use_case.execute(session_id, audio_bytes, filename=file.filename)
    assert result.reply_message.id is not None

    reply = MessageRead(
        id=result.reply_message.id,
        role=result.reply_message.role,
        content=result.reply_message.content,
        created_at=result.reply_message.created_at,
        status=result.reply_message.status,
    )

    if isinstance(result, VoiceChatResult):
        return ChatVoiceResponse(
            transcribed_text=result.transcribed_text,
            reply=reply,
            reply_audio_base64=base64.b64encode(result.reply_audio).decode("ascii"),
            reply_audio_content_type=content_type,
        )

    return ChatVoiceResponse(transcribed_text=result.transcribed_text, reply=reply)


@router.websocket("/ws/voice")
async def voice_websocket(
    websocket: WebSocket,
    use_case: ProcessVoiceChatUseCase | ProcessVoiceChatAsyncUseCase = Depends(get_process_voice_chat_use_case),
    content_type: str = Depends(get_tts_content_type),
    hub: WebSocketPushHub = Depends(get_ws_hub),
) -> None:
    """Voice chat over a WebSocket.

    Protocol: connect to `/ws/voice?session_id=SESSION_ID`. Each binary frame
    the client sends is treated as one complete utterance -- not a raw PCM
    stream needing voice-activity segmentation, the same granularity as the
    REST voice endpoints. For each one, in production (async, the sole
    PatientReplyGenerator is the RAG API, 6-48s observed per call): the server
    replies immediately with `{"type": "transcript", "text": ...}` then
    `{"type": "reply", "id": ..., "status": "pending", ...}` (no audio yet)
    and goes straight back to `receive_bytes()` -- it does not wait for the
    real reply. The real reply -- `{"type": "reply", "status":
    "complete"|"failed", ...}` -- and a binary audio frame arrive later,
    pushed to this same connection once the background generation finishes
    (see app/infrastructure/ws_hub.py). An interim `{"type": "thinking", "id":
    ...}` frame may arrive first if generation is still running after
    Settings.rag_patient_filler_after_seconds.

    (Tests that override get_process_voice_chat_use_case to the synchronous
    ProcessVoiceChatUseCase get the older, fully-synchronous sequence instead:
    transcript, then a `{"type": "reply", ...}` frame with the real text, then
    one binary frame with the reply audio -- all before the next
    `receive_bytes()`. See this module's own docstring.)

    A `SessionBusyError` (a second frame arrived while the first is still
    generating) sends `{"type": "error", "code": "session_busy", "detail":
    ...}` and keeps the connection open -- a doctor's double-tap shouldn't
    kill the whole voice channel. Any other DomainError (most commonly an
    unknown session_id) sends `{"type": "error", "detail": ...}` and closes
    the connection -- there is no HTTP middleware layer for a WebSocket, so
    this is the WS-native equivalent of app/api/error_handlers.py's mapping.
    """

    await websocket.accept()

    session_id = websocket.query_params.get("session_id")
    if not session_id:
        await websocket.send_json({"type": "error", "detail": "missing session_id query parameter"})
        await websocket.close(code=4400)
        return

    # Registered for the whole connection lifetime, not just around one
    # execute() call -- a background task from an *earlier* frame on this
    # same connection may still push its result while a *later* frame is
    # being processed.
    hub.register(session_id, websocket)

    try:
        while True:
            audio_bytes = await websocket.receive_bytes()

            try:
                result = await use_case.execute(session_id, audio_bytes)
            except SessionBusyError as exc:
                await websocket.send_json({"type": "error", "code": "session_busy", "detail": str(exc)})
                continue
            except DomainError as exc:
                await websocket.send_json({"type": "error", "detail": str(exc)})
                await websocket.close(code=4400)
                return

            assert result.reply_message.id is not None
            await websocket.send_json({"type": "transcript", "text": result.transcribed_text})
            await websocket.send_json(
                {
                    "type": "reply",
                    "id": result.reply_message.id,
                    "text": result.reply_message.content,
                    "status": result.reply_message.status,
                    "created_at": result.reply_message.created_at.isoformat(),
                    "audio_content_type": content_type,
                }
            )
            if isinstance(result, VoiceChatResult):
                await websocket.send_bytes(result.reply_audio)
            # else: async path -- no audio yet; the real reply + its audio
            # are pushed later via push_port, to this same registered
            # connection, once PostMessageAsyncUseCase's background phase
            # finishes (see that class and app/infrastructure/ws_hub.py).
    except WebSocketDisconnect:
        pass
    finally:
        hub.unregister(session_id, websocket)
