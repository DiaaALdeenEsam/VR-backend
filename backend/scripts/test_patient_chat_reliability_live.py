"""Manual reliability check for the external RAG API's patient-chat route
(POST /v1/rag/patient/chat): multi-turn consistency, repeat-call variance,
and a control case, for a given `case_query` phrasing. Edit CASE_QUERY/
CONTROL_CASE_QUERY below to point this at a different case -- CASE_QUERY
defaults to 'Asthma', the bare-word phrasing found (by manual investigation)
to reliably avoid hallucinated off-topic replies for that scenario.

Three parts, all read-only against the external service (no DB writes, no
scenario/session created in our own app.db):

  A. Multi-turn consistency: 3 independent stateless conversations, each a
     fixed 5-turn Arabic trainee question script, case_query=CASE_QUERY.
     Follows the stateless contract exactly: every call resends the full
     prior history (user+assistant turns) plus the new user turn as the
     final message.
  B. Repeat-call variance: the same single opening turn sent 5 times as 5
     independent (non-conversational) calls, case_query=CASE_QUERY.
  C. Control: the same 5-turn script as Part A but case_query=CONTROL_CASE_QUERY
     (defaults to hepatitis -- the worked example from
     docs/backend-rag-handoff.md), to see whether CASE_QUERY specifically is
     more failure-prone than a documented-working case.

Same config/auth path as app.config.get_settings() (RAG_API_BASE_URL/
RAG_API_KEY/RAG_PATIENT_PERSONA). Prints every request/response and also
writes the complete raw log to OUTPUT_PATH.

A reply is flagged "contradiction" only on keyword/pattern grounds (see
_CONTRADICTION_MARKERS below, tuned for the default asthma case) -- final
clinical judgment of the printed quotes is left to the reader, not asserted
as ground truth by this script.

Usage:
    python scripts/test_patient_chat_reliability_live.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx

from app.config import get_settings
from app.infrastructure.rag_api_common import RAG_API_USER_AGENT

CASE_QUERY = "Asthma"
CONTROL_CASE_QUERY = "التهاب الكبد"  # hepatitis -- the worked example from docs/backend-rag-handoff.md

TRAINEE_SCRIPT = [
    "شو اللي جابك اليوم؟",
    "من أمتى عندك هالأعراض؟",
    "في شي بيخليها أسوأ؟",
    "بتستخدم بخاخ؟",
    "عندك تاريخ عائلي للربو؟",
]

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "artifact" / "patient_chat_reliability_live_output.json"

# Crude, transparent red-flag markers -- not a clinical NLU classifier, just
# a way to point a human reader at the exact suspicious phrase. Anything
# flagged here is quoted verbatim in the report; nothing is auto-suppressed.
_CONTRADICTION_MARKERS = [
    "يطلع للظهر",  # radiates to the back
    "الظهر والكتفين",  # back and shoulders
    "ممزق",  # tearing (aortic-dissection-like)
    "طعنة",  # stabbing (as in a knife)
    "بالقلب",  # "in my heart" -- cardiac framing rather than respiratory
]


def _dump(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def _flag(reply_text: str) -> str | None:
    for marker in _CONTRADICTION_MARKERS:
        if marker in reply_text:
            return marker
    return None


async def _patient_chat_turn(
    client: httpx.AsyncClient,
    persona: str,
    case_query: str,
    history: list[dict[str, str]],
    new_user_message: str,
) -> dict:
    messages = history + [{"role": "user", "content": new_user_message}]
    body = {
        "messages": messages,
        "persona": persona,
        "case_query": case_query,
        "top_k": 5,
        "response_language": "ar",
    }
    response = await client.post("/v1/rag/patient/chat", json=body)
    result: dict = {"request": body, "status_code": response.status_code}
    try:
        result["body"] = response.json()
    except ValueError:
        result["body"] = response.text
    return result


async def _run_conversation(
    client: httpx.AsyncClient, persona: str, case_query: str, conversation_id: str
) -> list[dict]:
    """Runs the fixed 5-turn TRAINEE_SCRIPT as one stateless conversation
    (resending accumulated history each turn). Returns one record per turn."""

    history: list[dict[str, str]] = []
    turns: list[dict] = []

    for turn_num, user_message in enumerate(TRAINEE_SCRIPT, start=1):
        result = await _patient_chat_turn(client, persona, case_query, history, user_message)
        body = result.get("body")
        reply_text = ""
        sources: list[dict] = []
        if isinstance(body, dict):
            choices = body.get("choices") or []
            if choices:
                reply_text = choices[0].get("message", {}).get("content", "")
            grounding = body.get("grounding") or {}
            sources = grounding.get("sources") or []

        flag = _flag(reply_text)

        record = {
            "conversation_id": conversation_id,
            "turn": turn_num,
            "case_query": case_query,
            "user_message": user_message,
            "request": result["request"],
            "status_code": result["status_code"],
            "response_body": body,
            "reply_text": reply_text,
            "sources": sources,
            "contradiction_flag": flag,
        }
        turns.append(record)

        print(f"\n--- conversation={conversation_id} turn={turn_num} case_query={case_query!r} ---")
        print(f"user: {user_message}")
        print(f"sources: {_dump(sources)}")
        print(f"reply: {reply_text}")
        if flag:
            print(f"*** CONTRADICTION FLAG: matched marker {flag!r} in reply text above ***")

        # Stateless contract: history accumulates the real exchange, not the
        # flagged status -- next turn resends everything said so far.
        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": reply_text})

    return turns


async def _run_repeat_calls(client: httpx.AsyncClient, persona: str, case_query: str, n: int) -> list[dict]:
    """Part B: the same single opening turn sent n times as independent
    (non-conversational, empty-history) stateless calls."""

    records = []
    for i in range(1, n + 1):
        result = await _patient_chat_turn(client, persona, case_query, [], TRAINEE_SCRIPT[0])
        body = result.get("body")
        reply_text = ""
        sources: list[dict] = []
        if isinstance(body, dict):
            choices = body.get("choices") or []
            if choices:
                reply_text = choices[0].get("message", {}).get("content", "")
            grounding = body.get("grounding") or {}
            sources = grounding.get("sources") or []

        flag = _flag(reply_text)
        record = {
            "call_id": f"repeat-{i}",
            "case_query": case_query,
            "user_message": TRAINEE_SCRIPT[0],
            "request": result["request"],
            "status_code": result["status_code"],
            "response_body": body,
            "reply_text": reply_text,
            "sources": sources,
            "contradiction_flag": flag,
        }
        records.append(record)

        print(f"\n--- repeat call {i}/{n} case_query={case_query!r} ---")
        print(f"sources: {_dump(sources)}")
        print(f"reply: {reply_text}")
        if flag:
            print(f"*** CONTRADICTION FLAG: matched marker {flag!r} in reply text above ***")

    return records


async def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    base_url = settings.rag_api_base_url
    api_key = settings.rag_api_key
    persona = settings.rag_patient_persona

    if not base_url or not api_key:
        print("RAG_API_BASE_URL and/or RAG_API_KEY not configured -- aborting.")
        return

    headers = {"X-API-Key": api_key.get_secret_value(), "User-Agent": RAG_API_USER_AGENT}
    timeout = httpx.Timeout(connect=5.0, read=60.0, write=60.0, pool=5.0)

    full_log: dict = {"part_a_conversations": [], "part_b_repeat_calls": [], "part_c_control_conversations": []}

    async with httpx.AsyncClient(base_url=base_url, timeout=timeout, headers=headers) as client:
        print("=" * 100)
        print(f"PART A: multi-turn consistency, case_query={CASE_QUERY!r}, 3 independent conversations")
        print("=" * 100)
        for i in range(1, 4):
            conv_id = f"case-query-conv-{i}"
            turns = await _run_conversation(client, persona, CASE_QUERY, conv_id)
            full_log["part_a_conversations"].append({"conversation_id": conv_id, "turns": turns})

        print("\n" + "=" * 100)
        print(f"PART B: repeat-call variance, same opening turn x5, case_query={CASE_QUERY!r}")
        print("=" * 100)
        repeat_records = await _run_repeat_calls(client, persona, CASE_QUERY, 5)
        full_log["part_b_repeat_calls"] = repeat_records

        print("\n" + "=" * 100)
        print(f"PART C: control, case_query={CONTROL_CASE_QUERY!r}, same 5-turn script")
        print("=" * 100)
        control_turns = await _run_conversation(client, persona, CONTROL_CASE_QUERY, "control-conv-1")
        full_log["part_c_control_conversations"].append(
            {"conversation_id": "control-conv-1", "turns": control_turns}
        )

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(full_log, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n\nFull raw log written to {OUTPUT_PATH}")

    # --- summary table -------------------------------------------------
    print("\n" + "=" * 100)
    print("SUMMARY TABLE")
    print("=" * 100)
    header = f"{'id':<24} {'turn':<5} {'best_distance':<14} {'flag':<10} {'note'}"
    print(header)
    print("-" * len(header))

    def _best_distance(sources: list[dict]) -> float | None:
        if not sources:
            return None
        return min(s.get("distance", float("inf")) for s in sources)

    for conv in full_log["part_a_conversations"]:
        for t in conv["turns"]:
            bd = _best_distance(t["sources"])
            flag = "NO" if t["contradiction_flag"] else "yes"
            note = f"marker={t['contradiction_flag']!r}" if t["contradiction_flag"] else ""
            print(f"{conv['conversation_id']:<24} {t['turn']:<5} {bd if bd is not None else '-':<14} {flag:<10} {note}")

    for rec in full_log["part_b_repeat_calls"]:
        bd = _best_distance(rec["sources"])
        flag = "NO" if rec["contradiction_flag"] else "yes"
        note = f"marker={rec['contradiction_flag']!r}" if rec["contradiction_flag"] else ""
        print(f"{rec['call_id']:<24} {'-':<5} {bd if bd is not None else '-':<14} {flag:<10} {note}")

    for conv in full_log["part_c_control_conversations"]:
        for t in conv["turns"]:
            bd = _best_distance(t["sources"])
            flag = "NO" if t["contradiction_flag"] else "yes"
            note = f"marker={t['contradiction_flag']!r}" if t["contradiction_flag"] else ""
            print(f"{conv['conversation_id']:<24} {t['turn']:<5} {bd if bd is not None else '-':<14} {flag:<10} {note}")


if __name__ == "__main__":
    asyncio.run(main())
