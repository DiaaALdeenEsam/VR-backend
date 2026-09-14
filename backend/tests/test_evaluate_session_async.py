"""Tests for the async evaluation-generation path: EvaluateSessionAsyncUseCase
directly (fast, deterministic, no real network) plus API-level checks that
the router/deps wiring for this path (the production default --
app/api/deps.py's get_evaluate_session_use_case) actually works, including
the WS "evaluation" frame.

See app/infrastructure/rag_llm_evaluator.py for the real LLM-judge adapter
this feature exists to run asynchronously -- these tests use a fake/
controllable-delay EvaluationGenerator instead of the real network call,
exactly like tests/test_post_message_async.py uses a fake PatientReplyGenerator.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from app.application.use_cases.evaluate_session_async import EvaluateSessionAsyncUseCase
from app.config import get_settings
from app.domain.entities import EvaluationCriterion, SessionEvaluation, utcnow
from app.domain.exceptions import MissingGoldStandardError, NotFoundError, RagServiceUnavailableError, SessionBusyError
from app.domain.repositories import EvaluationGenerator
from app.infrastructure.db.repositories.unit_of_work import SqlAlchemyUnitOfWork
from app.infrastructure.inflight_sessions import InMemorySessionConcurrencyGuard


class _FakeEvaluationGenerator(EvaluationGenerator):
    """Controllable-delay/outcome EvaluationGenerator -- stands in for the
    real tens-of-seconds-per-call RagLlmEvaluator in every test below."""

    def __init__(
        self,
        *,
        delay: float = 0.0,
        result: SessionEvaluation | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.delay = delay
        self.result = result or SessionEvaluation(
            status="complete",
            score=87.5,
            summary="fake evaluation",
            criteria_breakdown=[EvaluationCriterion(name="Conversation quality", passed=True, feedback="ok")],
        )
        self.raises = raises
        self.calls = 0
        self.last_call_kwargs: dict | None = None

    async def evaluate(
        self,
        case_text: str,
        gold_standard: str,
        messages: list[dict[str, str]],
    ) -> SessionEvaluation:
        self.calls += 1
        self.last_call_kwargs = dict(
            case_text=case_text,
            gold_standard=gold_standard,
            messages=messages,
        )
        await asyncio.sleep(self.delay)
        if self.raises is not None:
            raise self.raises
        return self.result


class _RecordingPushPort:
    """Fake ReplyPushPort -- records every push()/push_bytes() call instead
    of needing a real WS connection."""

    def __init__(self) -> None:
        self.pushed: list[dict] = []

    async def push(self, session_id: str, payload: dict) -> bool:
        self.pushed.append(payload)
        return True

    async def push_bytes(self, session_id: str, data: bytes) -> bool:
        raise AssertionError("evaluation never pushes binary frames")


@pytest_asyncio.fixture
async def seeded_session(session_factory: async_sessionmaker[AsyncSession]) -> str:
    """A scenario (with gold_standard set) + session, created directly
    against the test DB -- same pattern as tests/test_post_message_async.py's
    own seeded_session fixture."""

    from app.infrastructure.db.models import ScenarioModel, SessionModel

    async with session_factory() as session:
        scenario = ScenarioModel(
            name="حالة تقييم غير متزامنة",
            case_text="نص الحالة.",
            gold_standard="المعيار المرجعي",
        )
        session.add(scenario)
        await session.flush()

        session_row = SessionModel(id=str(uuid.uuid4()), scenario_id=scenario.id, created_at=utcnow())
        session.add(session_row)
        await session.commit()
        return session_row.id


_QUIZ_CATEGORIES_IN_ORDER = [
    "diagnosis",
    "severity",
    "management_immediate",
    "management_monitoring",
    "management_disposition",
]


@pytest_asyncio.fixture
async def seeded_session_with_quiz(session_factory: async_sessionmaker[AsyncSession]) -> dict:
    """Same shape as `seeded_session` above, plus 5 post-session-quiz
    questions -- one per category (migration 0007) -- each with 4 choices
    (choice index 0 is always the correct one). Returns the ids tests need to
    submit specific answers directly against the DB (via _submit_answer
    below) before invoking the use case."""

    from app.infrastructure.db.models import ChoiceModel, QuestionModel, ScenarioModel, SessionModel

    async with session_factory() as session:
        scenario = ScenarioModel(
            name="حالة تقييم مع اختبار ما بعد المحادثة", case_text="نص الحالة.", gold_standard="المعيار المرجعي"
        )
        session.add(scenario)
        await session.flush()

        session_row = SessionModel(id=str(uuid.uuid4()), scenario_id=scenario.id, created_at=utcnow())
        session.add(session_row)
        await session.flush()

        question_ids: dict[str, int] = {}
        choice_ids: dict[str, list[int]] = {}
        for category in _QUIZ_CATEGORIES_IN_ORDER:
            question = QuestionModel(
                scenario_id=scenario.id, text=f"{category} question", correct_choice_id=-1, category=category
            )
            session.add(question)
            await session.flush()

            ids = []
            for i in range(4):
                choice = ChoiceModel(question_id=question.id, text=f"{category} choice {i}")
                session.add(choice)
                await session.flush()
                ids.append(choice.id)

            question.correct_choice_id = ids[0]
            session.add(question)
            question_ids[category] = question.id
            choice_ids[category] = ids
        await session.commit()

        return {"session_id": session_row.id, "question_ids": question_ids, "choice_ids": choice_ids}


async def _submit_answer(
    session_factory: async_sessionmaker[AsyncSession],
    session_id: str,
    question_id: int,
    choice_id: int,
    is_correct: bool,
) -> None:
    """Writes an Answer row directly (bypassing the API/AnswerQuestionUseCase)
    -- this file tests EvaluateSessionAsyncUseCase in isolation, the same
    reasoning `seeded_session`/`seeded_session_without_gold_standard` above
    write ScenarioModel/SessionModel rows directly rather than going through
    POST /sessions."""

    from app.infrastructure.db.models import AnswerModel

    async with session_factory() as session:
        session.add(
            AnswerModel(
                session_id=session_id,
                question_id=question_id,
                choice_id=choice_id,
                is_correct=is_correct,
                answered_at=utcnow(),
            )
        )
        await session.commit()


@pytest_asyncio.fixture
async def seeded_session_without_gold_standard(session_factory: async_sessionmaker[AsyncSession]) -> str:
    from app.infrastructure.db.models import ScenarioModel, SessionModel

    async with session_factory() as session:
        scenario = ScenarioModel(name="حالة بلا معيار", case_text="نص الحالة.", gold_standard=None)
        session.add(scenario)
        await session.flush()

        session_row = SessionModel(id=str(uuid.uuid4()), scenario_id=scenario.id, created_at=utcnow())
        session.add(session_row)
        await session.commit()
        return session_row.id


def _build_use_case(
    session_factory: async_sessionmaker[AsyncSession],
    evaluation_generator: EvaluationGenerator,
    push_port: _RecordingPushPort,
    concurrency_guard: InMemorySessionConcurrencyGuard,
    hard_timeout_seconds: float = 5.0,
) -> EvaluateSessionAsyncUseCase:
    settings = get_settings().model_copy(update={"rag_evaluation_hard_timeout_seconds": hard_timeout_seconds})
    return EvaluateSessionAsyncUseCase(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        evaluation_generator=evaluation_generator,
        settings=settings,
        concurrency_guard=concurrency_guard,
        push_port=push_port,
    )


async def _wait_until_settled(concurrency_guard: InMemorySessionConcurrencyGuard, session_id: str) -> None:
    for _ in range(200):  # up to ~2s
        if not concurrency_guard.is_busy(session_id):
            return
        await asyncio.sleep(0.01)
    raise AssertionError("background task never settled")


# ---------- immediate ack -----------------------------------------------------


async def test_execute_returns_pending_immediately(
    session_factory: async_sessionmaker[AsyncSession], seeded_session: str
) -> None:
    guard = InMemorySessionConcurrencyGuard()
    push_port = _RecordingPushPort()
    generator = _FakeEvaluationGenerator(delay=0.2)
    use_case = _build_use_case(session_factory, generator, push_port, guard)

    pending = await use_case.execute(seeded_session)

    assert pending.status == "pending"
    assert pending.score is None
    assert pending.summary is None
    assert pending.criteria_breakdown == []
    # seeded_session's scenario has no post-session-quiz questions seeded --
    # `quiz` must still be present (not None), just empty. See
    # test_pending_response_includes_computed_quiz_result below for the
    # populated case.
    assert pending.quiz is not None
    assert pending.quiz.total_questions == 0
    assert pending.quiz.score is None
    assert pending.quiz.questions == []
    assert generator.calls == 0  # generation hasn't necessarily even started synchronously
    assert guard.is_busy(seeded_session) is True

    await _wait_until_settled(guard, seeded_session)


# ---------- post-session quiz merged into the pending ack / WS pushes --------
#
# Quiz scoring is a local DB lookup, never routed through EvaluationGenerator/
# the RAG API -- these tests use the same _FakeEvaluationGenerator as every
# other test in this file for the conversation portion; only the `quiz`
# section is new here.


async def test_pending_response_includes_computed_quiz_result(
    session_factory: async_sessionmaker[AsyncSession], seeded_session_with_quiz: dict
) -> None:
    ids = seeded_session_with_quiz
    session_id = ids["session_id"]

    # diagnosis answered correctly, severity answered incorrectly, the other
    # 3 categories left unanswered.
    await _submit_answer(
        session_factory, session_id, ids["question_ids"]["diagnosis"], ids["choice_ids"]["diagnosis"][0], True
    )
    await _submit_answer(
        session_factory, session_id, ids["question_ids"]["severity"], ids["choice_ids"]["severity"][1], False
    )

    guard = InMemorySessionConcurrencyGuard()
    push_port = _RecordingPushPort()
    generator = _FakeEvaluationGenerator(delay=0.2)  # slow -- proves quiz doesn't wait on it
    use_case = _build_use_case(session_factory, generator, push_port, guard)

    pending = await use_case.execute(session_id)

    assert pending.status == "pending"
    assert generator.calls == 0  # RAG call needn't have started -- quiz result didn't wait for it either

    quiz = pending.quiz
    assert quiz is not None
    assert quiz.total_questions == 5
    assert quiz.answered_count == 2
    assert quiz.correct_count == 1
    assert quiz.score == 20.0  # 1/5 * 100

    categories = [q.category for q in quiz.questions]
    assert categories == _QUIZ_CATEGORIES_IN_ORDER
    assert quiz.questions[0].answered is True and quiz.questions[0].is_correct is True  # diagnosis
    assert quiz.questions[1].answered is True and quiz.questions[1].is_correct is False  # severity
    for unanswered in quiz.questions[2:]:
        assert unanswered.answered is False
        assert unanswered.choice_id is None
        assert unanswered.is_correct is None

    await _wait_until_settled(guard, session_id)


async def test_successful_push_includes_quiz_result(
    session_factory: async_sessionmaker[AsyncSession], seeded_session_with_quiz: dict
) -> None:
    ids = seeded_session_with_quiz
    session_id = ids["session_id"]
    await _submit_answer(
        session_factory,
        session_id,
        ids["question_ids"]["management_disposition"],
        ids["choice_ids"]["management_disposition"][0],
        True,
    )

    guard = InMemorySessionConcurrencyGuard()
    push_port = _RecordingPushPort()
    generator = _FakeEvaluationGenerator(delay=0.05)
    use_case = _build_use_case(session_factory, generator, push_port, guard)

    await use_case.execute(session_id)
    await _wait_until_settled(guard, session_id)

    evaluation_pushes = [p for p in push_port.pushed if p["type"] == "evaluation"]
    assert len(evaluation_pushes) == 1
    pushed = evaluation_pushes[0]
    assert pushed["status"] == "complete"
    assert pushed["score"] == 87.5  # _FakeEvaluationGenerator's default result

    quiz = pushed["quiz"]
    assert quiz["total_questions"] == 5
    assert quiz["answered_count"] == 1
    assert quiz["correct_count"] == 1
    assert quiz["score"] == 20.0
    by_category = {q["category"]: q for q in quiz["questions"]}
    assert by_category["management_disposition"]["answered"] is True
    assert by_category["management_disposition"]["is_correct"] is True
    for q in quiz["questions"]:
        assert "correct_choice_id" not in q  # never leaked


async def test_failed_push_still_includes_quiz_result(
    session_factory: async_sessionmaker[AsyncSession], seeded_session_with_quiz: dict
) -> None:
    """A failed/timed-out RAG conversation evaluation must not drop the
    already-computed, purely-local quiz result -- the two are independent."""

    ids = seeded_session_with_quiz
    session_id = ids["session_id"]
    await _submit_answer(
        session_factory, session_id, ids["question_ids"]["diagnosis"], ids["choice_ids"]["diagnosis"][0], True
    )

    guard = InMemorySessionConcurrencyGuard()
    push_port = _RecordingPushPort()
    generator = _FakeEvaluationGenerator(delay=1.0)
    use_case = _build_use_case(session_factory, generator, push_port, guard, hard_timeout_seconds=0.1)

    await use_case.execute(session_id)
    await _wait_until_settled(guard, session_id)

    evaluation_pushes = [p for p in push_port.pushed if p["type"] == "evaluation"]
    assert len(evaluation_pushes) == 1
    pushed = evaluation_pushes[0]
    assert pushed["status"] == "failed"
    assert pushed["score"] is None

    quiz = pushed["quiz"]
    assert quiz["total_questions"] == 5
    assert quiz["answered_count"] == 1
    assert quiz["correct_count"] == 1
    assert quiz["score"] == 20.0


# ---------- success path: real evaluation lands, gets pushed, score computed -


async def test_successful_evaluation_pushes_complete_result(
    session_factory: async_sessionmaker[AsyncSession], seeded_session: str
) -> None:
    guard = InMemorySessionConcurrencyGuard()
    push_port = _RecordingPushPort()
    result = SessionEvaluation(
        status="complete",
        score=91.0,
        summary="great job",
        criteria_breakdown=[
            EvaluationCriterion(name="Conversation quality", passed=True, feedback="thorough"),
        ],
    )
    generator = _FakeEvaluationGenerator(delay=0.05, result=result)
    use_case = _build_use_case(session_factory, generator, push_port, guard)

    await use_case.execute(seeded_session)
    await _wait_until_settled(guard, seeded_session)

    assert generator.calls == 1
    evaluation_pushes = [p for p in push_port.pushed if p["type"] == "evaluation"]
    assert len(evaluation_pushes) == 1
    pushed = evaluation_pushes[0]
    assert pushed["status"] == "complete"
    assert pushed["score"] == 91.0
    assert pushed["summary"] == "great job"
    assert pushed["criteria_breakdown"] == [
        {"name": "Conversation quality", "passed": True, "feedback": "thorough"},
    ]


# ---------- timeout: hard deadline exceeded -> failed, no fallback score -----


async def test_timeout_pushes_failed_with_no_fallback_score(
    session_factory: async_sessionmaker[AsyncSession], seeded_session: str
) -> None:
    guard = InMemorySessionConcurrencyGuard()
    push_port = _RecordingPushPort()
    generator = _FakeEvaluationGenerator(delay=1.0)
    use_case = _build_use_case(session_factory, generator, push_port, guard, hard_timeout_seconds=0.1)

    await use_case.execute(seeded_session)
    await _wait_until_settled(guard, seeded_session)

    evaluation_pushes = [p for p in push_port.pushed if p["type"] == "evaluation"]
    assert len(evaluation_pushes) == 1
    pushed = evaluation_pushes[0]
    assert pushed["status"] == "failed"
    assert pushed["score"] is None
    assert pushed["summary"] is None
    assert pushed["criteria_breakdown"] == []
    assert "timed out" in pushed["detail"]


# ---------- RagServiceUnavailableError -> failed, no fallback score ----------


async def test_rag_service_unavailable_pushes_failed_with_no_fallback_score(
    session_factory: async_sessionmaker[AsyncSession], seeded_session: str
) -> None:
    guard = InMemorySessionConcurrencyGuard()
    push_port = _RecordingPushPort()
    generator = _FakeEvaluationGenerator(raises=RagServiceUnavailableError("RAG chat API", "returned HTTP 503"))
    use_case = _build_use_case(session_factory, generator, push_port, guard)

    await use_case.execute(seeded_session)
    await _wait_until_settled(guard, seeded_session)

    evaluation_pushes = [p for p in push_port.pushed if p["type"] == "evaluation"]
    assert len(evaluation_pushes) == 1
    pushed = evaluation_pushes[0]
    assert pushed["status"] == "failed"
    assert pushed["score"] is None
    assert "RAG chat API" in pushed["detail"]


# ---------- concurrency: second evaluate while first in flight -> 409 -------


async def test_second_evaluate_while_first_in_flight_raises_session_busy(
    session_factory: async_sessionmaker[AsyncSession], seeded_session: str
) -> None:
    guard = InMemorySessionConcurrencyGuard()
    push_port = _RecordingPushPort()
    generator = _FakeEvaluationGenerator(delay=0.3)
    use_case = _build_use_case(session_factory, generator, push_port, guard)

    await use_case.execute(seeded_session)
    assert guard.is_busy(seeded_session) is True

    try:
        await use_case.execute(seeded_session)
        raised = False
    except SessionBusyError as exc:
        raised = True
        assert exc.operation == "evaluation"
        assert "evaluation" in str(exc)

    assert raised is True
    await _wait_until_settled(guard, seeded_session)


async def test_a_new_evaluate_is_accepted_once_the_first_has_settled(
    session_factory: async_sessionmaker[AsyncSession], seeded_session: str
) -> None:
    guard = InMemorySessionConcurrencyGuard()
    push_port = _RecordingPushPort()
    generator = _FakeEvaluationGenerator(delay=0.05)
    use_case = _build_use_case(session_factory, generator, push_port, guard)

    await use_case.execute(seeded_session)
    await _wait_until_settled(guard, seeded_session)
    assert guard.is_busy(seeded_session) is False

    second = await use_case.execute(seeded_session)
    assert second.status == "pending"
    await _wait_until_settled(guard, seeded_session)
    assert generator.calls == 2


# ---------- synchronous validation: raised before "pending" goes out --------


async def test_unknown_session_raises_not_found_synchronously(session_factory: async_sessionmaker[AsyncSession]) -> None:
    guard = InMemorySessionConcurrencyGuard()
    push_port = _RecordingPushPort()
    generator = _FakeEvaluationGenerator()
    use_case = _build_use_case(session_factory, generator, push_port, guard)

    with pytest.raises(NotFoundError):
        await use_case.execute("does-not-exist")

    assert guard.is_busy("does-not-exist") is False
    assert generator.calls == 0


async def test_missing_gold_standard_raises_synchronously(
    session_factory: async_sessionmaker[AsyncSession], seeded_session_without_gold_standard: str
) -> None:
    guard = InMemorySessionConcurrencyGuard()
    push_port = _RecordingPushPort()
    generator = _FakeEvaluationGenerator()
    use_case = _build_use_case(session_factory, generator, push_port, guard)

    with pytest.raises(MissingGoldStandardError):
        await use_case.execute(seeded_session_without_gold_standard)

    assert guard.is_busy(seeded_session_without_gold_standard) is False
    assert generator.calls == 0


# ---------- pending/generating messages excluded from the graded transcript -


async def test_pending_and_generating_messages_are_excluded_from_transcript(
    session_factory: async_sessionmaker[AsyncSession], seeded_session: str
) -> None:
    from app.infrastructure.db.models import MessageModel

    async with session_factory() as session:
        session.add(
            MessageModel(session_id=seeded_session, role="user", content="a real question", created_at=utcnow())
        )
        session.add(
            MessageModel(
                session_id=seeded_session, role="assistant", content="", created_at=utcnow(), status="generating"
            )
        )
        await session.commit()

    guard = InMemorySessionConcurrencyGuard()
    push_port = _RecordingPushPort()
    generator = _FakeEvaluationGenerator(delay=0.02)
    use_case = _build_use_case(session_factory, generator, push_port, guard)

    await use_case.execute(seeded_session)
    await _wait_until_settled(guard, seeded_session)

    assert generator.last_call_kwargs is not None
    assert generator.last_call_kwargs["messages"] == [{"role": "user", "content": "a real question"}]


# ---------- API-level: confirm the router really wires the async use case ---


async def test_evaluate_endpoint_uses_async_path_when_wired_to_it(client, seed_ids, app) -> None:
    """tests/conftest.py's shared `app` fixture overrides
    get_evaluate_session_use_case to the synchronous EvaluateSessionUseCase by
    default (so the rest of the general test suite keeps its fast, immediate-
    score assertions unchanged -- see tests/test_evaluation.py). This test
    overrides it back to a real EvaluateSessionAsyncUseCase -- using the exact
    same construction app/api/deps.py's get_evaluate_session_use_case uses in
    production -- to prove the router/response-schema wiring for the async
    path actually works end to end (ack "pending", push "complete" over WS
    later)."""

    from app.api.deps import get_evaluate_session_use_case, get_reply_push_port, get_uow_factory

    fake_generator = _FakeEvaluationGenerator(delay=0.05)
    uow_factory = app.dependency_overrides[get_uow_factory]()
    guard = InMemorySessionConcurrencyGuard()
    push_port = get_reply_push_port()  # the real WS hub singleton -- nothing listens in this test, that's fine

    def override_with_async_use_case() -> EvaluateSessionAsyncUseCase:
        return EvaluateSessionAsyncUseCase(
            uow_factory=uow_factory,
            evaluation_generator=fake_generator,
            settings=get_settings().model_copy(update={"rag_evaluation_hard_timeout_seconds": 2.0}),
            concurrency_guard=guard,
            push_port=push_port,
        )

    app.dependency_overrides[get_evaluate_session_use_case] = override_with_async_use_case
    try:
        create_resp = await client.post("/sessions", json={"scenario_id": seed_ids["scenario_id"]})
        session_id = create_resp.json()["session_id"]

        response = await client.post(f"/sessions/{session_id}/evaluate")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "pending"
        assert body["score"] is None
        assert body["summary"] is None
        assert body["criteria_breakdown"] == []
        # seed_ids' question has no category -- quiz section present but empty.
        assert body["quiz"] == {
            "total_questions": 0,
            "answered_count": 0,
            "correct_count": 0,
            "score": None,
            "questions": [],
        }

        # A second call while the first is still in flight is a 409.
        second = await client.post(f"/sessions/{session_id}/evaluate")
        assert second.status_code == 409

        await _wait_until_settled(guard, session_id)
        assert fake_generator.calls == 1
    finally:
        del app.dependency_overrides[get_evaluate_session_use_case]


# ---------- WS: the real "evaluation" frame arrives on /ws/voice ------------
#
# Same reasoning/setup as tests/test_voice.py's WS section: httpx.AsyncClient's
# ASGITransport can't do a WebSocket upgrade, so this needs
# fastapi.testclient.TestClient (synchronous, its own event loop) instead --
# which in turn needs its own fully self-contained app/DB setup, not the
# shared `engine`/`session_factory` fixtures (loop/thread affinity). See that
# module's own docstring for the full explanation; this test duplicates the
# same small amount of setup rather than importing test_voice.py's
# underscore-prefixed (private-by-convention) helpers across files.


def _run_migrations() -> None:
    from alembic import command
    from alembic.config import Config

    backend_dir = Path(__file__).resolve().parent.parent
    alembic_cfg = Config(str(backend_dir / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(alembic_cfg, "head")


async def _seed_scenario_with_gold_standard() -> dict:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.infrastructure.db.models import ChoiceModel, QuestionModel, ScenarioModel

    engine = create_async_engine(get_settings().database_url)
    try:
        session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with session_factory() as session:
            scenario = ScenarioModel(name="سيناريو تقييم WS", case_text="نص الحالة.", gold_standard="معيار مرجعي")
            session.add(scenario)
            await session.flush()

            # One post-session-quiz question (diagnosis) -- enough to prove
            # `quiz` rides along on the real WS "evaluation" frame, without
            # needing all 5 categories for this particular check.
            question = QuestionModel(
                scenario_id=scenario.id, text="ws quiz question", correct_choice_id=-1, category="diagnosis"
            )
            session.add(question)
            await session.flush()
            correct = ChoiceModel(question_id=question.id, text="correct")
            wrong = ChoiceModel(question_id=question.id, text="wrong")
            session.add(correct)
            session.add(wrong)
            await session.flush()
            question.correct_choice_id = correct.id
            session.add(question)

            await session.commit()
            assert scenario.id is not None
            return {"scenario_id": scenario.id, "question_id": question.id, "correct_choice_id": correct.id}
    finally:
        await engine.dispose()


def test_evaluate_websocket_pushes_evaluation_frame(tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from app.api.deps import get_evaluate_session_use_case, get_reply_push_port, get_uow_factory
    from app.infrastructure.db.repositories.unit_of_work import SqlAlchemyUnitOfWork
    from app.main import create_app

    db_path = tmp_path / "eval_ws_test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    get_settings.cache_clear()

    _run_migrations()
    seeded = asyncio.run(_seed_scenario_with_gold_standard())
    scenario_id = seeded["scenario_id"]

    fastapi_app = create_app()

    def uow_factory() -> SqlAlchemyUnitOfWork:
        from app.infrastructure.db.engine import get_session_factory

        return SqlAlchemyUnitOfWork(get_session_factory())

    fake_generator = _FakeEvaluationGenerator(
        delay=0.05,
        result=SessionEvaluation(
            status="complete",
            score=75.0,
            summary="ws test summary",
            criteria_breakdown=[EvaluationCriterion(name="Conversation quality", passed=True, feedback="fine")],
        ),
    )

    def override_with_async_use_case() -> EvaluateSessionAsyncUseCase:
        return EvaluateSessionAsyncUseCase(
            uow_factory=uow_factory,
            evaluation_generator=fake_generator,
            settings=get_settings().model_copy(update={"rag_evaluation_hard_timeout_seconds": 2.0}),
            concurrency_guard=InMemorySessionConcurrencyGuard(),
            push_port=get_reply_push_port(),
        )

    fastapi_app.dependency_overrides[get_evaluate_session_use_case] = override_with_async_use_case

    try:
        with TestClient(fastapi_app) as test_client:
            create_resp = test_client.post("/sessions", json={"scenario_id": scenario_id})
            assert create_resp.status_code == 201
            session_id = create_resp.json()["session_id"]

            answer_resp = test_client.post(
                f"/sessions/{session_id}/answers",
                json={"question_id": seeded["question_id"], "choice_id": seeded["correct_choice_id"]},
            )
            assert answer_resp.status_code == 201

            with test_client.websocket_connect(f"/ws/voice?session_id={session_id}") as ws:
                eval_resp = test_client.post(f"/sessions/{session_id}/evaluate")
                assert eval_resp.status_code == 200
                pending_body = eval_resp.json()
                assert pending_body["status"] == "pending"
                # quiz is already fully computed in the immediate ack -- it
                # doesn't wait for the RAG push like score/summary do.
                assert pending_body["quiz"]["total_questions"] == 1
                assert pending_body["quiz"]["answered_count"] == 1
                assert pending_body["quiz"]["correct_count"] == 1

                frame = ws.receive_json()
                assert frame["type"] == "evaluation"
                assert frame["status"] == "complete"
                assert frame["score"] == 75.0
                assert frame["summary"] == "ws test summary"
                # quiz rides along on the real pushed WS frame too, same shape.
                assert frame["quiz"]["total_questions"] == 1
                assert frame["quiz"]["answered_count"] == 1
                assert frame["quiz"]["correct_count"] == 1
                assert frame["quiz"]["score"] == 100.0
                assert frame["quiz"]["questions"][0]["category"] == "diagnosis"
                assert frame["quiz"]["questions"][0]["is_correct"] is True
                assert "correct_choice_id" not in frame["quiz"]["questions"][0]
                assert frame["criteria_breakdown"] == [
                    {"name": "Conversation quality", "passed": True, "feedback": "fine"}
                ]
    finally:
        get_settings.cache_clear()
