"""Manual, read-mostly end-to-end smoke test: runs one complete realistic
session for a given scenario through the REAL running application -- real
FastAPI app, real StartSessionUseCase/PostMessageAsyncUseCase/
EvaluateSessionAsyncUseCase, real RagPatientReplyGenerator and RagLlmEvaluator
hitting the real external RAG API, real WebSocketPushHub -- nothing mocked.

Talks to a live `uvicorn app.main:app` instance over plain HTTP (httpx) for
every REST call, and opens a real WS connection (websockets) to /ws/voice
exactly the way a real client must, to receive the async-pushed reply/
evaluation frames (see app/infrastructure/ws_hub.py -- there is no REST
polling equivalent for the evaluation result specifically, since
SessionEvaluation is never persisted).

Writes real rows to whatever database the running server is pointed at (one
new `sessions` row + its `messages` rows per invocation) -- this is not a
dry run for the session/chat part.

Usage (start the server first, e.g.
`uvicorn app.main:app --host 127.0.0.1 --port 8199`):
    python scripts/test_session_e2e_live.py --base-url http://127.0.0.1:8199
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

import httpx
import websockets

DEFAULT_SCENARIO_ID = 6  # 'Asthma' in this project's seed data at the time this script was written

DEFAULT_TRAINEE_TURNS = [
    "مرحبا، شو اللي جابك اليوم؟",
    "من أمتى بدأت الأعراض؟",
    "في شي ساعد يخفف الأعراض او خلاها اسوأ؟",
    "عندك تاريخ سابق مع الربو؟",
    "شو الأدوية اللي تستخدمها حاليا؟",
]

# Generous margins over the server's own hard timeouts (app/config.py):
# rag_patient_hard_timeout_seconds=65, rag_evaluation_hard_timeout_seconds=100.
REPLY_TIMEOUT_SECONDS = 65.0 + 15.0
EVALUATION_TIMEOUT_SECONDS = 100.0 + 20.0


def _ts() -> str:
    return time.strftime("%H:%M:%S", time.localtime())


def _log(msg: str) -> None:
    print(f"[{_ts()}] {msg}")


def _dump(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


async def _wait_for_frame(
    ws: websockets.WebSocketClientProtocol, timeout: float, wanted_types: set[str]
) -> dict | None:
    """Prints every frame as it arrives (live, not buffered); returns the
    first frame whose "type" is in `wanted_types` and (for "reply") whose
    status has left pending/generating. None on timeout."""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        try:
            frame = await asyncio.wait_for(ws.recv(), timeout=remaining)
        except asyncio.TimeoutError:
            return None
        if isinstance(frame, bytes):
            _log(f"WS binary frame received ({len(frame)} bytes) -- ignored (no TTS involved in this probe)")
            continue
        payload = json.loads(frame)
        _log(f"WS frame: {_dump(payload)}")
        ftype = payload.get("type")
        if ftype == "error":
            return payload
        if ftype == "reply" and payload.get("status") in ("complete", "failed"):
            return payload
        if ftype == "evaluation":
            return payload
        # "thinking" frames, or a "reply" still pending/generating -- keep listening.
    return None


async def run(base_url: str, scenario_id: int, trainee_turns: list[str]) -> dict:
    """Returns a dict capturing everything observed, for the final report."""

    result: dict = {}

    async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
        # --- 0. sanity: what does this scenario/the global catalog actually
        # have available, before deciding whether test-ordering/quiz steps
        # are even possible for it.
        r = await client.get("/scenarios")
        _log(f"GET /scenarios -> {r.status_code}: {r.json()}")
        result["scenarios_list"] = r.json()

        r = await client.get("/test-categories")
        _log(f"GET /test-categories -> {r.status_code}: {r.json()}")
        result["test_categories"] = r.json()

        r = await client.get(f"/scenarios/{scenario_id}/relevant-tests")
        _log(f"GET /scenarios/{scenario_id}/relevant-tests -> {r.status_code}: {r.json()}")
        result["relevant_tests"] = r.json()

        r = await client.get(f"/scenarios/{scenario_id}/questions")
        _log(f"GET /scenarios/{scenario_id}/questions -> {r.status_code}: {r.json()}")
        result["questions"] = r.json()

        # --- 1. create session ---------------------------------------------
        r = await client.post("/sessions", json={"scenario_id": scenario_id})
        _log(f"POST /sessions -> {r.status_code}: {r.json()}")
        if r.status_code != 201:
            result["session_creation_error"] = r.text
            return result
        session_id = r.json()["session_id"]
        result["session_id"] = session_id

        r = await client.get(f"/sessions/{session_id}")
        _log(f"GET /sessions/{session_id} (immediately after creation) -> {r.status_code}: {_dump(r.json())}")
        result["session_after_creation"] = r.json()

        # --- 2. multi-turn chat, one at a time, over a persistent WS conn ---
        ws_url = base_url.replace("https://", "wss://").replace("http://", "ws://") + f"/ws/voice?session_id={session_id}"
        _log(f"Opening WS: {ws_url}")
        turns_result = []
        async with websockets.connect(ws_url) as ws:
            _log("WS connected.")
            for i, user_text in enumerate(trainee_turns, start=1):
                _log(f"--- turn {i}/{len(trainee_turns)} ---")
                _log(f"POST /sessions/{session_id}/messages content={user_text!r}")
                r = await client.post(f"/sessions/{session_id}/messages", json={"content": user_text})
                _log(f"  -> {r.status_code}: {_dump(r.json())}")
                placeholder = r.json()

                _log(f"Waiting up to {REPLY_TIMEOUT_SECONDS:.0f}s for the real reply over WS...")
                final_frame = await _wait_for_frame(ws, REPLY_TIMEOUT_SECONDS, {"reply", "error"})

                turns_result.append(
                    {
                        "turn": i,
                        "user_message": user_text,
                        "placeholder_response": placeholder,
                        "final_ws_frame": final_frame,
                    }
                )
                if final_frame is None:
                    _log("  !!! NO REPLY RECEIVED WITHIN TIMEOUT -- stopping the conversation here.")
                    break
                if final_frame.get("type") == "error":
                    _log(f"  !!! SERVER ERROR: {final_frame}")
                    break
                _log(f"  FINAL status={final_frame['status']!r} content={final_frame.get('content')!r}")

            result["turns"] = turns_result

            # --- 3. evaluate, listening on the SAME ws connection -----------
            _log("POST /sessions/{id}/evaluate")
            r = await client.post(f"/sessions/{session_id}/evaluate")
            _log(f"  -> {r.status_code}: {_dump(r.json())}")
            result["evaluate_immediate_response"] = {"status_code": r.status_code, "body": r.json()}

            if r.status_code == 200:
                _log(f"Waiting up to {EVALUATION_TIMEOUT_SECONDS:.0f}s for the real evaluation over WS...")
                eval_frame = await _wait_for_frame(ws, EVALUATION_TIMEOUT_SECONDS, {"evaluation", "error"})
                result["evaluation_ws_frame"] = eval_frame
                if eval_frame is None:
                    _log("  !!! NO EVALUATION RECEIVED WITHIN TIMEOUT.")
                else:
                    _log(f"  EVALUATION: {_dump(eval_frame)}")

        # --- 4. final session review (full transcript from the DB side) ----
        r = await client.get(f"/sessions/{session_id}")
        _log(f"Final GET /sessions/{session_id} -> {r.status_code}")
        result["final_session_review"] = r.json()

    return result


async def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://127.0.0.1:8199")
    parser.add_argument("--scenario-id", type=int, default=DEFAULT_SCENARIO_ID)
    parser.add_argument(
        "--message",
        action="append",
        dest="messages",
        help="A doctor message to send (repeatable, in order). Defaults to a 5-turn asthma history-taking script.",
    )
    parser.add_argument("--out", default="artifact/session_e2e_live_output.json")
    args = parser.parse_args()

    trainee_turns = args.messages or DEFAULT_TRAINEE_TURNS
    result = await run(args.base_url, args.scenario_id, trainee_turns)

    from pathlib import Path

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(f"Full raw result written to {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
