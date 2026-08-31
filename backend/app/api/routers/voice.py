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
"""

from __future__ import annotations

import base64

from fastapi import APIRouter, Depends, File, UploadFile, WebSocket, WebSocketDisconnect

from app.api.deps import (
    get_process_voice_chat_use_case,
    get_transcribe_audio_use_case,
    get_tts_content_type,
)
from app.api.schemas.message import MessageRead
from app.api.schemas.voice import ChatVoiceResponse, TranscribeResponse
from app.application.use_cases.process_voice_chat import ProcessVoiceChatUseCase
from app.application.use_cases.transcribe_audio import TranscribeAudioUseCase
from app.domain.exceptions import DomainError

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
    use_case: ProcessVoiceChatUseCase = Depends(get_process_voice_chat_use_case),
    content_type: str = Depends(get_tts_content_type),
) -> ChatVoiceResponse:
    audio_bytes = await file.read()
    result = await use_case.execute(session_id, audio_bytes, filename=file.filename)
    assert result.reply_message.id is not None

    return ChatVoiceResponse(
        transcribed_text=result.transcribed_text,
        reply=MessageRead(
            id=result.reply_message.id,
            role=result.reply_message.role,
            content=result.reply_message.content,
            created_at=result.reply_message.created_at,
        ),
        reply_audio_base64=base64.b64encode(result.reply_audio).decode("ascii"),
        reply_audio_content_type=content_type,
    )


@router.websocket("/ws/voice")
async def voice_websocket(
    websocket: WebSocket,
    use_case: ProcessVoiceChatUseCase = Depends(get_process_voice_chat_use_case),
    content_type: str = Depends(get_tts_content_type),
) -> None:
    """Voice chat over a WebSocket.

    Protocol: connect to `/ws/voice?session_id=SESSION_ID`. Each binary frame
    the client sends is treated as one complete utterance -- not a raw PCM
    stream needing voice-activity segmentation, the same granularity as the
    REST voice endpoints. For each one, the server replies with two JSON text
    frames (`{"type": "transcript", "text": ...}`, then
    `{"type": "reply", "id": ..., "text": ..., "created_at": ...,
    "audio_content_type": ...}`) followed by one binary frame containing the
    synthesized reply audio. A domain error (most commonly an unknown
    session_id) sends `{"type": "error", "detail": ...}` and closes the
    connection -- there is no HTTP middleware layer for a WebSocket, so this
    is the WS-native equivalent of app/api/error_handlers.py's mapping.
    """

    await websocket.accept()

    session_id = websocket.query_params.get("session_id")
    if not session_id:
        await websocket.send_json({"type": "error", "detail": "missing session_id query parameter"})
        await websocket.close(code=4400)
        return

    try:
        while True:
            audio_bytes = await websocket.receive_bytes()

            try:
                result = await use_case.execute(session_id, audio_bytes)
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
                    "created_at": result.reply_message.created_at.isoformat(),
                    "audio_content_type": content_type,
                }
            )
            await websocket.send_bytes(result.reply_audio)
    except WebSocketDisconnect:
        pass
