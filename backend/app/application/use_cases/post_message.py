from __future__ import annotations

import logging

from app.domain.entities import Evidence, Message, utcnow
from app.domain.exceptions import NotFoundError, RagServiceUnavailableError
from app.domain.repositories import AbstractUnitOfWork, EvidenceRetriever, PatientReplyGenerator

logger = logging.getLogger(__name__)


class PostMessageUseCase:
    """Persists a doctor's chat message and the simulated patient's reply.

    The reply itself comes from a PatientReplyGenerator port -- in this project
    that's a stub (see app/infrastructure/patient_reply.py) that echoes back a
    placeholder. A real LLM integration would only need a new implementation of
    that port; this use case and everything above it stays unchanged.

    Also queries an EvidenceRetriever port (RAG) for context relevant to the
    doctor's message, and passes whatever comes back into generate_reply().
    RAG retrieval is treated as best-effort, not required: a
    RagServiceUnavailableError (the RAG API down/unconfigured/erroring) is
    caught here and logged, and the chat turn proceeds with no evidence
    rather than failing the whole request -- a doctor's conversation with
    the simulated patient should not go down because an auxiliary retrieval
    service is unavailable.
    """

    def __init__(
        self,
        uow: AbstractUnitOfWork,
        reply_generator: PatientReplyGenerator,
        evidence_retriever: EvidenceRetriever,
    ) -> None:
        self._uow = uow
        self._reply_generator = reply_generator
        self._evidence_retriever = evidence_retriever

    async def _retrieve_evidence(self, content: str) -> list[Evidence]:
        try:
            return await self._evidence_retriever.retrieve(query=content)
        except RagServiceUnavailableError as exc:
            logger.warning("RAG evidence retrieval failed, proceeding with no evidence: %s", exc)
            return []

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

            evidence = await self._retrieve_evidence(content)
            reply_text = await self._reply_generator.generate_reply(scenario, history, content, evidence)

            assistant_message = Message(
                session_id=session_id, role="assistant", content=reply_text, created_at=utcnow()
            )
            assistant_message = await self._uow.messages.add(assistant_message)

            await self._uow.commit()
            return assistant_message
