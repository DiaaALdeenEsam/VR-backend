from __future__ import annotations

from app.domain.entities import Answer, utcnow
from app.domain.exceptions import InvalidChoiceError, NotFoundError
from app.domain.repositories import AbstractUnitOfWork


class AnswerQuestionUseCase:
    """Records a doctor's answer to a question, upserting on re-answer.

    Correctness is computed and stored here, but never returned to the caller --
    the response schema for POST /sessions/{id}/answers must not include
    is_correct. It only becomes visible via GetSessionReviewUseCase.
    """

    def __init__(self, uow: AbstractUnitOfWork) -> None:
        self._uow = uow

    async def execute(self, session_id: str, question_id: int, choice_id: int) -> Answer:
        async with self._uow:
            session = await self._uow.sessions.get(session_id)
            if session is None:
                raise NotFoundError("Session", session_id)

            question = await self._uow.questions.get(question_id)
            if question is None:
                raise NotFoundError("Question", question_id)

            if not any(choice.id == choice_id for choice in question.choices):
                raise InvalidChoiceError(question_id, choice_id)

            is_correct = choice_id == question.correct_choice_id
            answer = Answer(
                session_id=session_id,
                question_id=question_id,
                choice_id=choice_id,
                is_correct=is_correct,
                answered_at=utcnow(),
            )
            answer = await self._uow.answers.upsert(answer)

            await self._uow.commit()
            return answer
