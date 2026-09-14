"""PatientReplyGenerator adapter backed by the external RAG API's dedicated
patient route (POST /v1/rag/patient/chat) -- see docs/backend-rag-handoff.md.

The sole PatientReplyGenerator implementation (app/api/deps.py's
get_patient_reply_generator() constructs this unconditionally, no config
toggle). A local Qwen2.5-0.5B-Instruct model used to be a selectable
alternative here; removed after live reproduction confirmed it hallucinates
and breaks character systematically, while this API's anti-leak boundary was
separately verified to hold cleanly under a harder probe.

The existing PatientReplyGenerator port shape needed no change to support
this adapter: generate_reply(scenario, history, user_message, evidence)
already carries everything it needs --

  - case_query: derived from `scenario.name` (not threaded through the port).
    Live-tested against the real API: an English case_query and its Arabic
    translation returned byte-identical `grounding.sources` -- the embedding
    model is cross-lingual, so no translation step is needed regardless of
    which language `scenario.name` happens to be stored in.
  - persona: a fixed adapter-level setting (Settings.rag_patient_persona),
    not per-call -- personas are a RAG-API-specific concept with no
    equivalent in the domain layer.
  - evidence: deliberately ignored here. The RAG-patient route does its own
    internal retrieval scoped to patient-safe content types (per the handoff
    doc); this adapter's own case_query IS that retrieval trigger, so
    threading our separately-retrieved `evidence` list into it as well would
    be redundant, not additive (and the handoff doc is explicit that the
    patient route "never returns retrieved chunk text" -- it isn't designed
    to accept externally-supplied evidence either).

This adapter is deliberately synchronous-shaped (one `await
generate_reply(...)` call that blocks until done or raises). The "don't block
the STT->TTS loop for 6-48s" problem is solved by *how this port is called*
(see app/application/use_cases/post_message_async.py), not by this adapter
itself returning early.
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

from app.config import Settings, get_settings
from app.domain.entities import Evidence, Message, Scenario
from app.domain.exceptions import RagServiceUnavailableError, RagValidationError
from app.domain.repositories import PatientReplyGenerator
from app.infrastructure.rag_api_common import RAG_API_USER_AGENT, is_validation_status

logger = logging.getLogger(__name__)

_SERVICE_NAME = "RAG patient-chat API"

# Per docs/backend-rag-handoff.md's per-route retry guidance: retry only these
# two error codes (both mean "the dependency/model didn't respond in time",
# transient by nature); everything else -- PATIENT_LANGUAGE_MISMATCH,
# PATIENT_CHAT_EMPTY, invalid/auth responses -- is "a controlled fallback
# condition", not something a retry would fix.
_RETRYABLE_ERROR_CODES = frozenset({"PATIENT_CHAT_TIMEOUT", "PATIENT_CHAT_DEPENDENCY_ERROR"})

# The same two codes the doc calls "controlled fallback conditions" above --
# i.e. the request was understood and definitively rejected, not a transient
# failure -- used to raise RagValidationError instead of the more general
# RagServiceUnavailableError. Distinct from _RETRYABLE_ERROR_CODES: those two
# sets are disjoint by construction (a code is either "retry this" or "don't
# bother, it won't change").
_VALIDATION_ERROR_CODES = frozenset({"PATIENT_LANGUAGE_MISMATCH", "PATIENT_CHAT_EMPTY"})

MAX_HISTORY_MESSAGES = 8

# Per-message and total-combined-length safety caps, mirroring
# evaluation_prompt.py's MAX_PROMPT_CHARS/GOLD_STANDARD_MAX_CHARS pattern and
# its own documented finding: live testing against the real API found
# /v1/rag/chat's retrieval step fails internally (undocumented 503) once a
# single message's text passes roughly 2,000 characters -- see that module's
# docstring for the full empirical writeup (2026-09-07, content-independent,
# reproducible to within ~10 chars). /v1/rag/patient/chat shares the same
# underlying retrieval path ("retrieves against the latest user turn" per
# docs/backend-rag-handoff.md), so the same risk applies here even though it
# hasn't been separately reproduced against this specific route.
#
# MESSAGE_MAX_CHARS uses the exact same ~300-char safety margin
# evaluation_prompt.py chose (2000 - 300 = 1700) for the same reason: margin
# against an empirically-found cliff, not a documented limit.
MESSAGE_MAX_CHARS = 1700

# Separate from the per-message cap above -- guards the *combined* size of
# the whole "messages" array against the handoff doc's own documented
# 24,000-char total-length contract ("Limits are 24 messages, 4,000
# characters per message, and 24,000 characters total"). Unlike
# MESSAGE_MAX_CHARS, this number is a conservative fraction of that
# documented ceiling, not independently empirically measured against
# /v1/rag/patient/chat specifically -- revisit if real payloads ever
# approach it.
TOTAL_MESSAGES_MAX_CHARS = 12_000


async def _backoff_sleep(seconds: float) -> None:
    """Isolated seam around asyncio.sleep -- same pattern as
    rag_client_adapter.py's _backoff_sleep, so tests can monkeypatch just this."""

    await asyncio.sleep(seconds)


def _build_messages(history: list[Message], user_message: str) -> list[dict[str, str]]:
    """Trims history to the last MAX_HISTORY_MESSAGES turns and appends the
    new user turn -- no system prompt here: the RAG-patient route owns its
    own persona/system prompt internally; the handoff doc's contract is "send
    prior user/assistant turns, final message is the new user turn", no
    system role (the doc explicitly says system messages are rejected by
    /v1/rag/chat; the patient route's contract table doesn't list "system" as
    an accepted role either).

    Also enforces two client-side length guards before this ever reaches an
    HTTP call, per MESSAGE_MAX_CHARS/TOTAL_MESSAGES_MAX_CHARS's docstrings
    above:

    - The new user_message -- the turn retrieval actually keys on, per the
      handoff doc ("/v1/rag/chat retrieves against the latest user turn") --
      is REJECTED outright (raises RagValidationError) if it's too long,
      rather than silently truncated. Silently cutting the doctor's actual
      current question could hand the RAG API a garbled version of what's
      actually being asked, which is a worse failure mode than refusing
      clearly and falling back to the fixed reply
      (PostMessageAsyncUseCase already has that fallback path for exactly
      this kind of pre-flight rejection).
    - Older history turns are TRUNCATED instead, never rejected -- losing
      some context from an earlier turn is a much smaller correctness risk
      than truncating the live question, matching the
      truncate-old-not-new philosophy evaluation_prompt.py's own
      _format_transcript() already uses for the same reason.
    - If the combined total still exceeds TOTAL_MESSAGES_MAX_CHARS after
      per-message truncation, the OLDEST history turns are dropped entirely
      (never the new user_message, which is always the last element added
      and is provably never reached by this loop as long as more than one
      message remains) until it fits.
    """

    if len(user_message) > MESSAGE_MAX_CHARS:
        raise RagValidationError(
            _SERVICE_NAME,
            f"new message is {len(user_message)} chars, exceeding the {MESSAGE_MAX_CHARS}-char "
            "per-message limit (see MESSAGE_MAX_CHARS's docstring) -- not sent, to avoid silently "
            "truncating the doctor's actual question",
        )

    messages: list[dict[str, str]] = []
    for m in history[-MAX_HISTORY_MESSAGES:]:
        role = "assistant" if m.role == "assistant" else "user"
        content = m.content
        if len(content) > MESSAGE_MAX_CHARS:
            logger.warning(
                "[rag_patient] history_message_truncated | original_chars=%d | max_chars=%d",
                len(content),
                MESSAGE_MAX_CHARS,
            )
            content = content[:MESSAGE_MAX_CHARS].rstrip() + " …[truncated]"
        messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_message})

    total_chars = sum(len(m["content"]) for m in messages)
    while total_chars > TOTAL_MESSAGES_MAX_CHARS and len(messages) > 1:
        dropped = messages.pop(0)
        total_chars -= len(dropped["content"])
        logger.warning(
            "[rag_patient] history_message_dropped_for_total_budget | dropped_chars=%d | "
            "remaining_total_chars=%d | max_total_chars=%d",
            len(dropped["content"]),
            total_chars,
            TOTAL_MESSAGES_MAX_CHARS,
        )

    return messages


def _error_code(body: object) -> str | None:
    if isinstance(body, dict):
        detail = body.get("detail")
        if isinstance(detail, dict):
            code = detail.get("code")
            return code if isinstance(code, str) else None
    return None


class RagPatientReplyGenerator(PatientReplyGenerator):
    """PatientReplyGenerator backed by an HTTP call to POST /v1/rag/patient/chat."""

    def __init__(self, settings: Settings | None = None, transport: httpx.BaseTransport | None = None) -> None:
        self._settings = settings or get_settings()
        # Only ever set in tests, via httpx.MockTransport -- same pattern as
        # RagClientAdapter.
        self._transport = transport

    async def generate_reply(
        self,
        scenario: Scenario,
        history: list[Message],
        user_message: str,
        evidence: list[Evidence] | None = None,
    ) -> str:
        del evidence  # deliberately unused -- see module docstring

        base_url = self._settings.rag_api_base_url
        api_key = self._settings.rag_api_key
        if not base_url or not api_key:
            raise RagServiceUnavailableError(
                _SERVICE_NAME,
                "RAG_API_BASE_URL and/or RAG_API_KEY is not configured (see .env.example).",
            )

        body = {
            "messages": _build_messages(history, user_message),
            "persona": self._settings.rag_patient_persona,
            "case_query": scenario.name,
            "top_k": 5,
            "response_language": "ar",
        }

        timeout = httpx.Timeout(
            connect=self._settings.rag_api_timeout_connect_seconds,
            read=self._settings.rag_patient_read_timeout_seconds,
            write=self._settings.rag_patient_read_timeout_seconds,
            pool=self._settings.rag_api_timeout_connect_seconds,
        )
        headers = {"X-API-Key": api_key.get_secret_value(), "User-Agent": RAG_API_USER_AGENT}

        max_attempts = max(1, self._settings.rag_api_max_attempts)
        response: httpx.Response | None = None

        logger.info("[rag_patient] request_started | persona=%s", self._settings.rag_patient_persona)
        start_time = time.monotonic()

        for attempt in range(1, max_attempts + 1):
            try:
                async with httpx.AsyncClient(
                    base_url=base_url, timeout=timeout, headers=headers, transport=self._transport
                ) as client:
                    response = await client.post("/v1/rag/patient/chat", json=body)
            except httpx.TimeoutException as exc:
                logger.warning(
                    "[rag_patient] request_timeout | reason=connection | attempt=%d/%d", attempt, max_attempts
                )
                raise RagServiceUnavailableError(_SERVICE_NAME, "request timed out") from exc
            except httpx.RequestError as exc:
                logger.warning(
                    "[rag_patient] request_error | reason=connection | attempt=%d/%d | error_type=%s",
                    attempt,
                    max_attempts,
                    type(exc).__name__,
                )
                raise RagServiceUnavailableError(_SERVICE_NAME, "connection failed") from exc

            if response.status_code == 200:
                break

            code = None
            try:
                code = _error_code(response.json())
            except ValueError:
                pass

            if response.status_code == 503 and code in _RETRYABLE_ERROR_CODES and attempt < max_attempts:
                backoff_seconds = self._settings.rag_api_retry_backoff_seconds * (2 ** (attempt - 1))
                logger.warning(
                    "[rag_patient] retrying | code=%s | attempt=%d/%d | backoff_s=%.1f",
                    code,
                    attempt,
                    max_attempts,
                    backoff_seconds,
                )
                await _backoff_sleep(backoff_seconds)
                continue

            # Distinguish "the API is telling us plainly this request is
            # invalid" (401/422, or a documented controlled-fallback code)
            # from a genuine outage/connection problem -- see
            # RagValidationError's docstring (app/domain/exceptions.py).
            if is_validation_status(response.status_code) or code in _VALIDATION_ERROR_CODES:
                logger.warning(
                    "[rag_patient] request_failed | reason=validation | http_status=%s | code=%s",
                    response.status_code,
                    code,
                )
                raise RagValidationError(_SERVICE_NAME, f"returned HTTP {response.status_code} ({code})")

            logger.warning(
                "[rag_patient] request_failed | reason=connection | http_status=%s | code=%s",
                response.status_code,
                code,
            )
            raise RagServiceUnavailableError(_SERVICE_NAME, f"returned HTTP {response.status_code} ({code})")

        assert response is not None

        try:
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            logger.warning("[rag_patient] unexpected_response_body | reason=unexpected_response")
            raise RagServiceUnavailableError(_SERVICE_NAME, "response body was not in the expected shape") from exc

        logger.info(
            "[rag_patient] request_succeeded | duration_s=%.1f | reply_chars=%d",
            time.monotonic() - start_time,
            len(content),
        )
        return content
