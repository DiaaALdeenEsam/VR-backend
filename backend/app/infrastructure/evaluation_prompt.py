"""The exact prompt sent to POST /v1/rag/chat for LLM-judge session
evaluation (app/infrastructure/rag_llm_evaluator.py), plus the strict parser
for the JSON it's expected to return.

Kept as its own module (a template constant + pure builder/parser functions,
no HTTP in here at all) so the prompt text and the JSON contract can be
edited/tuned later without touching rag_llm_evaluator.py's request/retry
logic, and so both halves can be unit-tested without any network mocking
(see tests/test_evaluation_prompt.py).

--- Why this is ONE user message, not a system prompt + several turns -----

docs/backend-rag-handoff.md is explicit that /v1/rag/chat rejects system
messages ("System messages are rejected so the service can retain its
grounding and language controls") and documents a 4,000-char-per-message /
24,000-char-total limit. With no system role available, the instructions,
the JSON-output contract, and all the case/transcript/test/quiz data this
port receives have to travel as ordinary chat turns. Splitting that payload
across several *fake* user/assistant turns (synthesizing assistant replies
that were never said) would plant fabricated dialogue in the one place this
call cares most about being read literally -- the grading instructions --
for no benefit. So instead everything is assembled into exactly one "user"
message.

--- The real, undocumented budget: ~2,000 chars, not 4,000 -----------------

/v1/rag/chat "retrieves against the latest user turn" (same doc) before
answering -- and live testing against the real API (2026-09-07) found that
retrieval step fails internally (HTTP 503, `{"code": "RAG_CHAT_FAILED"}`,
completely undocumented) once that single message's text passes roughly
2,000 characters, well under the documented 4,000-char *message* limit --
content-independent (English filler and Arabic filler both failed at the
same length) and reproducible to within about 10 characters (1,994 chars
succeeded, 2,006 failed, repeatedly). That number lines up with
/v1/rag/query's own documented `query` field limit ("1-2,000 non-whitespace
characters") -- consistent with /v1/rag/chat reusing that same
retrieval/embedding path against the whole latest message rather than a
short query. MAX_PROMPT_CHARS below is set well under that empirical
ceiling, not under the documented 4,000-char one -- if a future API change
raises (or removes) that internal limit, MAX_PROMPT_CHARS can safely grow
back up toward 4,000; it should never be *raised* without re-testing this,
since exceeding it doesn't degrade output, it fails the whole call.

--- Why response_language is derived from the transcript, not fixed -------

RagPatientReplyGenerator (app/infrastructure/rag_patient_generator.py)
confirmed the RAG API's retrieval is cross-lingual (an English query and its
Arabic translation returned identical grounding), so response_language only
controls the *output* language, never retrieval quality. There is no
"language" field anywhere in this project's data (Scenario/Message have
none), so this module infers it from what the doctor actually typed in the
transcript being graded -- the trainee-facing feedback should read in the
same language the trainee used, not in whatever language happens to be
stored for the scenario's case_text/gold_standard.
"""

from __future__ import annotations

import json

# --- Per-message budget -----------------------------------------------------
#
# ~300-char safety margin under the ~2,000-char empirical ceiling documented
# above -- deliberately not a margin against the documented 4,000-char limit,
# which is not the binding constraint here.
MAX_PROMPT_CHARS = 1700

# Per-section truncation caps. Deliberately generous for GOLD_STANDARD (the
# single most decision-relevant field for judging the conversation and
# test-ordering sections) and comparatively tight for CASE_TEXT (background
# color the transcript itself usually re-establishes) and the two
# code-computed summaries (test_ordering/quiz), which are short sentences by
# construction and never need anywhere near their cap -- it exists as a
# safety net, not a normal-case constraint. With MAX_PROMPT_CHARS this tight,
# every section is terse by necessity, not just GOLD_STANDARD's cap relative
# to CASE_TEXT's.
CASE_TEXT_MAX_CHARS = 150
GOLD_STANDARD_MAX_CHARS = 450
TEST_ORDERING_MAX_CHARS = 150
QUIZ_MAX_CHARS = 100

# Never send more than this many trailing messages from the transcript, even
# if the remaining char budget could technically fit more -- matches
# RagPatientReplyGenerator's own MAX_HISTORY_MESSAGES=8 precedent in spirit
# (recent turns are what's most relevant). Kept at 8 here too (not doubled,
# unlike an earlier version of this module) -- MAX_PROMPT_CHARS leaves too
# little room per turn to usefully keep more than that anyway.
TRANSCRIPT_MAX_MESSAGES = 8

# Floor for the transcript's char budget even if the other sections (already
# each truncated to their own caps above) leave less than this. In practice
# the caps above sum to comfortably under MAX_PROMPT_CHARS, so this floor is
# a safety net, not something normal traffic should ever hit.
TRANSCRIPT_MIN_CHARS = 200

_ARABIC_RANGE = range(0x0600, 0x0700)


EVALUATION_PROMPT_TEMPLATE = """OSCE exam grading. Reply with ONLY this JSON, nothing else (no markdown, no commentary): {{"conversation": {{"score_pct": <0-100>, "feedback": "<str>"}}, "test_ordering": {{"score_pct": <0-100>, "feedback": "<str>"}}, "quiz": {{"score_pct": <0-100>, "feedback": "<str>"}}, "overall_summary": "<str>"}}
Grade conversation (history-taking/communication vs case+gold standard), test_ordering and quiz (use the given counts, don't guess) independently and briefly. Respond only in {response_language}.
CASE: {case_block}
GOLD STANDARD: {gold_standard_block}
TRANSCRIPT:
{transcript_block}
TESTS ORDERED: {test_ordering_block}
QUIZ: {quiz_block}"""


def _truncate(text: str, max_chars: int, marker: str = " …[truncated]") -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    keep = max(0, max_chars - len(marker))
    return text[:keep].rstrip() + marker


def _detect_response_language(messages: list[dict[str, str]]) -> str:
    """"ar" if any doctor (role="user") turn contains an Arabic-range
    character, "en" if there are doctor turns but none of them do, else "ar"
    by default (matching Settings.rag_patient_fallback_reply's own Arabic
    default) when there are no doctor turns to read at all -- e.g. a session
    evaluated before the doctor ever sent a message."""

    doctor_turns = [m.get("content", "") for m in messages if m.get("role") == "user"]
    if not doctor_turns:
        return "ar"
    if any(ord(ch) in _ARABIC_RANGE for content in doctor_turns for ch in content):
        return "ar"
    return "en"


def _format_transcript(messages: list[dict[str, str]], max_chars: int) -> str:
    """Formats the trailing TRANSCRIPT_MAX_MESSAGES turns as "Doctor:"/
    "Patient:" lines, dropping the *oldest* of those (not the newest) if the
    result still doesn't fit max_chars -- the most recent turns are the ones
    most likely to matter for judging how the encounter concluded.

    Always keeps at least the single most recent turn (mid-line-truncated by
    the final _truncate() call if even that doesn't fit) rather than ever
    dropping down to zero lines: with MAX_PROMPT_CHARS as tight as it is (see
    module docstring), a pathological combination of a maxed-out case_text
    and gold_standard could otherwise leave a transcript budget too small for
    even one full line, and "(no conversation took place)" would be an
    outright false statement to hand the grader when a conversation did
    happen -- a truncated glimpse of it is strictly more honest than that.
    """

    trimmed = messages[-TRANSCRIPT_MAX_MESSAGES:]
    if not trimmed:
        return "(no conversation took place)"

    omitted_earlier = len(messages) > len(trimmed)
    lines = [f"{'Doctor' if m.get('role') == 'user' else 'Patient'}: {m.get('content', '')}" for m in trimmed]

    while len(lines) > 1 and sum(len(line) + 1 for line in lines) > max_chars:
        lines.pop(0)
        omitted_earlier = True

    text = "\n".join(lines)
    if omitted_earlier:
        text = "[earlier turns omitted]\n" + text
    return _truncate(text, max_chars, marker=" …[truncated]")


def _format_test_ordering(ordered_tests: list[dict], relevant_test_ids: list[int], max_chars: int) -> str:
    """Deliberately reports pre-computed appropriateness counts, not raw test
    names/ids -- the LLM is told which ordered tests were appropriate rather
    than asked to infer it, per this port's design (see
    EvaluationGenerator.evaluate()'s docstring in app/domain/repositories.py)."""

    ordered_ids = [t["test_id"] for t in ordered_tests]
    relevant_set = set(relevant_test_ids)
    relevant_ordered = sum(1 for tid in ordered_ids if tid in relevant_set)
    irrelevant_ordered = len(ordered_ids) - relevant_ordered
    missed = len(relevant_set - set(ordered_ids))

    text = (
        f"{len(ordered_ids)} ordered, {relevant_ordered} appropriate, "
        f"{irrelevant_ordered} inappropriate, {missed} relevant test(s) missed."
    )
    return _truncate(text, max_chars)


def _format_quiz(answers: list[dict], total_questions: int, max_chars: int) -> str:
    correct = sum(1 for a in answers if a.get("is_correct"))
    attempted = len(answers)
    # Unanswered questions count as incorrect, not "not yet graded" -- e.g.
    # "1/3 correct (2 attempted)" already implies 1 wrong-or-unanswered
    # without spelling it out, keeping this within QUIZ_MAX_CHARS's tight
    # budget (see module docstring for why every section here is this terse).
    text = f"{correct}/{total_questions} correct ({attempted} attempted)."
    return _truncate(text, max_chars)


def build_prompt(
    case_text: str,
    gold_standard: str,
    messages: list[dict[str, str]],
    ordered_tests: list[dict],
    answers: list[dict],
    relevant_test_ids: list[int],
    total_questions: int,
) -> tuple[str, str]:
    """Returns (prompt_text, response_language). prompt_text is the single
    "user" message content to send to /v1/rag/chat -- see module docstring
    for why this is one message rather than several turns."""

    response_language = _detect_response_language(messages)
    case_block = _truncate(case_text, CASE_TEXT_MAX_CHARS)
    gold_standard_block = _truncate(gold_standard, GOLD_STANDARD_MAX_CHARS)
    test_ordering_block = _format_test_ordering(ordered_tests, relevant_test_ids, TEST_ORDERING_MAX_CHARS)
    quiz_block = _format_quiz(answers, total_questions, QUIZ_MAX_CHARS)

    # Fill the transcript last, sized to whatever's left of MAX_PROMPT_CHARS
    # after every other (already-capped) section -- rather than giving the
    # transcript its own fixed cap, this way it gets to use the budget the
    # other sections didn't need (the common case: case/gold_standard text is
    # usually much shorter than their caps).
    skeleton = EVALUATION_PROMPT_TEMPLATE.format(
        response_language=response_language,
        case_block=case_block,
        gold_standard_block=gold_standard_block,
        transcript_block="",
        test_ordering_block=test_ordering_block,
        quiz_block=quiz_block,
    )
    transcript_budget = max(MAX_PROMPT_CHARS - len(skeleton), TRANSCRIPT_MIN_CHARS)
    transcript_block = _format_transcript(messages, transcript_budget)

    prompt = EVALUATION_PROMPT_TEMPLATE.format(
        response_language=response_language,
        case_block=case_block,
        gold_standard_block=gold_standard_block,
        transcript_block=transcript_block,
        test_ordering_block=test_ordering_block,
        quiz_block=quiz_block,
    )
    return prompt, response_language


_REQUIRED_SECTION_KEYS = ("conversation", "test_ordering", "quiz")


def _validate_section(section: object, name: str) -> tuple[float, str]:
    if not isinstance(section, dict):
        raise ValueError(f"{name!r} section is not a JSON object")

    score_pct = section.get("score_pct")
    if isinstance(score_pct, bool) or not isinstance(score_pct, (int, float)):
        raise ValueError(f"{name!r}.score_pct is missing or not a number")
    if not (0 <= score_pct <= 100):
        raise ValueError(f"{name!r}.score_pct is out of range 0-100: {score_pct!r}")

    feedback = section.get("feedback")
    if not isinstance(feedback, str) or not feedback.strip():
        raise ValueError(f"{name!r}.feedback is missing or empty")

    return float(score_pct), feedback


def parse_llm_evaluation(raw_content: str) -> dict[str, tuple[float, str] | str]:
    """Strictly parses+validates the LLM's response into
    {"conversation": (score_pct, feedback), "test_ordering": (...), "quiz":
    (...), "overall_summary": str}.

    Raises ValueError (never returns a partial/best-effort result) if the
    content isn't valid JSON, isn't an object, is missing any required key,
    or has a section whose shape doesn't match -- per this port's contract,
    a caller must never salvage a partial parse into a guessed score. The
    only normalization applied is stripping a leading/trailing markdown code
    fence (```/```json), a common formatting quirk models produce despite
    being told not to -- that's normalizing a wrapper, not salvaging broken
    content.
    """

    text = raw_content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"response was not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError("response JSON was not an object")

    missing = [k for k in (*_REQUIRED_SECTION_KEYS, "overall_summary") if k not in payload]
    if missing:
        raise ValueError(f"response JSON is missing required key(s): {missing}")

    overall_summary = payload.get("overall_summary")
    if not isinstance(overall_summary, str) or not overall_summary.strip():
        raise ValueError("'overall_summary' is missing or empty")

    return {
        "conversation": _validate_section(payload["conversation"], "conversation"),
        "test_ordering": _validate_section(payload["test_ordering"], "test_ordering"),
        "quiz": _validate_section(payload["quiz"], "quiz"),
        "overall_summary": overall_summary,
    }
