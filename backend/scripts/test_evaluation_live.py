"""Manual, human-driven live test of the async LLM-judge evaluation flow,
end to end, against a REAL running server and the REAL external
/v1/rag/chat API -- no mocks, no stubs.

Runs two sessions for a given scenario (default: scenario_id=2, "Acalculous
cholecystitis" -- see converted_json/acalculous_cholecystitis_scenario.json)
and prints the full real LLM-judge result for each:

  1. "good" session -- a short realistic conversation, all of the scenario's
     clinically-relevant tests ordered, all quiz questions answered
     correctly.
  2. "did nothing" session -- one perfunctory message (POST /sessions/{id}/
     evaluate requires messages to exist as a *possible* signal, but nothing
     else -- no tests ordered, no quiz answered).

Which tests are "relevant" and which choice is "correct" per question are
NOT hardcoded per scenario -- they're looked up fresh for whatever
--scenario-id is passed: relevant tests via the real public endpoint
(GET /scenarios/{id}/relevant-tests, the same guidance a real client would
see), and correct choices via a direct read of the local app.db (the public
API deliberately never exposes correct_choice_id -- see
GET /scenarios/{id}/questions's no-leak contract -- so there is no public
endpoint this could come from; this mirrors
scripts/run_scenario_pipeline.py's own step_quiz(), which reads its seeded
correct_choice_id back from the DB rather than the API for the same reason).
This makes the script scenario-agnostic: run it against any scenario_id
without editing constants first.

For each session: POST /sessions/{id}/evaluate, confirm the immediate
"pending" response, then listen on WS /ws/voice?session_id=... (opened
*before* posting evaluate, same race-avoidance as test_voice_ws_live.py) for
the real `{"type": "evaluation", ...}` push, and print the full result --
all three section scores, their feedback text, overall_summary, and the
final weighted score -- plus the observed round-trip latency.

Usage (from backend/, PYTHONPATH=. so `app` resolves):

    PYTHONPATH=. python scripts/test_evaluation_live.py [--scenario-id 2] [--base-url http://127.0.0.1:8199]

Requires EVALUATION_BACKEND=rag_llm and a working RAG_API_BASE_URL/
RAG_API_KEY in the running server's environment (see .env) -- this is a
deliberate real-network test, not a mocked one.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time

import httpx
import websockets

from app.config import get_settings
from app.infrastructure.db.engine import create_engine_from_settings, create_session_factory
from app.infrastructure.db.models import QuestionModel

# Matches Settings.rag_evaluation_hard_timeout_seconds (100s default, see
# app/config.py) plus margin for scheduling/DB overhead around the call
# itself -- same reasoning as test_voice_ws_live.py's own
# DEFAULT_TIMEOUT_SECONDS relative to rag_patient_hard_timeout_seconds.
DEFAULT_TIMEOUT_SECONDS = 100.0 + 20.0

GOOD_SESSION_MESSAGES = [
    "Hello, I'm Dr. Amin. What's been bothering you?",
    "Have you had any fever, chills, or right-sided abdominal pain recently, especially after your surgery?",
]


async def _fetch_relevant_test_ids(client: httpx.AsyncClient, scenario_id: int) -> list[int]:
    r = await client.get(f"/scenarios/{scenario_id}/relevant-tests")
    if r.status_code != 200:
        _log(f"ERROR: GET /relevant-tests failed (status={r.status_code}): {r.text}")
        raise SystemExit(1)
    return [t["id"] for t in r.json()]


async def _fetch_correct_answers(scenario_id: int) -> list[tuple[int, int]]:
    """(question_id, correct_choice_id) pairs, read directly from the DB --
    see module docstring for why this can't come from the public API."""

    settings = get_settings()
    engine = create_engine_from_settings(settings)
    try:
        session_factory = create_session_factory(engine)
        async with session_factory() as session:
            from sqlmodel import select

            result = await session.exec(
                select(QuestionModel).where(QuestionModel.scenario_id == scenario_id).order_by(QuestionModel.id)
            )
            return [(q.id, q.correct_choice_id) for q in result.all()]
    finally:
        await engine.dispose()


def _ts() -> str:
    return time.strftime("%H:%M:%S", time.localtime()) + f".{int(time.time() * 1000) % 1000:03d}"


def _log(message: str) -> None:
    print(f"[{_ts()}] {message}")


def _ws_url(base_url: str, session_id: str) -> str:
    ws_base = base_url.replace("https://", "wss://").replace("http://", "ws://")
    return f"{ws_base}/ws/voice?session_id={session_id}"


async def _create_session(client: httpx.AsyncClient, scenario_id: int) -> str:
    r = await client.post("/sessions", json={"scenario_id": scenario_id})
    if r.status_code != 201:
        _log(f"ERROR: could not create session (status={r.status_code}): {r.text}")
        raise SystemExit(1)
    session_id = r.json()["session_id"]
    _log(f"Created session {session_id!r} for scenario_id={scenario_id}")
    return session_id


async def _send_message_and_wait(client: httpx.AsyncClient, session_id: str, content: str) -> str:
    r = await client.post(f"/sessions/{session_id}/messages", json={"content": content})
    if r.status_code != 201:
        _log(f"ERROR: POST /messages failed (status={r.status_code}): {r.text}")
        raise SystemExit(1)
    message_id = r.json()["id"]
    _log(f'Doctor: "{content}"')

    deadline = time.monotonic() + 70.0
    while time.monotonic() < deadline:
        review = await client.get(f"/sessions/{session_id}")
        messages = review.json()["messages"]
        message = next(m for m in messages if m["id"] == message_id)
        if message["status"] in ("complete", "failed"):
            _log(f'Patient ({message["status"]}): "{message["content"]}"')
            return message["content"]
        await asyncio.sleep(0.5)
    raise SystemExit(f"message {message_id} in session {session_id} never settled")


async def _order_test(client: httpx.AsyncClient, session_id: str, test_id: int) -> None:
    r = await client.post(f"/sessions/{session_id}/tests", json={"test_id": test_id})
    if r.status_code != 201:
        _log(f"ERROR: POST /tests failed (status={r.status_code}): {r.text}")
        raise SystemExit(1)
    _log(f"Ordered test_id={test_id} -> {r.json()['name']}")


async def _answer_question(client: httpx.AsyncClient, session_id: str, question_id: int, choice_id: int) -> None:
    r = await client.post(
        f"/sessions/{session_id}/answers", json={"question_id": question_id, "choice_id": choice_id}
    )
    if r.status_code != 201:
        _log(f"ERROR: POST /answers failed (status={r.status_code}): {r.text}")
        raise SystemExit(1)
    _log(f"Answered question_id={question_id} with choice_id={choice_id}")


async def _evaluate_and_wait(client: httpx.AsyncClient, base_url: str, session_id: str) -> tuple[dict, float]:
    """Returns (evaluation_frame, elapsed_seconds) -- elapsed is measured from
    the POST /evaluate call to the WS frame actually arriving, i.e. the real
    round trip a client experiences."""

    async with websockets.connect(_ws_url(base_url, session_id)) as ws:
        t0 = time.monotonic()
        r = await client.post(f"/sessions/{session_id}/evaluate")
        if r.status_code != 200:
            _log(f"ERROR: POST /evaluate failed (status={r.status_code}): {r.text}")
            raise SystemExit(1)
        pending = r.json()
        _log(f"POST /evaluate -> 200, immediate response: {pending}")
        if pending["status"] != "pending":
            _log(f"ERROR: expected status='pending', got {pending!r}")
            raise SystemExit(1)

        deadline = time.monotonic() + DEFAULT_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            try:
                frame = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except (asyncio.TimeoutError, TimeoutError):
                break
            payload = json.loads(frame)
            if payload.get("type") == "evaluation":
                elapsed = time.monotonic() - t0
                return payload, elapsed

    raise SystemExit(f"no evaluation frame arrived for session {session_id} within {DEFAULT_TIMEOUT_SECONDS}s")


def _print_result(label: str, evaluation: dict, elapsed: float) -> None:
    print()
    print(f"===== {label} =====")
    print(f"round-trip latency: {elapsed:.1f}s")
    print(f"status: {evaluation['status']}")
    if evaluation["status"] != "complete":
        print(f"detail: {evaluation.get('detail')}")
        return
    print(f"score: {evaluation['score']}")
    print(f"summary: {evaluation['summary']}")
    for c in evaluation["criteria_breakdown"]:
        print(f"  - [{'PASS' if c['passed'] else 'FAIL'}] {c['name']}: {c['feedback']}")


async def run(scenario_id: int, base_url: str) -> None:
    async with httpx.AsyncClient(base_url=base_url, timeout=90.0) as client:
        health = await client.get("/health")
        if health.status_code != 200:
            _log(f"ERROR: server at {base_url} is not healthy (status={health.status_code})")
            raise SystemExit(1)

        relevant_test_ids = await _fetch_relevant_test_ids(client, scenario_id)
        correct_answers = await _fetch_correct_answers(scenario_id)
        _log(f"relevant_test_ids={relevant_test_ids} (from GET /scenarios/{scenario_id}/relevant-tests)")
        _log(f"correct_answers={correct_answers} (question_id, correct_choice_id) -- from local DB")

        # ---------- session 1: "good" performance -----------------------
        print("\n########## SESSION 1: good performance ##########")
        good_session_id = await _create_session(client, scenario_id)
        for content in GOOD_SESSION_MESSAGES:
            await _send_message_and_wait(client, good_session_id, content)
        for test_id in relevant_test_ids:
            await _order_test(client, good_session_id, test_id)
        for question_id, choice_id in correct_answers:
            await _answer_question(client, good_session_id, question_id, choice_id)

        good_evaluation, good_elapsed = await _evaluate_and_wait(client, base_url, good_session_id)
        _print_result("SESSION 1 (good performance)", good_evaluation, good_elapsed)

        # ---------- session 2: "did nothing" -----------------------------
        print("\n########## SESSION 2: did nothing ##########")
        idle_session_id = await _create_session(client, scenario_id)
        # One minimal message -- an *entirely* empty transcript is a real
        # (if degenerate) case this port must also handle, but a single
        # perfunctory greeting is closer to "a trainee who showed up and did
        # nothing useful" than "a trainee who never opened the case at all".
        await _send_message_and_wait(client, idle_session_id, "Hi.")

        idle_evaluation, idle_elapsed = await _evaluate_and_wait(client, base_url, idle_session_id)
        _print_result("SESSION 2 (did nothing)", idle_evaluation, idle_elapsed)

        # ---------- comparison --------------------------------------------
        print("\n########## COMPARISON ##########")
        if good_evaluation["status"] == "complete" and idle_evaluation["status"] == "complete":
            print(f"good score:   {good_evaluation['score']}")
            print(f"idle score:   {idle_evaluation['score']}")
            print(f"difference:   {good_evaluation['score'] - idle_evaluation['score']:+.1f}")
            if good_evaluation["score"] > idle_evaluation["score"]:
                print("PASS: good session scored meaningfully higher than the idle session.")
            else:
                print("FAIL: good session did NOT score higher than the idle session.")
        else:
            print("Cannot compare -- at least one evaluation did not complete.")

        print(f"\nlatency: good={good_elapsed:.1f}s, idle={idle_elapsed:.1f}s")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario-id", type=int, default=2, help="Scenario to evaluate against (default: 2).")
    parser.add_argument("--base-url", default="http://127.0.0.1:8199", help="Base URL of a running server.")
    args = parser.parse_args()

    if hasattr(__import__("sys").stdout, "reconfigure"):
        __import__("sys").stdout.reconfigure(encoding="utf-8")

    asyncio.run(run(args.scenario_id, args.base_url))


if __name__ == "__main__":
    main()
