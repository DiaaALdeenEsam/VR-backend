"""Tests for app/infrastructure/evaluation_prompt.py -- the prompt builder
and the strict JSON-response parser, both pure functions (no HTTP, no mocks
needed).

Chat-only assessment model (2026-09-14): build_prompt()/parse_llm_evaluation()
no longer take or produce test_ordering/quiz sections -- see the module's own
docstring for the full rationale and the transcript-vs-case/gold_standard
budget rebalance that came with it.
"""

from __future__ import annotations

import pytest

from app.infrastructure.evaluation_prompt import (
    MAX_PROMPT_CHARS,
    build_prompt,
    parse_llm_evaluation,
)


def _kwargs(**overrides: object) -> dict:
    defaults: dict[str, object] = dict(
        case_text="A patient presents with abdominal pain.",
        gold_standard="Acute appendicitis.",
        messages=[
            {"role": "user", "content": "Where does it hurt?"},
            {"role": "assistant", "content": "Lower right side."},
        ],
    )
    defaults.update(overrides)
    return defaults


# ---------- build_prompt --------------------------------------------------


def test_build_prompt_stays_under_the_per_message_budget() -> None:
    prompt, _ = build_prompt(**_kwargs())
    assert len(prompt) <= MAX_PROMPT_CHARS


def test_build_prompt_stays_under_budget_with_a_huge_transcript() -> None:
    huge_messages = [{"role": "user" if i % 2 == 0 else "assistant", "content": "x" * 500} for i in range(50)]
    prompt, _ = build_prompt(**_kwargs(messages=huge_messages, case_text="y" * 5000, gold_standard="z" * 5000))
    assert len(prompt) <= MAX_PROMPT_CHARS
    # TRANSCRIPT_MAX_MESSAGES trims to the last few turns before the
    # char-budget trimming even runs -- so with 50 turns, some were dropped
    # for that reason alone, and the omission marker must reflect it.
    assert "[earlier turns omitted]" in prompt


def test_build_prompt_drops_oldest_transcript_turns_first_on_a_tight_budget() -> None:
    """Even within TRANSCRIPT_MAX_MESSAGES, a tight remaining budget (huge
    case/gold text) must still drop the *oldest* of those turns, not
    silently omit the newest ones -- the transcript is now sized against its
    own real need first (see the module docstring's 2026-09-14 rebalance),
    but a genuinely long transcript can still exceed even that reservation."""

    messages = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i} " * 20} for i in range(8)]
    prompt, _ = build_prompt(**_kwargs(messages=messages, case_text="y" * 5000, gold_standard="z" * 5000))
    assert len(prompt) <= MAX_PROMPT_CHARS
    assert "turn 7" in prompt  # most recent turn survives
    assert "turn 0" not in prompt  # oldest was dropped first


def test_build_prompt_protects_transcript_from_a_long_case_and_gold_standard() -> None:
    """The 2026-09-14 fix this test locks in: a long case_text/gold_standard
    must no longer starve a short, realistic transcript down to 1-2 turns --
    every turn of a normal-length conversation should survive even when
    case_text/gold_standard are each far longer than their old (150/450)
    caps."""

    messages = [
        {"role": "user", "content": "What brought you in today?"},
        {"role": "assistant", "content": "Shortness of breath for three hours."},
        {"role": "user", "content": "Any triggers?"},
        {"role": "assistant", "content": "Being near a cat."},
        {"role": "user", "content": "Do you use an inhaler?"},
        {"role": "assistant", "content": "Yes, but it isn't helping today."},
    ]
    prompt, _ = build_prompt(**_kwargs(messages=messages, case_text="a" * 2000, gold_standard="b" * 1800))
    assert len(prompt) <= MAX_PROMPT_CHARS
    assert "[earlier turns omitted]" not in prompt
    for turn in messages:
        assert turn["content"] in prompt


def test_build_prompt_does_not_truncate_a_short_case_and_gold_standard() -> None:
    """A case/gold_standard short enough to fit the two seed scenarios must
    still reach the grader completely untruncated -- unaffected by the
    2026-09-14 rebalance."""

    case_text = "A patient presents with abdominal pain."
    gold_standard = "Acute appendicitis."
    prompt, _ = build_prompt(**_kwargs(case_text=case_text, gold_standard=gold_standard))
    assert case_text in prompt
    assert gold_standard in prompt
    assert "…[truncated]" not in prompt


def test_build_prompt_handles_no_conversation_at_all() -> None:
    prompt, language = build_prompt(**_kwargs(messages=[]))
    assert "(no conversation took place)" in prompt
    assert language == "ar"  # default with no doctor turns to read


def test_build_prompt_detects_arabic_from_doctor_turns() -> None:
    _, language = build_prompt(**_kwargs(messages=[{"role": "user", "content": "أين يؤلمك؟"}]))
    assert language == "ar"


def test_build_prompt_detects_english_from_doctor_turns() -> None:
    _, language = build_prompt(**_kwargs(messages=[{"role": "user", "content": "Where does it hurt?"}]))
    assert language == "en"


def test_build_prompt_requests_json_only_output() -> None:
    prompt, _ = build_prompt(**_kwargs())
    assert "ONLY this JSON" in prompt
    assert '"conversation"' in prompt
    assert '"overall_summary"' in prompt
    # Chat-only assessment model: no test-ordering/quiz section left.
    assert '"test_ordering"' not in prompt
    assert '"quiz"' not in prompt
    assert "TESTS ORDERED" not in prompt
    assert "QUIZ:" not in prompt


# ---------- parse_llm_evaluation -------------------------------------------


def _valid_json() -> str:
    return '{"conversation": {"score_pct": 80, "feedback": "Good history taking."}, "overall_summary": "Solid overall performance."}'


def test_parse_valid_json() -> None:
    parsed = parse_llm_evaluation(_valid_json())
    assert parsed["conversation"] == (80.0, "Good history taking.")
    assert parsed["overall_summary"] == "Solid overall performance."


def test_parse_strips_markdown_code_fence() -> None:
    wrapped = f"```json\n{_valid_json()}\n```"
    parsed = parse_llm_evaluation(wrapped)
    assert parsed["overall_summary"] == "Solid overall performance."


def test_parse_strips_bare_code_fence() -> None:
    wrapped = f"```\n{_valid_json()}\n```"
    parsed = parse_llm_evaluation(wrapped)
    assert parsed["conversation"] == (80.0, "Good history taking.")


@pytest.mark.parametrize(
    "raw",
    [
        "not json at all",
        "[]",
        '{"conversation": {"score_pct": 80, "feedback": "ok"}}',  # missing overall_summary
        '{"conversation": "not an object", "overall_summary": "s"}',
        '{"conversation": {"score_pct": "eighty", "feedback": "ok"}, "overall_summary": "s"}',
        '{"conversation": {"score_pct": 150, "feedback": "ok"}, "overall_summary": "s"}',
        '{"conversation": {"score_pct": 80, "feedback": ""}, "overall_summary": "s"}',
        '{"conversation": {"score_pct": 80, "feedback": "ok"}, "overall_summary": ""}',
        '{"conversation": {"score_pct": true, "feedback": "ok"}, "overall_summary": "s"}',
    ],
)
def test_parse_rejects_malformed_or_incomplete_responses(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_llm_evaluation(raw)


def test_parse_does_not_salvage_partial_output() -> None:
    """A missing required key must fail the whole parse -- never return a
    result with the missing field silently defaulted."""

    raw = '{"overall_summary": "s"}'
    with pytest.raises(ValueError, match="conversation"):
        parse_llm_evaluation(raw)
