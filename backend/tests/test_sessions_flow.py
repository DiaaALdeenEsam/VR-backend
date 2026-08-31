"""End-to-end flow: start a session -> chat -> order a test -> answer -> review."""

from __future__ import annotations


async def test_full_session_flow(client, seed_ids):
    # 1. start a session
    create_resp = await client.post("/sessions", json={"scenario_id": seed_ids["scenario_id"]})
    assert create_resp.status_code == 201
    session_id = create_resp.json()["session_id"]
    assert session_id

    # 2. chat: send a message, get a (stub) patient reply back, persisted
    message_resp = await client.post(
        f"/sessions/{session_id}/messages", json={"content": "هل تعاني من حمى؟"}
    )
    assert message_resp.status_code == 201
    message_body = message_resp.json()
    assert message_body["role"] == "assistant"
    assert message_body["content"]

    # 3. order a test: result only appears now, never before
    order_resp = await client.post(
        f"/sessions/{session_id}/tests", json={"test_id": seed_ids["test_id"]}
    )
    assert order_resp.status_code == 201
    order_body = order_resp.json()
    assert order_body["test_id"] == seed_ids["test_id"]
    assert order_body["result"] == "120/80 mmHg"

    # 4. answer the question -- correctness must NOT leak in this response
    answer_resp = await client.post(
        f"/sessions/{session_id}/answers",
        json={"question_id": seed_ids["question_id"], "choice_id": seed_ids["correct_choice_id"]},
    )
    assert answer_resp.status_code == 201
    answer_body = answer_resp.json()
    assert "is_correct" not in answer_body
    assert answer_body["choice_id"] == seed_ids["correct_choice_id"]

    # 5. re-answer with the wrong choice -- upsert replaces the prior answer
    reanswer_resp = await client.post(
        f"/sessions/{session_id}/answers",
        json={"question_id": seed_ids["question_id"], "choice_id": seed_ids["wrong_choice_id"]},
    )
    assert reanswer_resp.status_code == 201

    # 6. full review: everything shows up, correctness is now visible and reflects
    #    the *latest* answer (the wrong one), and there is exactly one answer row
    #    for this question (upsert, not a duplicate insert).
    review_resp = await client.get(f"/sessions/{session_id}")
    assert review_resp.status_code == 200
    review = review_resp.json()

    assert review["session_id"] == session_id
    assert review["scenario_id"] == seed_ids["scenario_id"]

    assert len(review["messages"]) == 2  # user + assistant
    assert [m["role"] for m in review["messages"]] == ["user", "assistant"]

    assert len(review["ordered_tests"]) == 1
    assert review["ordered_tests"][0]["test_id"] == seed_ids["test_id"]

    assert len(review["answers"]) == 1
    assert review["answers"][0]["choice_id"] == seed_ids["wrong_choice_id"]
    assert review["answers"][0]["is_correct"] is False


async def test_start_session_for_unknown_scenario_is_404(client):
    response = await client.post("/sessions", json={"scenario_id": 999999})
    assert response.status_code == 404


async def test_get_unknown_session_is_404(client):
    response = await client.get("/sessions/does-not-exist")
    assert response.status_code == 404


async def test_order_unknown_test_is_404(client, seed_ids):
    create_resp = await client.post("/sessions", json={"scenario_id": seed_ids["scenario_id"]})
    session_id = create_resp.json()["session_id"]

    response = await client.post(f"/sessions/{session_id}/tests", json={"test_id": 999999})
    assert response.status_code == 404


async def test_answer_with_choice_from_another_question_is_rejected(client, seed_ids):
    create_resp = await client.post("/sessions", json={"scenario_id": seed_ids["scenario_id"]})
    session_id = create_resp.json()["session_id"]

    response = await client.post(
        f"/sessions/{session_id}/answers",
        json={"question_id": seed_ids["question_id"], "choice_id": 999999},
    )
    assert response.status_code == 400
