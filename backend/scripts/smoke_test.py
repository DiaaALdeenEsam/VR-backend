"""Manual smoke test: walks through all 11 REST endpoints against a running server.

Prerequisites:
    alembic upgrade head
    python -m app.infrastructure.seed seed_data
    uvicorn app.main:app          # in another terminal

Usage:
    python scripts/smoke_test.py [base_url]

base_url defaults to http://127.0.0.1:8000.
"""

from __future__ import annotations

import asyncio
import sys

import httpx

STEP_COUNT = 0


def _step(title: str) -> None:
    global STEP_COUNT
    STEP_COUNT += 1
    print(f"\n[{STEP_COUNT:02d}] {title}")


def _show(response: httpx.Response) -> None:
    print(f"    -> {response.status_code} {response.text}")


def _fail(message: str) -> None:
    print(f"\n✗ {message}")
    sys.exit(1)


async def main(base_url: str) -> None:
    async with httpx.AsyncClient(base_url=base_url, timeout=60.0) as client:
        _step("GET /health")
        r = await client.get("/health")
        _show(r)
        if r.status_code != 200:
            _fail("Server isn't healthy -- is uvicorn running and the DB migrated?")

        _step("GET /scenarios")
        r = await client.get("/scenarios")
        _show(r)
        scenarios = r.json()
        if not scenarios:
            _fail("No scenarios found -- run: python -m app.infrastructure.seed seed_data")
        scenario_id = scenarios[0]["id"]

        _step(f"POST /sessions  (scenario_id={scenario_id})")
        r = await client.post("/sessions", json={"scenario_id": scenario_id})
        _show(r)
        if r.status_code != 201:
            _fail("Could not start a session.")
        session_id = r.json()["session_id"]

        _step(f"POST /sessions/{session_id}/messages")
        r = await client.post(f"/sessions/{session_id}/messages", json={"content": "أين يؤلمك بالضبط؟"})
        _show(r)

        _step("GET /test-categories")
        r = await client.get("/test-categories")
        _show(r)
        categories = r.json()
        if not categories:
            print("    (no test categories -- skipping test-order steps)")
            category_id = test_id = None
        else:
            category_id = categories[0]["id"]

            _step(f"GET /test-categories/{category_id}/tests")
            r = await client.get(f"/test-categories/{category_id}/tests")
            _show(r)
            tests = r.json()
            test_id = tests[0]["id"] if tests else None

        if test_id is not None:
            _step(f"POST /sessions/{session_id}/tests  (test_id={test_id})")
            r = await client.post(f"/sessions/{session_id}/tests", json={"test_id": test_id})
            _show(r)

        _step(f"GET /scenarios/{scenario_id}/questions")
        r = await client.get(f"/scenarios/{scenario_id}/questions")
        _show(r)
        questions = r.json()

        if questions:
            question = questions[0]
            choice_id = question["choices"][0]["id"]

            _step(f"POST /sessions/{session_id}/answers")
            r = await client.post(
                f"/sessions/{session_id}/answers",
                json={"question_id": question["id"], "choice_id": choice_id},
            )
            _show(r)
        else:
            print("    (no questions for this scenario -- skipping answer step)")

        _step(f"GET /sessions/{session_id}")
        r = await client.get(f"/sessions/{session_id}")
        _show(r)

        _step(f"POST /sessions/{session_id}/evaluate")
        r = await client.post(f"/sessions/{session_id}/evaluate")
        _show(r)
        if r.status_code == 400:
            print("    (expected if this scenario has no gold_standard set)")

    print(f"\n✓ Done -- {STEP_COUNT} steps against {base_url}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Arabic text prints correctly on Windows consoles

    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    asyncio.run(main(base))
