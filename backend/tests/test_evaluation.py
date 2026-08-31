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
    )

    assert isinstance(evaluation.score, float)
    assert 0.0 <= evaluation.score <= 100.0
    assert evaluation.summary.strip() != ""
    assert len(evaluation.criteria_breakdown) > 0
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
    )

    history_criterion = next(c for c in evaluation.criteria_breakdown if c.name == "History taking")
    assert history_criterion.passed is False


# ---------- EvaluateSessionUseCase / POST /sessions/{id}/evaluate, via the API ----------


async def test_evaluate_session_returns_structured_evaluation(client, seed_ids) -> None:
    create_resp = await client.post("/sessions", json={"scenario_id": seed_ids["scenario_id"]})
    session_id = create_resp.json()["session_id"]

    response = await client.post(f"/sessions/{session_id}/evaluate")

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["score"], (int, float))
    assert body["summary"].strip() != ""
    assert len(body["criteria_breakdown"]) > 0
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
