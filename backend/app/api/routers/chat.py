from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_post_message_use_case
from app.api.schemas.message import MessageCreate, MessageRead
from app.application.use_cases.post_message import PostMessageUseCase

router = APIRouter(tags=["chat"])


@router.post("/sessions/{session_id}/messages", response_model=MessageRead, status_code=201)
async def post_message(
    session_id: str,
    payload: MessageCreate,
    use_case: PostMessageUseCase = Depends(get_post_message_use_case),
) -> MessageRead:
    message = await use_case.execute(session_id, payload.content)
    assert message.id is not None
    return MessageRead(id=message.id, role=message.role, content=message.content, created_at=message.created_at)
