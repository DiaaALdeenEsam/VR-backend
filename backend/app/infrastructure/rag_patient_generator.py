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

import httpx

from app.config import Settings, get_settings
from app.domain.entities import Evidence, Message, Scenario
from app.domain.exceptions import RagServiceUnavailableError
from app.domain.repositories import PatientReplyGenerator

logger = logging.getLogger(__name__)

_SERVICE_NAME = "RAG patient-chat API"

# Per docs/backend-rag-handoff.md's per-route retry guidance: retry only these
# two error codes (both mean "the dependency/model didn't respond in time",
# transient by nature); everything else -- PATIENT_LANGUAGE_MISMATCH,
# PATIENT_CHAT_EMPTY, invalid/auth responses -- is "a controlled fallback
# condition", not something a retry would fix.
_RETRYABLE_ERROR_CODES = frozenset({"PATIENT_CHAT_TIMEOUT", "PATIENT_CHAT_DEPENDENCY_ERROR"})

MAX_HISTORY_MESSAGES = 8


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
    an accepted role either)."""

    messages: list[dict[str, str]] = []
    for m in history[-MAX_HISTORY_MESSAGES:]:
        role = "assistant" if m.role == "assistant" else "user"
        messages.append({"role": role, "content": m.content})
    messages.append({"role": "user", "content": user_message})
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
        headers = {"X-API-Key": api_key.get_secret_value()}

        max_attempts = max(1, self._settings.rag_api_max_attempts)
        response: httpx.Response | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                async with httpx.AsyncClient(
                    base_url=base_url, timeout=timeout, headers=headers, transport=self._transport
                ) as client:
                    response = await client.post("/v1/rag/patient/chat", json=body)
            except httpx.TimeoutException as exc:
                logger.warning("RAG patient-chat request timed out")
                raise RagServiceUnavailableError(_SERVICE_NAME, "request timed out") from exc
            except httpx.RequestError as exc:
                logger.warning("RAG patient-chat request failed: %s", type(exc).__name__)
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
                    "RAG patient-chat returned %s (attempt %d/%d) -- retrying in %.1fs",
                    code,
                    attempt,
                    max_attempts,
                    backoff_seconds,
                )
                await _backoff_sleep(backoff_seconds)
                continue

            logger.warning("RAG patient-chat returned HTTP %s (code=%s)", response.status_code, code)
            raise RagServiceUnavailableError(_SERVICE_NAME, f"returned HTTP {response.status_code} ({code})")

        assert response is not None

        try:
            payload = response.json()
            return payload["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            logger.warning("RAG patient-chat returned an unexpected response body")
            raise RagServiceUnavailableError(_SERVICE_NAME, "response body was not in the expected shape") from exc
