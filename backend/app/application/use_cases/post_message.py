from __future__ import annotations

from app.domain.entities import Message, utcnow
from app.domain.exceptions import NotFoundError
from app.domain.repositories import AbstractUnitOfWork, PatientReplyGenerator


class PostMessageUseCase:
    """Persists a doctor's chat message and the simulated patient's reply.

    The reply itself comes from a PatientReplyGenerator port -- in this project
    that's a stub (see app/infrastructure/patient_reply.py) that echoes back a
    placeholder. A real LLM integration would only need a new implementation of
    that port; this use case and everything above it stays unchanged.
    """

    def __init__(self, uow: AbstractUnitOfWork, reply_generator: PatientReplyGenerator) -> None:
        self._uow = uow
        self._reply_generator = reply_generator

    async def execute(self, session_id: str, content: str) -> Message:
        async with self._uow:
            session = await self._uow.sessions.get(session_id)
            if session is None:
                raise NotFoundError("Session", session_id)

            scenario = await self._uow.scenarios.get(session.scenario_id)
            if scenario is None:
                raise NotFoundError("Scenario", session.scenario_id)

            history = await self._uow.messages.list_by_session(session_id)

            user_message = Message(
                session_id=session_id, role="user", content=content, created_at=utcnow()
            )
            await self._uow.messages.add(user_message)

            reply_text = await self._reply_generator.generate_reply(scenario, history, content)

            assistant_message = Message(
                session_id=session_id, role="assistant", content=reply_text, created_at=utcnow()
            )
            assistant_message = await self._uow.messages.add(assistant_message)

            await self._uow.commit()
            return assistant_message
