from __future__ import annotations

import uuid

from app.domain.entities import Session, utcnow
from app.domain.exceptions import NotFoundError
from app.domain.repositories import AbstractUnitOfWork


class StartSessionUseCase:
    """Starts a new training session against a scenario."""

    def __init__(self, uow: AbstractUnitOfWork) -> None:
        self._uow = uow

    async def execute(self, scenario_id: int) -> Session:
        async with self._uow:
            scenario = await self._uow.scenarios.get(scenario_id)
            if scenario is None:
                raise NotFoundError("Scenario", scenario_id)

            session = Session(id=str(uuid.uuid4()), scenario_id=scenario_id, created_at=utcnow())
            session = await self._uow.sessions.add(session)
            await self._uow.commit()
            return session
