"""The exact prompt sent to POST /v1/rag/chat for LLM-judge session
evaluation (app/infrastructure/rag_llm_evaluator.py), plus the strict parser
for the JSON it's expected to return.

Kept as its own module (a template constant + pure builder/parser functions,
no HTTP in here at all) so the prompt text and the JSON contract can be
edited/tuned later without touching rag_llm_evaluator.py's request/retry
logic, and so both halves can be unit-tested without any network mocking
(see tests/test_evaluation_prompt.py).

--- Scope: conversation-only grading (chat-only assessment model) ----------

The application has moved to a chat-only assessment model: test-ordering and
quiz (questions/choices/answers/ordered_tests) are permanently deprecated as
scoring inputs. This module now grades ONLY the conversation
(history-taking/communication vs. case+gold standard) and produces a single
"conversation" section plus an "overall_summary" -- there is no
"test_ordering"/"quiz" section in the prompt, the expected JSON response
shape, or the parsed result. The underlying tests/questions/choices/answers/
ordered_tests tables, models, and endpoints are untouched by this -- this is
a scoring-logic change only (see gather_evaluation_inputs() in
app/application/use_cases/evaluate_session.py, which correspondingly no
longer gathers that data for this purpose).

--- Why this is ONE user message, not a system prompt + several turns -----

docs/backend-rag-handoff.md is explicit that /v1/rag/chat rejects system
messages ("System messages are rejected so the service can retain its
grounding and language controls") and documents a 4,000-char-per-message /
24,000-char-total limit. With no system role available, the instructions,
the JSON-output contract, and all the case/transcript data this port
receives have to travel as ordinary chat turns. Splitting that payload
across several *fake* user/assistant turns (synthesizing assistant replies
that were never said) would plant fabricated dialogue in the one place this
call cares most about being read literally -- the grading instructions --
for no benefit. So instead everything is assembled into exactly one "user"
message.

--- The real, undocumented budget: ~2,000 chars, not 4,000, not 24,000 -----

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
short query. The documented 24,000-char-total / 4,000-char-per-message
limits are for a *multi-turn* conversation array and do not apply here --
this whole prompt is one message, so the ~2,000-char single-message ceiling
above is the actual binding constraint, not those larger documented numbers.

MAX_PROMPT_CHARS below is set well under that empirical ceiling, not under
the documented 4,000-char one -- if a future API change raises (or removes)
that internal limit, MAX_PROMPT_CHARS can safely grow back up toward 4,000;
it should never be *raised* without re-testing this live against the real
API first, since exceeding it doesn't degrade output, it fails the whole
call. This module deliberately does NOT raise it as part of accommodating
longer case/gold_standard text (2026-09-14 rebalance, see below) -- that
would risk turning every evaluation call into a guaranteed 503 with no
verification it wouldn't. What changed instead is how the existing ~1,700
budget is split across sections.

--- The 2026-09-14 rebalance: fixing "transcript starved by case/gold" -----

Before this rebalance, case_text/gold_standard each had a small, FIXED cap
(150/450 chars -- sized for two short two-hundred/four-hundred-char seed
scenarios) and the transcript got only whatever was left over after those
fixed caps were spent -- for a scenario with a long, fully-transcribed
case_text/gold_standard (e.g. a full clinical-vignette document, 2,000+
chars each), that fixed spend consistently ate nearly the whole budget,
leaving the transcript down to 1-2 turns even for a short 5-question
history-taking conversation. Live-tested (2026-09-14) against a real 5-turn
asthma session: case_text kept 150/2,133 chars (7%), gold_standard kept
450/1,876 chars (24%), and 8 of the 10 real transcript messages were dropped
entirely.

The fix flips the fill order: the transcript is now sized FIRST, against its
own real formatted length (up to TRANSCRIPT_MAX_MESSAGES turns), reserved out
of the budget remaining after the fixed instructional skeleton -- protected
by CASE_AND_GOLD_MIN_CHARS so a very long chat history still can't consume
the *entire* remaining budget and leave the grader with zero case context.
case_text/gold_standard then split whatever's left after the transcript's
actual (not worst-case) length is known, weighted toward gold_standard (the
single most decision-relevant field for judging the conversation, same
rationale as before), each still bounded by its own MAX constant as an upper
ceiling.

Important, honest limit of this fix: it does NOT make "0% truncation of a
2,000+-char case_text/gold_standard" achievable in general -- that combined
length alone already exceeds the ~1,700-char total budget for the entire
message, transcript included. What it does achieve: a case/gold_standard
short enough to fit today's two seed scenarios (a few hundred chars each)
continues to reach the grader completely untruncated, exactly as before
(unaffected by this change), AND a realistic ~10-turn conversation is no
longer starved down to 1-2 messages just because the case/gold_standard text
happens to be long -- the transcript, not the case/gold_standard text, is now
what's protected first. For a case this long, case_text/gold_standard will
still be heavily truncated; there is no way to avoid that within one
~2,000-char message short of re-architecting this into multiple prompt turns
(out of scope here) or shortening the stored case_text/gold_standard text
itself (a content decision, not a prompt-budget one).
"""

from __future__ import annotations

import json

# --- Per-message budget -----------------------------------------------------
#
# ~300-char safety margin under the ~2,000-char empirical ceiling documented
# above -- deliberately not a margin against the documented 4,000-char limit,
# which is not the binding constraint here. NOT raised as part of the
# 2026-09-14 rebalance -- see the module docstring's explanation of why that
# would be reckless without live re-verification.
MAX_PROMPT_CHARS = 1700

# Upper ceilings for case_text/gold_standard -- reachable only when the
# transcript (reserved first, see build_prompt()) doesn't need the whole
# remaining budget itself. Raised from the pre-2026-09-14 values of 150/450
# (sized for two short seed scenarios) to give real headroom for longer
# cases once transcript competition is light -- gold_standard's ceiling
# stays the larger of the two, matching its existing priority rationale.
CASE_TEXT_MAX_CHARS = 900
GOLD_STANDARD_MAX_CHARS = 1100

# Never send more than this many trailing transcript turns even if the char
# budget could fit more. Doubled from 8 (pre-2026-09-14) to 16 so a realistic
# ~10-turn history-taking conversation is never pre-truncated by message
# count alone -- only (if ever) by the char budget below.
TRANSCRIPT_MAX_MESSAGES = 16

# Guaranteed minimum reserved for case_text+gold_standard COMBINED, even
# against a very long chat history -- protects the grader from ever being
# handed a transcript with zero case context. Symmetric to
# TRANSCRIPT_MIN_CHARS below (the transcript's own floor for the reverse
# situation: a very long case_text/gold_standard).
CASE_AND_GOLD_MIN_CHARS = 300

# Floor for the transcript's reserved budget -- matters only if the fixed
# instructional skeleton plus CASE_AND_GOLD_MIN_CHARS somehow leaves less
# than this; in practice the skeleton is small and stable, so this is a
# defensive floor, not something normal traffic should hit.
TRANSCRIPT_MIN_CHARS = 400

# gold_standard's share of whatever's left for case_text+gold_standard after
# the transcript's actual (not worst-case) length is known -- see
# build_prompt(). Weighted above 50% for the same reason GOLD_STANDARD's own
# ceiling is larger than CASE_TEXT's.
GOLD_STANDARD_SHARE = 0.58

_ARABIC_RANGE = range(0x0600, 0x0700)


EVALUATION_PROMPT_TEMPLATE = """OSCE exam grading. Reply with ONLY this JSON, nothing else (no markdown, no commentary): {{"conversation": {{"score_pct": <0-100>, "feedback": "<str>"}}, "overall_summary": "<str>"}}
Grade the conversation (history-taking/communication vs case+gold standard) briefly. Respond only in {response_language}.
CASE: {case_block}
GOLD STANDARD: {gold_standard_block}
TRANSCRIPT:
{transcript_block}"""


def _truncate(text: str, max_chars: int, marker: str = " …[truncated]") -> str:
    text = text.strip()
    if max_chars <= 0:
        return ""
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
    dropping down to zero lines: a pathologically long case/gold_standard
    combined with a long chat history could otherwise leave a transcript
    budget too small for even one full line, and "(no conversation took
    place)" would be an outright false statement to hand the grader when a
    conversation did happen -- a truncated glimpse of it is strictly more
    honest than that.
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


def build_prompt(
    case_text: str,
    gold_standard: str,
    messages: list[dict[str, str]],
) -> tuple[str, str]:
    """Returns (prompt_text, response_language). prompt_text is the single
    "user" message content to send to /v1/rag/chat -- see module docstring
    for why this is one message rather than several turns, and for the
    2026-09-14 rebalance that changed the fill order below.
    """

    response_language = _detect_response_language(messages)

    # 1. Measure the fixed instructional skeleton (everything except the
    #    three content blocks) to find out how much budget is actually left
    #    to divide between transcript and case/gold_standard.
    empty_skeleton = EVALUATION_PROMPT_TEMPLATE.format(
        response_language=response_language, case_block="", gold_standard_block="", transcript_block=""
    )
    remaining = MAX_PROMPT_CHARS - len(empty_skeleton)

    # 2. Size the transcript FIRST, against its own real need (not a
    #    leftover computed after case/gold_standard have already spent their
    #    full fixed cap) -- this is the actual 2026-09-14 fix: a long
    #    case_text/gold_standard can no longer starve the transcript down to
    #    1-2 turns. CASE_AND_GOLD_MIN_CHARS caps how much of `remaining` the
    #    transcript is allowed to claim, so the reverse (a very long chat
    #    history leaving zero case context) can't happen either.
    transcript_ceiling = max(remaining - CASE_AND_GOLD_MIN_CHARS, TRANSCRIPT_MIN_CHARS)
    transcript_block = _format_transcript(messages, transcript_ceiling)

    # 3. case_text/gold_standard split whatever's actually left, using the
    #    transcript block's real (not worst-case) length -- a short
    #    transcript leaves more room for case/gold_standard, not wasted
    #    headroom. Each still bounded by its own MAX constant as an upper
    #    ceiling (so a very short transcript doesn't hand case_text/
    #    gold_standard more than they can ever need to fill).
    case_and_gold_budget = max(remaining - len(transcript_block), 0)
    gold_standard_chars = min(GOLD_STANDARD_MAX_CHARS, round(case_and_gold_budget * GOLD_STANDARD_SHARE))
    case_text_chars = min(CASE_TEXT_MAX_CHARS, max(case_and_gold_budget - gold_standard_chars, 0))

    case_block = _truncate(case_text, case_text_chars)
    gold_standard_block = _truncate(gold_standard, gold_standard_chars)

    prompt = EVALUATION_PROMPT_TEMPLATE.format(
        response_language=response_language,
        case_block=case_block,
        gold_standard_block=gold_standard_block,
        transcript_block=transcript_block,
    )
    return prompt, response_language


_REQUIRED_SECTION_KEYS = ("conversation",)


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
    {"conversation": (score_pct, feedback), "overall_summary": str}.

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
        "overall_summary": overall_summary,
    }
