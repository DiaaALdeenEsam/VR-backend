"""End-to-end, idempotent pipeline: disease-template JSON -> fully testable scenario.

Runs, in order, every step needed to take a scenario from "just a JSON file"
to "provably working through the live API":

  1. Import        -- scripts/import_scenario_from_json.py's import_scenario(),
                       skipped (id reused) if a scenario with this disease_name
                       already exists.
  2. Gold standard  -- sets scenarios.gold_standard if not already set.
  3. Quiz seeding   -- inserts questions/choices from a companion questions JSON,
                       skipping (by exact text match) any question already present
                       for this scenario.
  4. Tests baseline -- runs the legacy seed_data seed (test_categories/tests) if
                       both are still empty.
  5. Live E2E       -- starts the app (if not already running) and exercises the
                       full conversation + quiz + evaluation flow against it,
                       asserting on every response.
  6. Summary        -- a table of what happened at each step.

Every step is safe to re-run: nothing here ever inserts a second scenario, a
second copy of a question (matched by exact text), or duplicate quiz answers/
ordered tests for a *new* session (each run of step 5 creates its own session,
by design -- that part is deliberately NOT idempotent, since "run another
E2E check" is exactly what re-running this script for that purpose means).

Usage (from backend/, matching this repo's other scripts -- PYTHONPATH=backend
so `app` and `scripts` both resolve):

    PYTHONPATH=. python scripts/run_scenario_pipeline.py <scenario.json> \\
        [--gold-standard TEXT | --gold-standard-file PATH] \\
        [--questions PATH] \\
        [--port PORT]

--questions expects: [{"text": str, "choices": [{"text": str, "correct": bool}, ...]}, ...]
(exactly one choice per question must have "correct": true).

Never modifies or deletes any of its input files. Deletes only its own
temporary server log if it had to start the app itself.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from sqlmodel import func, select

from app.config import get_settings
from app.infrastructure.db.engine import create_engine_from_settings, create_session_factory
from app.infrastructure.db.models import (
    ChoiceModel,
    QuestionModel,
    ScenarioModel,
    ScenarioPatientProfileModel,
    TestCategoryModel,
    TestModel,
)
from scripts.import_scenario_from_json import _clean, import_scenario

BACKEND_DIR = Path(__file__).resolve().parent.parent


# --- summary bookkeeping -----------------------------------------------------


@dataclass
class StepResult:
    name: str
    action: str  # imported/skipped/reused/inserted/started/reused-running/pass/fail/...
    result: str  # "pass" / "fail" / "n/a"
    ids: str = ""


@dataclass
class PipelineState:
    steps: list[StepResult] = field(default_factory=list)

    def record(self, name: str, action: str, result: str, ids: str = "") -> None:
        self.steps.append(StepResult(name, action, result, ids))

    def print_summary(self) -> None:
        print("\n" + "=" * 88)
        print("SUMMARY")
        print("=" * 88)
        header = f"{'step':<22} {'action':<24} {'result':<8} {'key ids'}"
        print(header)
        print("-" * len(header))
        for s in self.steps:
            print(f"{s.name:<22} {s.action:<24} {s.result:<8} {s.ids}")
        print("=" * 88)


class E2EStepFailed(Exception):
    """Raised to stop the E2E sequence immediately after printing full diagnostics."""


# --- Step 1: import -----------------------------------------------------------


async def step_import(session, state: PipelineState, data: dict) -> int:
    disease_name = _clean(data.get("disease_name")) or "Untitled scenario"

    result = await session.exec(select(ScenarioModel).where(ScenarioModel.name == disease_name))
    existing = result.one_or_none()

    if existing is not None:
        assert existing.id is not None
        print(f"[1/6] IMPORT: already imported, reusing id={existing.id} (name={existing.name!r})")
        await _print_patient_profile(session, existing.id, source="stored row (existing scenario)")
        state.record("1. import", "reused", "pass", f"scenario_id={existing.id}")
        return existing.id

    scenario, parsed_fields, null_fields = await import_scenario(session, data)
    await session.commit()
    assert scenario.id is not None

    print(f"[1/6] IMPORT: imported new scenario id={scenario.id} (name={scenario.name!r})")
    print("       patient_profile -- parsed to concrete values:")
    for line in parsed_fields or ["  (none)"]:
        print(f"         - {line}")
    print("       patient_profile -- left NULL for manual review:")
    for line in null_fields or ["  (none)"]:
        print(f"         - {line}")

    state.record("1. import", "imported", "pass", f"scenario_id={scenario.id}")
    return scenario.id


async def _print_patient_profile(session, scenario_id: int, source: str) -> None:
    """Prints the current scenario_patient_profile row, whatever produced it."""

    result = await session.exec(
        select(ScenarioPatientProfileModel).where(ScenarioPatientProfileModel.scenario_id == scenario_id)
    )
    profile = result.one_or_none()
    print(f"       patient_profile ({source}):")
    if profile is None:
        print("         (no scenario_patient_profile row)")
        return
    print(f"         age={profile.age!r}  (raw: {profile.age_group_raw!r})")
    print(f"         sex={profile.sex!r}  (raw: {profile.sex_predominance_raw!r})")
    print(f"         geographical_context={profile.geographical_context!r}")


# --- Step 2: gold standard -----------------------------------------------------


async def step_gold_standard(session, state: PipelineState, scenario_id: int, gold_standard: str | None) -> None:
    scenario = await session.get(ScenarioModel, scenario_id)
    assert scenario is not None

    if scenario.gold_standard:
        print(f"[2/6] GOLD STANDARD: already set ({len(scenario.gold_standard)} chars) -- skipping")
        state.record("2. gold_standard", "skipped", "pass", f"{len(scenario.gold_standard)} chars")
        return

    if not gold_standard:
        print(
            "[2/6] GOLD STANDARD: not set, and no --gold-standard/--gold-standard-file given -- "
            "skipping (note: /sessions/{id}/evaluate will 400 for this scenario until it's set)"
        )
        state.record("2. gold_standard", "skipped (no input)", "n/a")
        return

    scenario.gold_standard = gold_standard
    session.add(scenario)
    await session.commit()

    # Verify by re-querying, not by trusting the value we just assigned in memory.
    await session.refresh(scenario)
    assert scenario.gold_standard == gold_standard
    print(f"[2/6] GOLD STANDARD: set and verified by re-query ({len(scenario.gold_standard)} chars)")
    state.record("2. gold_standard", "set", "pass", f"{len(scenario.gold_standard)} chars")


# --- Step 3: quiz seeding -------------------------------------------------------


async def step_quiz(
    session, state: PipelineState, scenario_id: int, questions_data: list[dict] | None
) -> list[tuple[int, int]]:
    """Returns [(question_id, correct_choice_id), ...] for every question now on
    file for this scenario (both pre-existing and newly inserted this run)."""

    if questions_data is None:
        print("[3/6] QUIZ SEEDING: no --questions file given -- skipping")
        state.record("3. quiz_seeding", "skipped (no input)", "n/a")
        return await _existing_questions(session, scenario_id)

    existing = await session.exec(select(QuestionModel).where(QuestionModel.scenario_id == scenario_id))
    existing_by_text = {q.text: q for q in existing.all()}

    inserted, skipped = [], []
    for q in questions_data:
        text = q["text"]
        if text in existing_by_text:
            skipped.append((existing_by_text[text].id, text))
            continue

        # Two-phase insert (app/infrastructure/seed.py:95-127): placeholder
        # correct_choice_id -> insert choices -> patch with the real id, since
        # correct_choice_id is a soft reference, not a DB foreign key (see
        # QuestionModel in app/infrastructure/db/models.py).
        question_row = QuestionModel(scenario_id=scenario_id, text=text, correct_choice_id=-1)
        session.add(question_row)
        await session.flush()
        assert question_row.id is not None

        correct_id: int | None = None
        for choice in q["choices"]:
            choice_row = ChoiceModel(question_id=question_row.id, text=choice["text"])
            session.add(choice_row)
            await session.flush()
            assert choice_row.id is not None
            if choice.get("correct"):
                correct_id = choice_row.id

        if correct_id is None:
            raise ValueError(f"question {text!r} has no choice with \"correct\": true")

        question_row.correct_choice_id = correct_id
        session.add(question_row)
        inserted.append((question_row.id, text))

    await session.commit()

    print(f"[3/6] QUIZ SEEDING: {len(inserted)} inserted, {len(skipped)} skipped as duplicates")
    for qid, text in inserted:
        print(f"       INSERT  id={qid}  {text[:70]}{'...' if len(text) > 70 else ''}")
    for qid, text in skipped:
        print(f"       SKIP    id={qid}  {text[:70]}{'...' if len(text) > 70 else ''}  (already present)")

    action = "inserted" if inserted and not skipped else ("skipped" if skipped and not inserted else "mixed")
    state.record(
        "3. quiz_seeding",
        f"{action} ({len(inserted)} new, {len(skipped)} dup)",
        "pass",
        f"question_ids={[qid for qid, _ in inserted + skipped]}",
    )
    return await _existing_questions(session, scenario_id)


async def _existing_questions(session, scenario_id: int) -> list[tuple[int, int]]:
    result = await session.exec(
        select(QuestionModel).where(QuestionModel.scenario_id == scenario_id).order_by(QuestionModel.id)
    )
    return [(q.id, q.correct_choice_id) for q in result.all()]


# --- Step 4: tests/test_categories baseline ------------------------------------


async def step_tests_baseline(session, state: PipelineState) -> None:
    tests_count = (await session.exec(select(func.count()).select_from(TestModel))).one()
    categories_count = (await session.exec(select(func.count()).select_from(TestCategoryModel))).one()

    if tests_count == 0 and categories_count == 0:
        print("[4/6] TESTS BASELINE: test_categories and tests both empty -- running seed_data seed")
        from app.infrastructure.seed import run as run_legacy_seed

        await run_legacy_seed(BACKEND_DIR / "seed_data")

        tests_count = (await session.exec(select(func.count()).select_from(TestModel))).one()
        categories_count = (await session.exec(select(func.count()).select_from(TestCategoryModel))).one()
        print(f"       after seeding: test_categories={categories_count}, tests={tests_count}")
        state.record(
            "4. tests_baseline", "seeded", "pass", f"test_categories={categories_count}, tests={tests_count}"
        )
    else:
        print(
            f"[4/6] TESTS BASELINE: already populated "
            f"(test_categories={categories_count}, tests={tests_count}) -- skipping"
        )
        state.record(
            "4. tests_baseline", "skipped", "pass", f"test_categories={categories_count}, tests={tests_count}"
        )


# --- Step 5: live E2E validation ------------------------------------------------


def _assert(condition: bool, message: str, response: httpx.Response | None = None) -> None:
    if condition:
        return
    print("\n!!! E2E ASSERTION FAILED !!!")
    print(f"    {message}")
    if response is not None:
        print(f"    request:  {response.request.method} {response.request.url}")
        if response.request.content:
            print(f"    body:     {response.request.content!r}")
        print(f"    status:   {response.status_code}")
        print(f"    response: {response.text}")
    raise E2EStepFailed(message)


async def _wait_for_message_settled(
    client: httpx.AsyncClient, session_id: str, message_id: int, timeout: float = 90.0
) -> dict:
    """Polls GET /sessions/{id} until the given message's status leaves
    "pending"/"generating". The sole PatientReplyGenerator now is the RAG
    API (6-48s observed per call, served through PostMessageAsyncUseCase) --
    POST /sessions/{id}/messages acks with a placeholder immediately, so
    anything that wants the real reply text (this script, for its printed
    output) has to wait for it the same way a real client would (here, by
    polling; a real client could instead listen on WS /ws/voice)."""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = await client.get(f"/sessions/{session_id}")
        messages = r.json()["messages"]
        message = next(m for m in messages if m["id"] == message_id)
        if message["status"] in ("complete", "failed"):
            return message
        await asyncio.sleep(0.5)
    raise E2EStepFailed(f"message {message_id} in session {session_id} never settled within {timeout}s")


async def _wait_for_health(client: httpx.AsyncClient, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = await client.get("/health", timeout=2.0)
            if r.status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        await asyncio.sleep(0.5)
    return False


async def step_e2e(
    state: PipelineState, scenario_id: int, questions: list[tuple[int, int]], base_url: str, port: int
) -> None:
    server_process: subprocess.Popen | None = None
    log_path: Path | None = None

    async with httpx.AsyncClient(base_url=base_url, timeout=120.0) as probe:
        already_running = await _wait_for_health(probe, timeout=2.0)

    if already_running:
        print(f"[5/6] LIVE E2E: server already running at {base_url} -- reusing it")
        server_started_by_us = False
    else:
        print(f"[5/6] LIVE E2E: starting app on {base_url} ...")
        log_file = tempfile.NamedTemporaryFile(
            mode="w", suffix=".log", prefix="run_scenario_pipeline_", delete=False
        )
        log_path = Path(log_file.name)
        server_process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=str(BACKEND_DIR),
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
        log_file.close()
        server_started_by_us = True

        async with httpx.AsyncClient(base_url=base_url, timeout=120.0) as probe:
            healthy = await _wait_for_health(probe, timeout=45.0)
        if not healthy:
            log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
            print("       server did not become healthy in time. Log:")
            print(log_text[-3000:])
            _cleanup_server(server_process, log_path)
            state.record("5. live_e2e", "failed to start", "fail")
            raise E2EStepFailed("server never became healthy")
        print("       server is healthy")

    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=120.0) as client:
            # a. create session
            r = await client.post("/sessions", json={"scenario_id": scenario_id})
            _assert(r.status_code == 201, f"POST /sessions expected 201, got {r.status_code}", r)
            session_id = r.json()["session_id"]
            print(f"   a. POST /sessions -> 201, session_id={session_id}")

            # b. two opening messages -- waited out one at a time: the sole
            # PatientReplyGenerator (the RAG API) is async now, and a session
            # only accepts one in-flight message at a time (409 otherwise;
            # see PostMessageAsyncUseCase's concurrency policy), so the
            # second POST would fail with 409 if fired before the first
            # settles.
            for opener in (
                "Hello, can you tell me what's bothering you today?",
                "Have you noticed any fever, chills, or pain? Can you describe it?",
            ):
                r = await client.post(f"/sessions/{session_id}/messages", json={"content": opener})
                _assert(r.status_code == 201, f"POST /messages expected 201, got {r.status_code}", r)
                pending_body = r.json()
                print(f"   b. POST /messages ({opener[:35]}...) -> 201, status={pending_body['status']}")

                settled = await _wait_for_message_settled(client, session_id, pending_body["id"])
                print(f"      patient reply ({settled['status']}): {settled['content']!r}")

            # c. test categories
            r = await client.get("/test-categories")
            _assert(r.status_code == 200, f"GET /test-categories expected 200, got {r.status_code}", r)
            categories = r.json()
            if not categories:
                print("   c. GET /test-categories -> 200, but empty -- skipping d/e (test-order steps)")
                category_id = test_id = None
            else:
                category_id = categories[0]["id"]
                print(f"   c. GET /test-categories -> 200, using category_id={category_id}")

                # d. tests in that category
                r = await client.get(f"/test-categories/{category_id}/tests")
                _assert(r.status_code == 200, f"GET .../tests expected 200, got {r.status_code}", r)
                tests = r.json()
                _assert(bool(tests), f"category {category_id} has no tests", r)
                test_id = tests[0]["id"]
                print(f"   d. GET /test-categories/{category_id}/tests -> 200, using test_id={test_id}")

            # e. order a test
            if test_id is not None:
                r = await client.post(f"/sessions/{session_id}/tests", json={"test_id": test_id})
                _assert(r.status_code == 201, f"POST /tests expected 201, got {r.status_code}", r)
                print(f"   e. POST /sessions/{{id}}/tests (test_id={test_id}) -> 201")
                ordered_tests_expected = 1
            else:
                ordered_tests_expected = 0

            # f. list questions, assert no correctness leak
            r = await client.get(f"/scenarios/{scenario_id}/questions")
            _assert(r.status_code == 200, f"GET /questions expected 200, got {r.status_code}", r)
            body_text = r.text
            _assert("correct_choice_id" not in body_text, "GET /questions leaks correct_choice_id", r)
            _assert('"is_correct"' not in body_text, "GET /questions leaks is_correct", r)
            print(f"   f. GET /scenarios/{{id}}/questions -> 200, no correctness leak, {len(r.json())} question(s)")

            # g. answer the first question, using OUR seeded correct_choice_id
            # (not anything parsed back out of the API response, which never
            # exposes it -- see the assertion just above).
            answers_expected = 0
            if questions:
                question_id, correct_choice_id = questions[0]
                r = await client.post(
                    f"/sessions/{session_id}/answers",
                    json={"question_id": question_id, "choice_id": correct_choice_id},
                )
                _assert(r.status_code == 201, f"POST /answers expected 201, got {r.status_code}", r)
                _assert("is_correct" not in r.json(), "POST /answers response leaks is_correct", r)
                print(
                    f"   g. POST /sessions/{{id}}/answers "
                    f"(question_id={question_id}, choice_id={correct_choice_id}) -> 201, no is_correct leak"
                )
                answers_expected = 1
            else:
                print("   g. no questions available for this scenario -- skipping answer step")

            # h. evaluate
            r = await client.post(f"/sessions/{session_id}/evaluate")
            _assert(r.status_code == 200, f"POST /evaluate expected 200 (gold_standard set?), got {r.status_code}", r)
            body = r.json()
            print(f"   h. POST /sessions/{{id}}/evaluate -> 200")
            print(f"      score: {body['score']}")
            print(f"      summary: {body['summary']}")
            for c in body["criteria_breakdown"]:
                print(f"      - [{'PASS' if c['passed'] else 'FAIL'}] {c['name']}: {c['feedback']}")

            # i. session review, assert array lengths
            r = await client.get(f"/sessions/{session_id}")
            _assert(r.status_code == 200, f"GET /sessions/{{id}} expected 200, got {r.status_code}", r)
            review = r.json()
            _assert(len(review["messages"]) == 4, f"expected 4 messages, got {len(review['messages'])}", r)
            _assert(
                len(review["ordered_tests"]) == ordered_tests_expected,
                f"expected {ordered_tests_expected} ordered_tests, got {len(review['ordered_tests'])}",
                r,
            )
            _assert(
                len(review["answers"]) == answers_expected,
                f"expected {answers_expected} answers, got {len(review['answers'])}",
                r,
            )
            print(
                f"   i. GET /sessions/{{id}} -> 200, "
                f"messages={len(review['messages'])}, ordered_tests={len(review['ordered_tests'])}, "
                f"answers={len(review['answers'])} -- all as expected"
            )

        state.record("5. live_e2e", "ran fresh", "pass", f"session_id={session_id}")

    except E2EStepFailed:
        state.record("5. live_e2e", "ran fresh", "fail")
        raise
    finally:
        if server_started_by_us:
            _cleanup_server(server_process, log_path)
            print("       server (started by this script) stopped, log removed")


def _cleanup_server(process: subprocess.Popen | None, log_path: Path | None) -> None:
    if process is not None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)

    if log_path is None or not log_path.exists():
        return

    # On Windows, the OS can take a moment to release the child process's
    # handle on this file even after process.wait() returns -- deleting it
    # immediately can raise PermissionError (WinError 32). Retry briefly;
    # if it still won't budge, warn instead of crashing the whole pipeline
    # over a leftover temp log.
    for attempt in range(10):
        try:
            log_path.unlink()
            return
        except PermissionError:
            if attempt == 9:
                print(f"       (warning: could not delete temp log {log_path}, leaving it in place)")
                return
            time.sleep(0.3)


# --- CLI / orchestration --------------------------------------------------------


def _load_questions_arg(path: str | None) -> list[dict] | None:
    if path is None:
        return None
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("--questions file must contain a JSON array")
    return data


def _load_gold_standard_arg(text: str | None, file_path: str | None) -> str | None:
    if text and file_path:
        raise ValueError("pass at most one of --gold-standard / --gold-standard-file")
    if file_path:
        return Path(file_path).read_text(encoding="utf-8").strip()
    return text


async def run(args: argparse.Namespace) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    data = json.loads(Path(args.json_path).read_text(encoding="utf-8"))
    gold_standard = _load_gold_standard_arg(args.gold_standard, args.gold_standard_file)
    questions_data = _load_questions_arg(args.questions)

    settings = get_settings()
    engine = create_engine_from_settings(settings)
    session_factory = create_session_factory(engine)

    state = PipelineState()
    exit_code = 0

    try:
        async with session_factory() as session:
            scenario_id = await step_import(session, state, data)
            await step_gold_standard(session, state, scenario_id, gold_standard)
            questions = await step_quiz(session, state, scenario_id, questions_data)
            await step_tests_baseline(session, state)

        base_url = f"http://127.0.0.1:{args.port}"
        try:
            await step_e2e(state, scenario_id, questions, base_url, args.port)
        except E2EStepFailed:
            exit_code = 1
    finally:
        await engine.dispose()
        state.print_summary()

    return exit_code


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("json_path", help="Path to a disease-template scenario JSON.")
    gold_group = parser.add_mutually_exclusive_group()
    gold_group.add_argument("--gold-standard", help="gold_standard text, inline.")
    gold_group.add_argument("--gold-standard-file", help="Path to a .txt file containing gold_standard text.")
    parser.add_argument(
        "--questions",
        help='Path to a JSON file: [{"text": str, "choices": [{"text": str, "correct": bool}, ...]}, ...]',
    )
    parser.add_argument("--port", type=int, default=8199, help="Port for the live E2E check (default: 8199).")
    args = parser.parse_args()

    exit_code = asyncio.run(run(args))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
