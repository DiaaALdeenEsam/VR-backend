"""Tests for the OSCE evaluation feature: EvaluateSessionUseCase, the stub
EvaluationGenerator, and POST /sessions/{id}/evaluate.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from app.infrastructure.stub_evaluator import StubEvaluationGenerator

_QUIZ_CATEGORIES_IN_ORDER = [
    "diagnosis",
    "severity",
    "management_immediate",
    "management_monitoring",
    "management_disposition",
]


async def _seed_quiz_evaluation_scenario(session_factory: async_sessionmaker[AsyncSession]) -> dict:
    """A scenario with gold_standard set (so /evaluate is callable) plus 5
    post-session-quiz questions, one per category, each with 4 choices (the
    first choice is always the correct one). Local to this file rather than
    imported from tests/test_quiz.py -- same cross-file-private-helper
    convention as test_evaluate_session_async.py's WS test section."""

    from app.infrastructure.db.models import ChoiceModel, QuestionModel, ScenarioModel

    async with session_factory() as session:
        scenario = ScenarioModel(
            name="Asthma (quiz+evaluation test)", case_text="نص الحالة.", gold_standard="معيار مرجعي"
        )
        session.add(scenario)
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

            question.correct_choice_id = ids[0]  # choice 0 is always correct
            session.add(question)

            question_ids[category] = question.id
            choice_ids[category] = ids
        await session.commit()

        return {"scenario_id": scenario.id, "question_ids": question_ids, "choice_ids": choice_ids}


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
    # Chat-only assessment model: History taking, Patient communication,
    # Diagnostic reasoning -- no investigation-ordering/quiz criteria.
    assert len(evaluation.criteria_breakdown) == 3
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
    assert 0.0 <= body["score"] <= 100.0
    assert body["summary"].strip() != ""
    assert len(body["criteria_breakdown"]) == 3
    for criterion in body["criteria_breakdown"]:
        assert set(criterion.keys()) == {"name", "passed", "feedback"}

    # seed_ids' question has no category (an ordinary OSCE question, not part
    # of any post-session quiz) -- the merged `quiz` section must still be
    # present, just empty, not omitted or an error.
    assert body["quiz"] == {
        "total_questions": 0,
        "answered_count": 0,
        "correct_count": 0,
        "score": None,
        "questions": [],
    }


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


# ---------- post-session quiz merged into POST /sessions/{id}/evaluate --------
#
# The RAG-based conversation portion (score/summary/criteria_breakdown) comes
# from the stub EvaluationGenerator wired by tests/conftest.py's shared
# `client` fixture, exactly as in every other test in this file -- these
# tests only add assertions about the `quiz` section riding alongside it in
# the same response.


async def test_evaluate_response_includes_quiz_with_no_answers_submitted(
    client, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    quiz = await _seed_quiz_evaluation_scenario(session_factory)
    create_resp = await client.post("/sessions", json={"scenario_id": quiz["scenario_id"]})
    session_id = create_resp.json()["session_id"]

    # The RAG-based conversation evaluation portion is unaffected either way --
    # confirm it's still present and structurally unchanged.
    response = await client.post(f"/sessions/{session_id}/evaluate")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["score"], (int, float))
    assert len(body["criteria_breakdown"]) == 3

    quiz_body = body["quiz"]
    assert quiz_body["total_questions"] == 5
    assert quiz_body["answered_count"] == 0
    assert quiz_body["correct_count"] == 0
    assert quiz_body["score"] == 0.0  # seeded but unanswered -- 0.0, not null

    categories = [q["category"] for q in quiz_body["questions"]]
    assert categories == _QUIZ_CATEGORIES_IN_ORDER
    for q in quiz_body["questions"]:
        assert q["answered"] is False
        assert q["choice_id"] is None
        assert q["is_correct"] is None
        assert "correct_choice_id" not in q  # never leaked, same as GET /sessions/{id}/quiz


async def test_evaluate_response_includes_quiz_with_partial_answers(
    client, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    quiz = await _seed_quiz_evaluation_scenario(session_factory)
    create_resp = await client.post("/sessions", json={"scenario_id": quiz["scenario_id"]})
    session_id = create_resp.json()["session_id"]

    # Answer 2 of the 5 -- one correctly (diagnosis), one incorrectly (severity).
    await client.post(
        f"/sessions/{session_id}/answers",
        json={"question_id": quiz["question_ids"]["diagnosis"], "choice_id": quiz["choice_ids"]["diagnosis"][0]},
    )
    await client.post(
        f"/sessions/{session_id}/answers",
        json={"question_id": quiz["question_ids"]["severity"], "choice_id": quiz["choice_ids"]["severity"][1]},
    )

    response = await client.post(f"/sessions/{session_id}/evaluate")
    assert response.status_code == 200
    quiz_body = response.json()["quiz"]

    assert quiz_body["total_questions"] == 5
    assert quiz_body["answered_count"] == 2
    assert quiz_body["correct_count"] == 1
    assert quiz_body["score"] == 20.0  # 1/5 * 100

    by_category = {q["category"]: q for q in quiz_body["questions"]}
    assert by_category["diagnosis"]["answered"] is True
    assert by_category["diagnosis"]["is_correct"] is True
    assert by_category["severity"]["answered"] is True
    assert by_category["severity"]["is_correct"] is False
    for category in ("management_immediate", "management_monitoring", "management_disposition"):
        assert by_category[category]["answered"] is False
        assert by_category[category]["is_correct"] is None
        assert by_category[category]["choice_id"] is None


async def test_evaluate_response_includes_quiz_with_all_five_answered(
    client, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    quiz = await _seed_quiz_evaluation_scenario(session_factory)
    create_resp = await client.post("/sessions", json={"scenario_id": quiz["scenario_id"]})
    session_id = create_resp.json()["session_id"]

    # 3 correct (choice index 0), 2 incorrect (choice index 1) -- a mixed result.
    plan = {
        "diagnosis": 0,
        "severity": 0,
        "management_immediate": 1,
        "management_monitoring": 0,
        "management_disposition": 1,
    }
    for category, choice_index in plan.items():
        resp = await client.post(
            f"/sessions/{session_id}/answers",
            json={
                "question_id": quiz["question_ids"][category],
                "choice_id": quiz["choice_ids"][category][choice_index],
            },
        )
        assert resp.status_code == 201
        assert "is_correct" not in resp.json()  # POST /answers itself must never leak correctness

    response = await client.post(f"/sessions/{session_id}/evaluate")
    assert response.status_code == 200
    quiz_body = response.json()["quiz"]

    assert quiz_body["total_questions"] == 5
    assert quiz_body["answered_count"] == 5
    assert quiz_body["correct_count"] == 3
    assert quiz_body["score"] == 60.0  # 3/5 * 100

    by_category = {q["category"]: q for q in quiz_body["questions"]}
    for category, choice_index in plan.items():
        entry = by_category[category]
        assert entry["answered"] is True
        assert entry["is_correct"] is (choice_index == 0)
        assert entry["choice_id"] == quiz["choice_ids"][category][choice_index]
        assert "correct_choice_id" not in entry

    # The RAG-based conversation evaluation portion is present and unchanged
    # in shape, right alongside the quiz section -- both independently correct
    # in one payload.
    body = response.json()
    assert isinstance(body["score"], (int, float))
    assert body["summary"].strip() != ""
    assert len(body["criteria_breakdown"]) == 3
