from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_post_message_use_case
from app.api.schemas.message import MessageCreate, MessageRead
from app.application.use_cases.post_message_async import PostMessageAsyncUseCase

router = APIRouter(tags=["chat"])


@router.post("/sessions/{session_id}/messages", response_model=MessageRead, status_code=201)
async def post_message(
    session_id: str,
    payload: MessageCreate,
    use_case: PostMessageAsyncUseCase = Depends(get_post_message_use_case),
) -> MessageRead:
    """`message` is a placeholder (status="pending") -- the RAG API backing
    every reply now (6-48s observed per call) means this always ack's fast
    and generates the real reply in the background; see
    app/application/use_cases/post_message_async.py. The real reply arrives
    later over the session's WS connection (WS /ws/voice, see
    app/infrastructure/ws_hub.py). A 409 (SessionBusyError) means a reply for
    this session is already being generated -- see that use case's module
    docstring for the concurrency policy.
    """

    message = await use_case.execute(session_id, payload.content)
    assert message.id is not None
    return MessageRead(
        id=message.id,
        role=message.role,
        content=message.content,
        created_at=message.created_at,
        status=message.status,
    )
