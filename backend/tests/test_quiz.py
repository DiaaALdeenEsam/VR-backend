"""Tests for the post-session MCQ quiz feature: Question.category,
GetPostSessionQuizUseCase, and GET /sessions/{id}/quiz.

Reuses the existing questions/choices/answers tables and endpoints (see
app/domain/repositories.py's QuestionRepository.list_post_session_quiz and
migration 0007) -- so scoring/upserting is already covered by
tests/test_sessions_flow.py; these tests focus on what's new: category
filtering/ordering and the new endpoint's shape.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from app.infrastructure.db.models import ChoiceModel, QuestionModel, ScenarioModel


async def _seed_quiz_scenario(session_factory: async_sessionmaker[AsyncSession]) -> dict:
    """A scenario with 5 categorized quiz questions (inserted out of clinical
    order, to prove list_post_session_quiz re-sorts rather than relying on
    insertion/id order) plus one ordinary, uncategorized OSCE question --
    the quiz endpoint must return only the 5, correctly ordered."""

    async with session_factory() as session:
        scenario = ScenarioModel(
            name="Asthma",
            case_text="...",
            gold_standard="...",
        )
        session.add(scenario)
        await session.flush()

        def add_question(text: str, category: str | None) -> QuestionModel:
            q = QuestionModel(scenario_id=scenario.id, text=text, correct_choice_id=-1, category=category)
            session.add(q)
            return q  # flushed below, ids assigned after

        specs = [
            ("management_disposition Q", "management_disposition"),
            ("diagnosis Q", "diagnosis"),
            ("plain OSCE Q (no category)", None),
            ("management_immediate Q", "management_immediate"),
            ("severity Q", "severity"),
            ("management_monitoring Q", "management_monitoring"),
        ]
        rows = [add_question(text, category) for text, category in specs]
        await session.flush()

        ids = {}
        for row in rows:
            assert row.id is not None
            correct = ChoiceModel(question_id=row.id, text=f"{row.text} correct choice")
            wrong = ChoiceModel(question_id=row.id, text=f"{row.text} wrong choice")
            session.add(correct)
            session.add(wrong)
            await session.flush()
            row.correct_choice_id = correct.id
            session.add(row)
            ids[row.category or "plain"] = {
                "question_id": row.id,
                "correct_choice_id": correct.id,
                "wrong_choice_id": wrong.id,
            }
        await session.commit()

        return {"scenario_id": scenario.id, **ids}


async def test_quiz_returns_only_categorized_questions_in_clinical_stage_order(
    client, session_factory
) -> None:
    quiz = await _seed_quiz_scenario(session_factory)
    create_resp = await client.post("/sessions", json={"scenario_id": quiz["scenario_id"]})
    session_id = create_resp.json()["session_id"]

    resp = await client.get(f"/sessions/{session_id}/quiz")
    assert resp.status_code == 200
    body = resp.json()

    assert body["session_id"] == session_id
    assert body["scenario_id"] == quiz["scenario_id"]

    categories = [q["category"] for q in body["questions"]]
    assert categories == [
        "diagnosis",
        "severity",
        "management_immediate",
        "management_monitoring",
        "management_disposition",
    ]

    # the uncategorized OSCE question must not appear
    ids_returned = {q["id"] for q in body["questions"]}
    assert quiz["plain"]["question_id"] not in ids_returned

    # correct_choice_id must never leak, same as GET /scenarios/{id}/questions
    for q in body["questions"]:
        assert "correct_choice_id" not in q
        assert len(q["choices"]) == 2


async def test_quiz_is_empty_when_scenario_has_no_quiz_questions_seeded(client, seed_ids) -> None:
    """seed_ids' question has no category -- an ordinary OSCE question, not
    part of any post-session quiz. An empty quiz is a valid 200, not a 404."""

    create_resp = await client.post("/sessions", json={"scenario_id": seed_ids["scenario_id"]})
    session_id = create_resp.json()["session_id"]

    resp = await client.get(f"/sessions/{session_id}/quiz")
    assert resp.status_code == 200
    assert resp.json()["questions"] == []


async def test_quiz_unknown_session_returns_404(client) -> None:
    resp = await client.get("/sessions/does-not-exist/quiz")
    assert resp.status_code == 404


async def test_quiz_answers_round_trip_via_existing_answer_and_review_endpoints(
    client, session_factory
) -> None:
    """The quiz endpoint only lists questions -- submitting/scoring still
    goes through the pre-existing POST /sessions/{id}/answers and
    GET /sessions/{id} endpoints, unchanged."""

    quiz = await _seed_quiz_scenario(session_factory)
    create_resp = await client.post("/sessions", json={"scenario_id": quiz["scenario_id"]})
    session_id = create_resp.json()["session_id"]

    # answer the severity-stage question correctly, the diagnosis-stage one incorrectly
    await client.post(
        f"/sessions/{session_id}/answers",
        json={
            "question_id": quiz["severity"]["question_id"],
            "choice_id": quiz["severity"]["correct_choice_id"],
        },
    )
    await client.post(
        f"/sessions/{session_id}/answers",
        json={
            "question_id": quiz["diagnosis"]["question_id"],
            "choice_id": quiz["diagnosis"]["wrong_choice_id"],
        },
    )

    review = (await client.get(f"/sessions/{session_id}")).json()
    by_question = {a["question_id"]: a["is_correct"] for a in review["answers"]}
    assert by_question[quiz["severity"]["question_id"]] is True
    assert by_question[quiz["diagnosis"]["question_id"]] is False
