"""End-to-end coverage of all 11 REST endpoints, in one realistic medical-
simulation flow, seeded from the real seed_data/*.json files.

The `e2e_seed` fixture below calls the actual functions in
app/infrastructure/seed.py against the test's in-memory DB -- so this suite
also exercises the real seed script and the real seed_data content, not a
separate hand-rolled fixture. If seed_data/*.json ever goes stale or breaks
the loader, this suite catches it.

Patient replies here come from the stub generator via the synchronous
PostMessageUseCase (see tests/conftest.py's `app` fixture, which overrides
get_patient_reply_generator and get_post_message_use_case for all tests in
this suite) -- fast and deterministic. The real RAG-API-backed async path is
tests/test_post_message_async.py's job.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from app.infrastructure.seed import seed_questions, seed_scenarios, seed_test_categories, seed_tests

SEED_DATA_DIR = Path(__file__).resolve().parent.parent / "seed_data"


def _load(name: str) -> dict[str, Any]:
    with (SEED_DATA_DIR / name).open(encoding="utf-8") as f:
        return json.load(f)


@pytest_asyncio.fixture
async def e2e_seed(session_factory: async_sessionmaker[AsyncSession]) -> dict[str, Any]:
    """Loads seed_data/*.json into the test DB via the real seed script functions."""

    async with session_factory() as session:
        category_id_map = await seed_test_categories(session, _load("test_categories.json"))
        test_id_map = await seed_tests(session, _load("tests.json"), category_id_map)
        scenario_id_map = await seed_scenarios(session, _load("scenarios.json"))
        await seed_questions(session, _load("questions.json"), scenario_id_map)
        await session.commit()

    return {
        "scenario_with_gold_id": scenario_id_map["sc_appendicitis"],
        "scenario_without_gold_id": scenario_id_map["sc_gastritis"],
        "category_id": category_id_map["cat_lab"],
        "test_id": test_id_map["t_cbc"],
    }


async def test_full_medical_simulation_e2e_flow(client, e2e_seed: dict[str, Any]) -> None:
    scenario_id = e2e_seed["scenario_with_gold_id"]

    # 1. GET /health
    health_resp = await client.get("/health")
    assert health_resp.status_code == 200
    assert health_resp.json() == {"status": "ok", "database": "ok"}

    # 2. GET /scenarios
    scenarios_resp = await client.get("/scenarios")
    assert scenarios_resp.status_code == 200
    scenarios_body = scenarios_resp.json()
    assert {s["id"] for s in scenarios_body} >= {scenario_id, e2e_seed["scenario_without_gold_id"]}
    assert all("case_text" not in s and "gold_standard" not in s for s in scenarios_body)

    # 3. POST /sessions -- start a session for the appendicitis scenario
    create_resp = await client.post("/sessions", json={"scenario_id": scenario_id})
    assert create_resp.status_code == 201
    session_id = create_resp.json()["session_id"]
    assert session_id

    # 4. POST /sessions/{id}/messages -- send a message, get a patient reply
    message_resp = await client.post(
        f"/sessions/{session_id}/messages", json={"content": "أين يؤلمك بالضبط؟"}
    )
    assert message_resp.status_code == 201
    message_body = message_resp.json()
    assert message_body["role"] == "assistant"
    assert message_body["content"].strip() != ""

    # 5a. GET /test-categories
    categories_resp = await client.get("/test-categories")
    assert categories_resp.status_code == 200
    assert any(c["id"] == e2e_seed["category_id"] for c in categories_resp.json())

    # 5b. GET /test-categories/{id}/tests -- menu only, no result yet
    tests_resp = await client.get(f"/test-categories/{e2e_seed['category_id']}/tests")
    assert tests_resp.status_code == 200
    tests_body = tests_resp.json()
    assert any(t["id"] == e2e_seed["test_id"] for t in tests_body)
    assert all("result" not in t for t in tests_body)

    # 6. POST /sessions/{id}/tests -- order the CBC, get its result
    order_resp = await client.post(f"/sessions/{session_id}/tests", json={"test_id": e2e_seed["test_id"]})
    assert order_resp.status_code == 201
    order_body = order_resp.json()
    assert order_body["test_id"] == e2e_seed["test_id"]
    assert order_body["result"].strip() != ""

    # 7. GET /scenarios/{id}/questions -- no answer key
    questions_resp = await client.get(f"/scenarios/{scenario_id}/questions")
    assert questions_resp.status_code == 200
    questions_body = questions_resp.json()
    assert len(questions_body) == 2  # sc_appendicitis has 2 questions in seed_data
    first_question = questions_body[0]
    assert "correct_choice_id" not in first_question
    first_choice_id = first_question["choices"][0]["id"]

    # 8. POST /sessions/{id}/answers -- no correctness leak
    answer_resp = await client.post(
        f"/sessions/{session_id}/answers",
        json={"question_id": first_question["id"], "choice_id": first_choice_id},
    )
    assert answer_resp.status_code == 201
    assert "is_correct" not in answer_resp.json()

    # 9. GET /sessions/{id} -- full review, correctness now visible
    review_resp = await client.get(f"/sessions/{session_id}")
    assert review_resp.status_code == 200
    review_body = review_resp.json()
    assert len(review_body["messages"]) == 2  # user + assistant
    assert len(review_body["ordered_tests"]) == 1
    assert len(review_body["answers"]) == 1
    assert "is_correct" in review_body["answers"][0]

    # 10. POST /sessions/{id}/evaluate -- OSCE rubric, scenario has a gold_standard
    evaluate_resp = await client.post(f"/sessions/{session_id}/evaluate")
    assert evaluate_resp.status_code == 200
    evaluate_body = evaluate_resp.json()
    assert 0.0 <= evaluate_body["score"] <= 100.0
    assert evaluate_body["summary"].strip() != ""
    assert len(evaluate_body["criteria_breakdown"]) > 0
    for criterion in evaluate_body["criteria_breakdown"]:
        assert set(criterion.keys()) == {"name", "passed", "feedback"}


async def test_evaluate_on_scenario_without_gold_standard_is_400(client, e2e_seed: dict[str, Any]) -> None:
    """sc_gastritis in seed_data has gold_standard: null -- confirms the 400 path end to end."""

    create_resp = await client.post("/sessions", json={"scenario_id": e2e_seed["scenario_without_gold_id"]})
    assert create_resp.status_code == 201
    session_id = create_resp.json()["session_id"]

    response = await client.post(f"/sessions/{session_id}/evaluate")

    assert response.status_code == 400
