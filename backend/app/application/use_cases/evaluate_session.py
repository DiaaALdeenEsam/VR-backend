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

            return await self._evaluation_generator.evaluate(
                case_text=scenario.case_text,
                gold_standard=scenario.gold_standard,
                messages=message_dicts,
            )
