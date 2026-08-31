from __future__ import annotations

from dataclasses import dataclass

from app.domain.entities import Answer, Message, OrderedTest, Question, Session, Test
from app.domain.exceptions import NotFoundError
from app.domain.repositories import AbstractUnitOfWork


@dataclass
class SessionReview:
    """Composed, read-only view of a session for GET /sessions/{id}.

    This is an application-layer DTO (not a domain entity) -- it exists purely
    to hand the API layer everything it needs in one call.
    """

    session: Session
    messages: list[Message]
    ordered_tests: list[tuple[OrderedTest, Test]]
    answers: list[tuple[Answer, Question]]


class GetSessionReviewUseCase:
    """Full review of a session: chat history, ordered tests, and graded answers.

    This is the only place correctness (Answer.is_correct) is ever exposed.
    """

    def __init__(self, uow: AbstractUnitOfWork) -> None:
        self._uow = uow

    async def execute(self, session_id: str) -> SessionReview:
        async with self._uow:
            session = await self._uow.sessions.get(session_id)
            if session is None:
                raise NotFoundError("Session", session_id)

            messages = await self._uow.messages.list_by_session(session_id)
            ordered_tests = await self._uow.ordered_tests.list_by_session(session_id)
            answers = await self._uow.answers.list_by_session(session_id)

            ordered_with_tests: list[tuple[OrderedTest, Test]] = []
            for ordered in ordered_tests:
                test = await self._uow.tests.get(ordered.test_id)
                if test is not None:
                    ordered_with_tests.append((ordered, test))

            answers_with_questions: list[tuple[Answer, Question]] = []
            for answer in answers:
                question = await self._uow.questions.get(answer.question_id)
                if question is not None:
                    answers_with_questions.append((answer, question))

            return SessionReview(
                session=session,
                messages=messages,
                ordered_tests=ordered_with_tests,
                answers=answers_with_questions,
            )
