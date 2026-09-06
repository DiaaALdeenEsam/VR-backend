"""Tests for the async reply-generation path: PostMessageAsyncUseCase directly
(fast, deterministic, no real network) plus one end-to-end check through the
API that the router/deps wiring for this path (the production default --
app/api/deps.py's get_post_message_use_case) actually works.

See docs/backend-rag-handoff.md for the real API's observed 6-48s latency
this feature exists to handle -- these tests use fake/controllable-delay
PatientReplyGenerators instead of the real network call, exactly like
tests/test_evaluation.py tests StubEvaluationGenerator directly rather than
a real LLM.
"""

from __future__ import annotations

import asyncio

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from app.application.use_cases.post_message_async import PostMessageAsyncUseCase
from app.config import get_settings
from app.domain.entities import Evidence, Message, Scenario
from app.domain.exceptions import SessionBusyError
from app.domain.repositories import EvidenceRetriever, PatientReplyGenerator
from app.infrastructure.db.repositories.unit_of_work import SqlAlchemyUnitOfWork
from app.infrastructure.inflight_sessions import InMemorySessionConcurrencyGuard


class _FakeReplyGenerator(PatientReplyGenerator):
    """Controllable-delay/outcome PatientReplyGenerator -- stands in for the
    real 6-48s-latency RagPatientReplyGenerator in every test below."""

    def __init__(self, *, delay: float = 0.0, reply: str = "fake reply", raises: Exception | None = None) -> None:
        self.delay = delay
        self.reply = reply
        self.raises = raises
        self.calls = 0

    async def generate_reply(
        self,
        scenario: Scenario,
        history: list[Message],
        user_message: str,
        evidence: list[Evidence] | None = None,
    ) -> str:
        self.calls += 1
        await asyncio.sleep(self.delay)
        if self.raises is not None:
            raise self.raises
        return self.reply


class _NoOpEvidenceRetriever(EvidenceRetriever):
    async def retrieve(self, query: str, top_k: int = 5, content_types: list[str] | None = None) -> list[Evidence]:
        return []


class _RecordingPushPort:
    """Fake ReplyPushPort -- records every push()/push_bytes() call instead
    of needing a real WS connection."""

    def __init__(self) -> None:
        self.pushed: list[dict] = []
        self.pushed_bytes: list[bytes] = []

    async def push(self, session_id: str, payload: dict) -> bool:
        self.pushed.append(payload)
        return True

    async def push_bytes(self, session_id: str, data: bytes) -> bool:
        self.pushed_bytes.append(data)
        return True


@pytest_asyncio.fixture
async def seeded_session(session_factory: async_sessionmaker[AsyncSession]) -> str:
    """A scenario + session, created directly against the test DB -- same
    pattern as tests/conftest.py's seed_ids fixture."""

    from app.infrastructure.db.models import ScenarioModel, SessionModel
    from app.domain.entities import utcnow
    import uuid

    async with session_factory() as session:
        scenario = ScenarioModel(name="حالة اختبار غير متزامنة", case_text="نص الحالة.", gold_standard=None)
        session.add(scenario)
        await session.flush()

        session_row = SessionModel(id=str(uuid.uuid4()), scenario_id=scenario.id, created_at=utcnow())
        session.add(session_row)
        await session.commit()
        return session_row.id


def _build_use_case(
    session_factory: async_sessionmaker[AsyncSession],
    reply_generator: PatientReplyGenerator,
    push_port: _RecordingPushPort,
    concurrency_guard: InMemorySessionConcurrencyGuard,
    hard_timeout_seconds: float = 5.0,
    filler_after_seconds: float = 60.0,
) -> PostMessageAsyncUseCase:
    settings = get_settings().model_copy(
        update={
            "rag_patient_hard_timeout_seconds": hard_timeout_seconds,
            "rag_patient_filler_after_seconds": filler_after_seconds,
            "rag_patient_fallback_reply": "fallback line",
        }
    )
    return PostMessageAsyncUseCase(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        reply_generator=reply_generator,
        evidence_retriever=_NoOpEvidenceRetriever(),
        settings=settings,
        concurrency_guard=concurrency_guard,
        push_port=push_port,
    )


async def _wait_until_settled(concurrency_guard: InMemorySessionConcurrencyGuard, session_id: str) -> None:
    """Polls (fast, short interval) until the background task for session_id
    has finished -- avoids a fixed sleep guessing at timing."""

    for _ in range(200):  # up to ~2s
        if not concurrency_guard.is_busy(session_id):
            return
        await asyncio.sleep(0.01)
    raise AssertionError("background task never settled")


# ---------- immediate ack ----------------------------------------------------


async def test_execute_returns_pending_placeholder_immediately(
    session_factory: async_sessionmaker[AsyncSession], seeded_session: str
) -> None:
    guard = InMemorySessionConcurrencyGuard()
    push_port = _RecordingPushPort()
    generator = _FakeReplyGenerator(delay=0.2, reply="real reply")
    use_case = _build_use_case(session_factory, generator, push_port, guard)

    placeholder = await use_case.execute(seeded_session, "hello")

    assert placeholder.status == "pending"
    assert placeholder.role == "assistant"
    assert generator.calls == 0  # generation hasn't necessarily even started synchronously
    assert guard.is_busy(seeded_session) is True

    await _wait_until_settled(guard, seeded_session)


# ---------- success path: real reply lands, gets pushed ----------------------


async def test_successful_generation_updates_message_and_pushes(
    session_factory: async_sessionmaker[AsyncSession], seeded_session: str
) -> None:
    guard = InMemorySessionConcurrencyGuard()
    push_port = _RecordingPushPort()
    generator = _FakeReplyGenerator(delay=0.05, reply="the real patient reply")
    use_case = _build_use_case(session_factory, generator, push_port, guard)

    placeholder = await use_case.execute(seeded_session, "hello")
    await _wait_until_settled(guard, seeded_session)

    async with session_factory() as session:
        from sqlmodel import select
        from app.infrastructure.db.models import MessageModel

        result = await session.exec(select(MessageModel).where(MessageModel.id == placeholder.id))
        row = result.one()
        assert row.status == "complete"
        assert row.content == "the real patient reply"

    reply_pushes = [p for p in push_port.pushed if p["type"] == "reply"]
    assert len(reply_pushes) == 1
    assert reply_pushes[0]["status"] == "complete"
    assert reply_pushes[0]["content"] == "the real patient reply"


# ---------- timeout: hard deadline exceeded -> fallback ----------------------


async def test_timeout_falls_back_to_configured_reply(
    session_factory: async_sessionmaker[AsyncSession], seeded_session: str
) -> None:
    guard = InMemorySessionConcurrencyGuard()
    push_port = _RecordingPushPort()
    # Generator takes far longer than the hard timeout below.
    generator = _FakeReplyGenerator(delay=1.0, reply="too slow to matter")
    use_case = _build_use_case(
        session_factory, generator, push_port, guard, hard_timeout_seconds=0.1, filler_after_seconds=0.05
    )

    placeholder = await use_case.execute(seeded_session, "hello")
    await _wait_until_settled(guard, seeded_session)

    async with session_factory() as session:
        from sqlmodel import select
        from app.infrastructure.db.models import MessageModel

        result = await session.exec(select(MessageModel).where(MessageModel.id == placeholder.id))
        row = result.one()
        assert row.status == "failed"
        assert row.content == "fallback line"

    reply_pushes = [p for p in push_port.pushed if p["type"] == "reply"]
    assert len(reply_pushes) == 1
    assert reply_pushes[0]["status"] == "failed"
    assert reply_pushes[0]["content"] == "fallback line"

    # The filler fired before the hard timeout (0.05s < 0.1s), so a "thinking"
    # event should have been pushed too.
    thinking_pushes = [p for p in push_port.pushed if p["type"] == "thinking"]
    assert len(thinking_pushes) == 1


# ---------- concurrency: second message while first is in flight -> 409 -----


async def test_second_message_while_first_in_flight_raises_session_busy(
    session_factory: async_sessionmaker[AsyncSession], seeded_session: str
) -> None:
    guard = InMemorySessionConcurrencyGuard()
    push_port = _RecordingPushPort()
    generator = _FakeReplyGenerator(delay=0.3, reply="slow reply")
    use_case = _build_use_case(session_factory, generator, push_port, guard)

    await use_case.execute(seeded_session, "first message")
    assert guard.is_busy(seeded_session) is True

    try:
        await use_case.execute(seeded_session, "second message, too soon")
        raised = False
    except SessionBusyError:
        raised = True

    assert raised is True

    await _wait_until_settled(guard, seeded_session)

    # Exactly one user message + one assistant reply -- the rejected second
    # call must not have persisted anything.
    async with session_factory() as session:
        from sqlmodel import select
        from app.infrastructure.db.models import MessageModel

        result = await session.exec(
            select(MessageModel).where(MessageModel.session_id == seeded_session).order_by(MessageModel.id)
        )
        rows = result.all()
    assert len(rows) == 2
    assert [r.role for r in rows] == ["user", "assistant"]
    assert rows[0].content == "first message"


async def test_a_new_message_is_accepted_once_the_first_reply_has_settled(
    session_factory: async_sessionmaker[AsyncSession], seeded_session: str
) -> None:
    guard = InMemorySessionConcurrencyGuard()
    push_port = _RecordingPushPort()
    generator = _FakeReplyGenerator(delay=0.05, reply="reply one")
    use_case = _build_use_case(session_factory, generator, push_port, guard)

    await use_case.execute(seeded_session, "first message")
    await _wait_until_settled(guard, seeded_session)
    assert guard.is_busy(seeded_session) is False

    generator.reply = "reply two"
    second_placeholder = await use_case.execute(seeded_session, "second message, now allowed")
    assert second_placeholder.status == "pending"
    await _wait_until_settled(guard, seeded_session)


# ---------- API-level: confirm the router really wires PostMessageAsyncUseCase ----


async def test_messages_endpoint_uses_async_path_when_wired_to_it(client, seed_ids, app) -> None:
    """tests/conftest.py's shared `app` fixture overrides get_post_message_use_case
    to the synchronous PostMessageUseCase by default (so the rest of the
    general test suite keeps its fast, immediate-reply assertions unchanged).
    This test overrides it back to a real PostMessageAsyncUseCase -- using the
    exact same construction app/api/deps.py's get_post_message_use_case uses
    in production -- to prove the router/response-schema wiring for the async
    path actually works end to end (ack immediately, settle to "complete"
    later), the same thing Settings.patient_reply_backend == "rag_api" used
    to select before that setting was removed (the RAG API is now the only
    production backend, so there's nothing left to select)."""

    from app.api.deps import (
        get_evidence_retriever,
        get_post_message_use_case,
        get_reply_push_port,
        get_session_concurrency_guard,
        get_uow_factory,
    )
    from app.application.use_cases.post_message_async import PostMessageAsyncUseCase

    fake_generator = _FakeReplyGenerator(delay=0.05, reply="async patient reply")
    # Reuse the shared fixture's own uow_factory override (a plain callable
    # stored in dependency_overrides) rather than the real production one,
    # so this still runs against the test's in-memory DB.
    uow_factory = app.dependency_overrides[get_uow_factory]()

    def override_with_async_use_case() -> PostMessageAsyncUseCase:
        return PostMessageAsyncUseCase(
            uow_factory=uow_factory,
            reply_generator=fake_generator,
            evidence_retriever=get_evidence_retriever(),
            settings=get_settings().model_copy(update={"rag_patient_hard_timeout_seconds": 2.0}),
            concurrency_guard=get_session_concurrency_guard(),
            push_port=get_reply_push_port(),
        )

    app.dependency_overrides[get_post_message_use_case] = override_with_async_use_case
    try:
        create_resp = await client.post("/sessions", json={"scenario_id": seed_ids["scenario_id"]})
        session_id = create_resp.json()["session_id"]

        response = await client.post(f"/sessions/{session_id}/messages", json={"content": "hello"})
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "pending"

        # Poll GET /sessions/{id} (fast, short interval) until the background
        # task finishes and the transcript shows the real reply.
        for _ in range(200):
            review = await client.get(f"/sessions/{session_id}")
            messages = review.json()["messages"]
            if messages[-1]["status"] in ("complete", "failed"):
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("assistant reply never settled")

        assert messages[-1]["status"] == "complete"
        assert messages[-1]["content"] == "async patient reply"
    finally:
        del app.dependency_overrides[get_post_message_use_case]
