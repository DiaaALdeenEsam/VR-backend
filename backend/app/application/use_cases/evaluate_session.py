from __future__ import annotations

from app.domain.entities import SessionEvaluation
from app.domain.exceptions import MissingGoldStandardError, NotFoundError
from app.domain.repositories import AbstractUnitOfWork, EvaluationGenerator


class EvaluateSessionUseCase:
    """Generates an OSCE-style evaluation of a completed session.

    Note on dependencies: the original feature plan named SessionRepository,
    ScenarioRepository, and EvaluationGenerator as this use case's
    constructor dependencies. This project's established convention (every
    other use case in this package) bundles repository access behind a
    single AbstractUnitOfWork instead -- so this takes `uow` (exposing
    `.sessions` and `.scenarios`, among others) plus the EvaluationGenerator
    port, the same (uow, port) shape as PostMessageUseCase. Read-only, so it
    never calls `uow.commit()`.

    Gathers four kinds of signal for the EvaluationGenerator port to weigh:
    the chat transcript, which tests were ordered (against which tests the
    scenario actually considers relevant), and quiz answers (against how many
    questions the scenario has in total, not just how many were attempted) --
    see EvaluationGenerator.evaluate()'s docstring in app/domain/repositories.py
    for the exact shape of each.
    """

    def __init__(self, uow: AbstractUnitOfWork, evaluation_generator: EvaluationGenerator) -> None:
        self._uow = uow
        self._evaluation_generator = evaluation_generator

    async def execute(self, session_id: str) -> SessionEvaluation:
        async with self._uow:
            session = await self._uow.sessions.get(session_id)
            if session is None:
                raise NotFoundError("Session", session_id)

            scenario = await self._uow.scenarios.get(session.scenario_id)
            if scenario is None:
                raise NotFoundError("Scenario", session.scenario_id)

            if not scenario.gold_standard:
                raise MissingGoldStandardError(scenario.id)

            messages = await self._uow.messages.list_by_session(session_id)
            message_dicts = [{"role": m.role, "content": m.content} for m in messages]

            ordered_tests = await self._uow.ordered_tests.list_by_session(session_id)
            ordered_test_dicts = [{"test_id": ot.test_id} for ot in ordered_tests]

            answers = await self._uow.answers.list_by_session(session_id)
            answer_dicts = [
                {"question_id": a.question_id, "choice_id": a.choice_id, "is_correct": a.is_correct}
                for a in answers
            ]

            # What this scenario's own data says is relevant (see
            # ScenarioTestResultModel, migration 0004) -- what ordered_tests
            # is judged against, not what's merely orderable.
            relevant_tests = await self._uow.tests.list_scenario_relevant(session.scenario_id)
            relevant_test_ids = [t.id for t in relevant_tests]

            # Total, not just attempted: len(uow.questions.list_by_scenario(...))
            # rather than a dedicated count_by_scenario() -- QuestionRepository
            # already has to load full Question entities (with choices) for
            # GET /scenarios/{id}/questions, so a second method that only
            # returns a count would be redundant surface for what's already a
            # cheap, small, per-scenario list in this project's data.
            questions = await self._uow.questions.list_by_scenario(session.scenario_id)
            total_questions = len(questions)

            return await self._evaluation_generator.evaluate(
                case_text=scenario.case_text,
                gold_standard=scenario.gold_standard,
                messages=message_dicts,
                ordered_tests=ordered_test_dicts,
                answers=answer_dicts,
                relevant_test_ids=relevant_test_ids,
                total_questions=total_questions,
            )
