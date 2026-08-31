from __future__ import annotations

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.domain.entities import Answer
from app.domain.repositories import AnswerRepository
from app.infrastructure.db.models import AnswerModel


def _to_entity(row: AnswerModel) -> Answer:
    return Answer(
        id=row.id,
        session_id=row.session_id,
        question_id=row.question_id,
        choice_id=row.choice_id,
        is_correct=row.is_correct,
        answered_at=row.answered_at,
    )


class SqlAnswerRepository(AnswerRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(self, answer: Answer) -> Answer:
        result = await self._session.exec(
            select(AnswerModel).where(
                AnswerModel.session_id == answer.session_id,
                AnswerModel.question_id == answer.question_id,
            )
        )
        existing = result.first()

        if existing is not None:
            existing.choice_id = answer.choice_id
            existing.is_correct = answer.is_correct
            existing.answered_at = answer.answered_at
            self._session.add(existing)
            await self._session.flush()
            return _to_entity(existing)

        row = AnswerModel(
            session_id=answer.session_id,
            question_id=answer.question_id,
            choice_id=answer.choice_id,
            is_correct=answer.is_correct,
            answered_at=answer.answered_at,
        )
        self._session.add(row)
        await self._session.flush()
        return _to_entity(row)

    async def list_by_session(self, session_id: str) -> list[Answer]:
        result = await self._session.exec(
            select(AnswerModel).where(AnswerModel.session_id == session_id).order_by(AnswerModel.id)
        )
        return [_to_entity(row) for row in result.all()]
