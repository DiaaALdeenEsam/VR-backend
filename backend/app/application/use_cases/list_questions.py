from __future__ import annotations

from app.domain.entities import Question
from app.domain.exceptions import NotFoundError
from app.domain.repositories import AbstractUnitOfWork


class ListQuestionsUseCase:
    """Lists a scenario's quiz questions with their choices.

    correct_choice_id is part of the Question entity but must never be copied
    into the API response schema here -- that would leak the answer key.
    """

    def __init__(self, uow: AbstractUnitOfWork) -> None:
        self._uow = uow

    async def execute(self, scenario_id: int) -> list[Question]:
        async with self._uow:
            scenario = await self._uow.scenarios.get(scenario_id)
            if scenario is None:
                raise NotFoundError("Scenario", scenario_id)
            return await self._uow.questions.list_by_scenario(scenario_id)
