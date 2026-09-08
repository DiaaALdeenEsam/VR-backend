"""Tests for app/infrastructure/evaluation_prompt.py -- the prompt builder
and the strict JSON-response parser, both pure functions (no HTTP, no mocks
needed)."""

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
        ordered_tests=[{"test_id": 1}],
        answers=[{"question_id": 1, "choice_id": 1, "is_correct": True}],
        relevant_test_ids=[1, 2],
        total_questions=2,
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
    case/gold text eating most of MAX_PROMPT_CHARS) must still drop the
    *oldest* of those turns, not silently omit the newest ones."""

    messages = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i} " * 20} for i in range(8)]
    prompt, _ = build_prompt(**_kwargs(messages=messages, case_text="y" * 5000, gold_standard="z" * 5000))
    assert len(prompt) <= MAX_PROMPT_CHARS
    assert "turn 7" in prompt  # most recent turn survives
    assert "turn 0" not in prompt  # oldest was dropped first


def test_build_prompt_reports_appropriateness_not_raw_test_names() -> None:
    """The LLM must be told which ordered tests were appropriate, not asked
    to infer it from names it's never given (see EvaluationGenerator's
    docstring) -- so no test name/id should even appear in the prompt."""

    prompt, _ = build_prompt(**_kwargs(ordered_tests=[{"test_id": 1}, {"test_id": 99}], relevant_test_ids=[1]))
    assert "2 ordered, 1 appropriate, 1 inappropriate" in prompt


def test_build_prompt_reports_quiz_counts() -> None:
    prompt, _ = build_prompt(
        **_kwargs(
            answers=[
                {"question_id": 1, "choice_id": 1, "is_correct": True},
                {"question_id": 2, "choice_id": 2, "is_correct": False},
            ],
            total_questions=3,
        )
    )
    assert "1/3 correct (2 attempted)" in prompt


def test_build_prompt_handles_no_conversation_at_all() -> None:
    prompt, language = build_prompt(**_kwargs(messages=[], ordered_tests=[], answers=[], total_questions=0))
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
    assert '"test_ordering"' in prompt
    assert '"quiz"' in prompt
    assert '"overall_summary"' in prompt


# ---------- parse_llm_evaluation -------------------------------------------


def _valid_json() -> str:
    return (
        '{"conversation": {"score_pct": 80, "feedback": "Good history taking."}, '
        '"test_ordering": {"score_pct": 100, "feedback": "All relevant tests ordered."}, '
        '"quiz": {"score_pct": 66.5, "feedback": "Two of three correct."}, '
        '"overall_summary": "Solid overall performance."}'
    )


def test_parse_valid_json() -> None:
    parsed = parse_llm_evaluation(_valid_json())
    assert parsed["conversation"] == (80.0, "Good history taking.")
    assert parsed["test_ordering"] == (100.0, "All relevant tests ordered.")
    assert parsed["quiz"] == (66.5, "Two of three correct.")
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
        '{"conversation": {"score_pct": 80, "feedback": "ok"}, "test_ordering": {"score_pct": 80, "feedback": "ok"}}',  # missing quiz/overall_summary
        '{"conversation": "not an object", "test_ordering": {"score_pct": 80, "feedback": "ok"}, "quiz": {"score_pct": 80, "feedback": "ok"}, "overall_summary": "s"}',
        '{"conversation": {"score_pct": "eighty", "feedback": "ok"}, "test_ordering": {"score_pct": 80, "feedback": "ok"}, "quiz": {"score_pct": 80, "feedback": "ok"}, "overall_summary": "s"}',
        '{"conversation": {"score_pct": 150, "feedback": "ok"}, "test_ordering": {"score_pct": 80, "feedback": "ok"}, "quiz": {"score_pct": 80, "feedback": "ok"}, "overall_summary": "s"}',
        '{"conversation": {"score_pct": 80, "feedback": ""}, "test_ordering": {"score_pct": 80, "feedback": "ok"}, "quiz": {"score_pct": 80, "feedback": "ok"}, "overall_summary": "s"}',
        '{"conversation": {"score_pct": 80, "feedback": "ok"}, "test_ordering": {"score_pct": 80, "feedback": "ok"}, "quiz": {"score_pct": 80, "feedback": "ok"}, "overall_summary": ""}',
        '{"conversation": {"score_pct": true, "feedback": "ok"}, "test_ordering": {"score_pct": 80, "feedback": "ok"}, "quiz": {"score_pct": 80, "feedback": "ok"}, "overall_summary": "s"}',
    ],
)
def test_parse_rejects_malformed_or_incomplete_responses(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_llm_evaluation(raw)


def test_parse_does_not_salvage_partial_output() -> None:
    """One section missing entirely must fail the whole parse -- never
    return the two sections that did parse with the third silently
    defaulted."""

    raw = (
        '{"conversation": {"score_pct": 80, "feedback": "ok"}, '
        '"test_ordering": {"score_pct": 80, "feedback": "ok"}, '
        '"overall_summary": "s"}'
    )
    with pytest.raises(ValueError, match="quiz"):
        parse_llm_evaluation(raw)
