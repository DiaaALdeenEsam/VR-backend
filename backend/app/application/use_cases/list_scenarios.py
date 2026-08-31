from __future__ import annotations

from app.domain.entities import Scenario
from app.domain.repositories import AbstractUnitOfWork


class ListScenariosUseCase:
    """Lists scenarios available to start a session against.

    Note: only id/name are meant to reach the API response -- case_text and
    gold_standard are internal-only fields, never exposed by any endpoint.
    """

    def __init__(self, uow: AbstractUnitOfWork) -> None:
        self._uow = uow

    async def execute(self) -> list[Scenario]:
        async with self._uow:
            return await self._uow.scenarios.list_all()
