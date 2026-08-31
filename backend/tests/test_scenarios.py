from __future__ import annotations

import pytest


@pytest.mark.usefixtures("seed_ids")
async def test_list_scenarios_returns_seeded_scenario(client, seed_ids):
    response = await client.get("/scenarios")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == seed_ids["scenario_id"]
    assert body[0]["name"] == "حالة اختبار"
    # case_text / gold_standard must never be exposed
    assert "case_text" not in body[0]
    assert "gold_standard" not in body[0]


async def test_list_scenarios_empty(client):
    response = await client.get("/scenarios")

    assert response.status_code == 200
    assert response.json() == []


async def test_get_questions_for_unknown_scenario_is_404(client):
    response = await client.get("/scenarios/999999/questions")

    assert response.status_code == 404


async def test_questions_do_not_leak_correct_choice_id(client, seed_ids):
    response = await client.get(f"/scenarios/{seed_ids['scenario_id']}/questions")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert "correct_choice_id" not in body[0]
    choice_ids = {c["id"] for c in body[0]["choices"]}
    assert choice_ids == {seed_ids["correct_choice_id"], seed_ids["wrong_choice_id"]}


async def test_tests_menu_does_not_leak_result(client, seed_ids):
    response = await client.get(f"/test-categories/{seed_ids['category_id']}/tests")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == seed_ids["test_id"]
    assert "result" not in body[0]


async def test_tests_for_unknown_category_is_404(client):
    response = await client.get("/test-categories/999999/tests")

    assert response.status_code == 404


async def test_health_check(client):
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}
