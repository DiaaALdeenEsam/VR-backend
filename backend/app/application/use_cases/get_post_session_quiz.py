from __future__ import annotations

from app.domain.entities import Question
from app.domain.exceptions import NotFoundError
from app.domain.repositories import AbstractUnitOfWork


class GetPostSessionQuizUseCase:
    """Lists the post-session MCQ quiz for the scenario a session belongs to
    -- disease name, attack-severity classification, and management plan
    across its three stages (immediate 0-30min, monitoring 30-60min,
    disposition decision). See Question.category / QuestionCategory
    (migration 0007) for how these questions are distinguished from a
    scenario's ordinary OSCE questions (ListQuestionsUseCase's audience).

    Deliberately synchronous, unlike PostMessageAsyncUseCase/
    EvaluateSessionAsyncUseCase: scoring an MCQ against a stored
    correct_choice_id is an instant, deterministic DB lookup, not a call to
    an external LLM -- there is nothing here to defer to a background task or
    push over the session's WS connection. This mirrors AnswerQuestionUseCase/
    ListQuestionsUseCase's shape exactly, not the async ack-then-push ones.

    Session-scoped (takes session_id, not scenario_id) purely for the
    client's convenience -- by the time a chat session is over, the client
    already holds session_id, not scenario_id, so this resolves the scenario
    itself rather than requiring a second round trip. correct_choice_id is
    never returned -- see GetPostSessionQuizUseCase's caller in
    app/api/routers/sessions.py and QuestionRead's own docstring for where
    that's enforced.
    """

    def __init__(self, uow: AbstractUnitOfWork) -> None:
        self._uow = uow

    async def execute(self, session_id: str) -> tuple[int, list[Question]]:
        """Returns (scenario_id, questions) -- the scenario id is handed back
        alongside the questions purely for the API response shape (see
        PostSessionQuizResponse), not because any caller needs it for logic."""

        async with self._uow:
            session = await self._uow.sessions.get(session_id)
            if session is None:
                raise NotFoundError("Session", session_id)

            questions = await self._uow.questions.list_post_session_quiz(session.scenario_id)
            return session.scenario_id, questions
