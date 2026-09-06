"""Tests for the OSCE evaluation feature: EvaluateSessionUseCase, the stub
EvaluationGenerator, and POST /sessions/{id}/evaluate.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from app.infrastructure.stub_evaluator import StubEvaluationGenerator


# ---------- StubEvaluationGenerator, tested directly (no DB, no HTTP) ----------


async def test_stub_evaluator_returns_realistic_shape() -> None:
    generator = StubEvaluationGenerator()

    evaluation = await generator.evaluate(
        case_text="مريض يشكو من ألم في البطن.",
        gold_standard="التهاب الزائدة الدودية الحاد",
        messages=[
            {"role": "user", "content": "أين يؤلمك؟"},
            {"role": "assistant", "content": "في الجهة اليمنى."},
            {"role": "user", "content": "منذ متى؟"},
        ],
        ordered_tests=[{"test_id": 1}],
        answers=[{"question_id": 1, "choice_id": 1, "is_correct": True}],
        relevant_test_ids=[1, 2],
        total_questions=2,
    )

    assert isinstance(evaluation.score, float)
    assert 0.0 <= evaluation.score <= 100.0
    assert evaluation.summary.strip() != ""
    # History taking, Patient communication, Diagnostic reasoning (40% bucket),
    # Investigation appropriateness (30%), Quiz performance (30%).
    assert len(evaluation.criteria_breakdown) == 5
    for criterion in evaluation.criteria_breakdown:
        assert criterion.name.strip() != ""
        assert isinstance(criterion.passed, bool)
        assert criterion.feedback.strip() != ""


async def test_stub_evaluator_flags_thin_history_taking() -> None:
    generator = StubEvaluationGenerator()

    evaluation = await generator.evaluate(
        case_text="...",
        gold_standard="...",
        messages=[{"role": "user", "content": "مرحبًا"}],  # only one doctor message
        ordered_tests=[],
        answers=[],
        relevant_test_ids=[],
        total_questions=0,
    )

    history_criterion = next(c for c in evaluation.criteria_breakdown if c.name == "History taking")
    assert history_criterion.passed is False


async def test_stub_evaluator_weights_test_ordering_and_quiz() -> None:
    """Locks in the actual point of this feature: a session that ordered the
    relevant tests and answered the quiz correctly must score meaningfully
    higher than one that did neither -- before this feature, both would have
    scored identically (chat transcript was the only signal used)."""

    generator = StubEvaluationGenerator()
    messages = [
        {"role": "user", "content": "أين يؤلمك؟"},
        {"role": "assistant", "content": "في الجهة اليمنى."},
        {"role": "user", "content": "منذ متى؟"},
    ]

    full_credit = await generator.evaluate(
        case_text="...",
        gold_standard="...",
        messages=messages,
        ordered_tests=[{"test_id": 1}, {"test_id": 2}],
        answers=[
            {"question_id": 1, "choice_id": 1, "is_correct": True},
            {"question_id": 2, "choice_id": 3, "is_correct": True},
        ],
        relevant_test_ids=[1, 2],
        total_questions=2,
    )

    no_credit = await generator.evaluate(
        case_text="...",
        gold_standard="...",
        messages=messages,
        ordered_tests=[],
        answers=[],
        relevant_test_ids=[1, 2],
        total_questions=2,
    )

    assert full_credit.score > no_credit.score

    investigation = next(c for c in full_credit.criteria_breakdown if c.name == "Investigation appropriateness")
    quiz = next(c for c in full_credit.criteria_breakdown if c.name == "Quiz performance")
    assert investigation.passed is True
    assert quiz.passed is True

    investigation_zero = next(
        c for c in no_credit.criteria_breakdown if c.name == "Investigation appropriateness"
    )
    quiz_zero = next(c for c in no_credit.criteria_breakdown if c.name == "Quiz performance")
    assert investigation_zero.passed is False
    assert quiz_zero.passed is False


# ---------- EvaluateSessionUseCase / POST /sessions/{id}/evaluate, via the API ----------


async def test_evaluate_session_returns_structured_evaluation(client, seed_ids) -> None:
    create_resp = await client.post("/sessions", json={"scenario_id": seed_ids["scenario_id"]})
    session_id = create_resp.json()["session_id"]

    response = await client.post(f"/sessions/{session_id}/evaluate")

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["score"], (int, float))
    assert 0.0 <= body["score"] <= 100.0
    assert body["summary"].strip() != ""
    assert len(body["criteria_breakdown"]) == 5
    for criterion in body["criteria_breakdown"]:
        assert set(criterion.keys()) == {"name", "passed", "feedback"}


async def test_evaluate_unknown_session_is_404(client) -> None:
    response = await client.post("/sessions/does-not-exist/evaluate")
    assert response.status_code == 404


async def test_evaluate_session_without_gold_standard_is_400(
    client, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    from app.infrastructure.db.models import ScenarioModel

    async with session_factory() as session:
        scenario = ScenarioModel(
            name="سيناريو بلا معيار مرجعي",
            case_text="نص الحالة.",
            gold_standard=None,
        )
        session.add(scenario)
        await session.commit()
        scenario_id = scenario.id

    create_resp = await client.post("/sessions", json={"scenario_id": scenario_id})
    assert create_resp.status_code == 201
    session_id = create_resp.json()["session_id"]

    response = await client.post(f"/sessions/{session_id}/evaluate")

    assert response.status_code == 400
