"""EvaluationGenerator adapter backed by the external RAG API's clinician-chat
route (POST /v1/rag/chat) -- see docs/backend-rag-handoff.md.

Replaces StubEvaluationGenerator (app/infrastructure/stub_evaluator.py) as
the "real" evaluator: instead of deterministic rule-based arithmetic over
message counts, this asks the same backing LLM the RAG API already serves
for a structured JSON verdict on three independent axes -- conversation
quality, investigation-ordering appropriateness, and quiz performance (see
app/infrastructure/evaluation_prompt.py for the exact prompt and JSON
contract) -- then computes the final weighted score in *this* code from the
three score_pct values it returns. The LLM is never trusted to do that
arithmetic itself.

No fallback to StubEvaluationGenerator (or any rule-based score) on any
failure here. A bad/unreachable response, an exhausted-retries 503, a
timeout, or a malformed/unparseable JSON body all raise
RagServiceUnavailableError -- the same exception RagPatientReplyGenerator
raises for its own unexpected-response-shape case
(app/infrastructure/rag_patient_generator.py) -- which
EvaluateSessionAsyncUseCase (app/application/use_cases/evaluate_session_async.py)
catches and turns into a WS "evaluation" frame with status="failed" and a
clear message, exactly like PostMessageAsyncUseCase does for a failed
patient reply. There is deliberately no code path here that returns a
guessed/partial score.

Structurally this mirrors RagPatientReplyGenerator closely (same retry loop
shape, same settings reuse, same test-only `transport` seam) -- the two
adapters call different routes on the same external service under the same
client contract (docs/backend-rag-handoff.md), so duplicating that shape
here (rather than inventing a different one) keeps the two easy to compare
line-for-line when that contract changes.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from app.config import Settings, get_settings
from app.domain.entities import EvaluationCriterion, SessionEvaluation
from app.domain.exceptions import RagServiceUnavailableError
from app.domain.repositories import EvaluationGenerator
from app.infrastructure.evaluation_prompt import build_prompt, parse_llm_evaluation

logger = logging.getLogger(__name__)

_SERVICE_NAME = "RAG chat API"

# Per docs/backend-rag-handoff.md's per-route retry guidance for /v1/rag/chat
# specifically. Note this set is NOT the same as RagPatientReplyGenerator's
# _RETRYABLE_ERROR_CODES: RAG_CHAT_EMPTY is retryable here, whereas the
# patient route's PATIENT_CHAT_EMPTY is explicitly documented as a
# controlled-fallback condition instead -- the two routes' client contracts
# genuinely differ on this point, not a copy-paste-and-forgot-to-change.
_RETRYABLE_ERROR_CODES = frozenset({"RAG_CHAT_TIMEOUT", "RAG_CHAT_EMPTY", "RAG_CHAT_DEPENDENCY_ERROR"})

# How much of a malformed/unparseable raw response to log when parsing fails.
# Unlike rag_client_adapter.py's evidence text (never logged, even
# truncated -- that's corpus-sourced clinical content), this is the model's
# own generated evaluation text: a human has to be able to see what it
# actually said to fix the prompt, per parse_llm_evaluation()'s "no salvage"
# contract (app/infrastructure/evaluation_prompt.py) -- this logging is what
# makes that debuggable instead of a silent black box. Capped so one
# pathological response can't flood the log.
_RAW_RESPONSE_LOG_CHARS = 2000

# Below this score_pct (0-100), a section's mapped EvaluationCriterion below
# is passed=False. Arbitrary, matching StubEvaluationGenerator's
# QUIZ_PASS_THRESHOLD (0.5) precedent -- the same simple midpoint for every
# section rather than section-specific cutoffs, named here so it isn't a
# magic number at the call site.
SECTION_PASS_THRESHOLD_PCT = 50.0

# Same three weights StubEvaluationGenerator used (app/infrastructure/stub_evaluator.py)
# -- must sum to 1.0. The LLM never computes this itself (see module
# docstring); it only returns the three raw score_pct values these multiply.
CONVERSATION_WEIGHT = 0.4
TEST_ORDERING_WEIGHT = 0.3
QUIZ_WEIGHT = 0.3


async def _backoff_sleep(seconds: float) -> None:
    """Isolated seam around asyncio.sleep -- same pattern as
    rag_client_adapter.py's and rag_patient_generator.py's own
    _backoff_sleep, so tests can monkeypatch just this."""

    await asyncio.sleep(seconds)


def _error_code(body: object) -> str | None:
    if isinstance(body, dict):
        detail = body.get("detail")
        if isinstance(detail, dict):
            code = detail.get("code")
            return code if isinstance(code, str) else None
    return None


def _criterion(name: str, score_pct: float, feedback: str) -> EvaluationCriterion:
    return EvaluationCriterion(name=name, passed=score_pct >= SECTION_PASS_THRESHOLD_PCT, feedback=feedback)


class RagLlmEvaluator(EvaluationGenerator):
    """EvaluationGenerator backed by an HTTP call to POST /v1/rag/chat."""

    def __init__(self, settings: Settings | None = None, transport: httpx.BaseTransport | None = None) -> None:
        self._settings = settings or get_settings()
        # Only ever set in tests, via httpx.MockTransport -- same pattern as
        # every other RAG-API adapter in this package.
        self._transport = transport

    async def evaluate(
        self,
        case_text: str,
        gold_standard: str,
        messages: list[dict[str, str]],
        ordered_tests: list[dict],
        answers: list[dict],
        relevant_test_ids: list[int],
        total_questions: int,
    ) -> SessionEvaluation:
        base_url = self._settings.rag_api_base_url
        api_key = self._settings.rag_api_key
        if not base_url or not api_key:
            raise RagServiceUnavailableError(
                _SERVICE_NAME,
                "RAG_API_BASE_URL and/or RAG_API_KEY is not configured (see .env.example).",
            )

        prompt, response_language = build_prompt(
            case_text=case_text,
            gold_standard=gold_standard,
            messages=messages,
            ordered_tests=ordered_tests,
            answers=answers,
            relevant_test_ids=relevant_test_ids,
            total_questions=total_questions,
        )

        body = {
            "messages": [{"role": "user", "content": prompt}],
            "top_k": 5,
            "response_language": response_language,
        }

        timeout = httpx.Timeout(
            connect=self._settings.rag_api_timeout_connect_seconds,
            read=self._settings.rag_evaluation_read_timeout_seconds,
            write=self._settings.rag_evaluation_read_timeout_seconds,
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
                    response = await client.post("/v1/rag/chat", json=body)
            except httpx.TimeoutException as exc:
                logger.warning("RAG chat (evaluation) request timed out")
                raise RagServiceUnavailableError(_SERVICE_NAME, "request timed out") from exc
            except httpx.RequestError as exc:
                logger.warning("RAG chat (evaluation) request failed: %s", type(exc).__name__)
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
                    "RAG chat (evaluation) returned %s (attempt %d/%d) -- retrying in %.1fs",
                    code,
                    attempt,
                    max_attempts,
                    backoff_seconds,
                )
                await _backoff_sleep(backoff_seconds)
                continue

            logger.warning("RAG chat (evaluation) returned HTTP %s (code=%s)", response.status_code, code)
            raise RagServiceUnavailableError(_SERVICE_NAME, f"returned HTTP {response.status_code} ({code})")

        assert response is not None

        try:
            payload = response.json()
            raw_content = payload["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            logger.warning("RAG chat (evaluation) returned an unexpected response body")
            raise RagServiceUnavailableError(_SERVICE_NAME, "response body was not in the expected shape") from exc

        try:
            parsed = parse_llm_evaluation(raw_content)
        except ValueError as exc:
            logger.error(
                "RAG chat (evaluation) returned unparseable JSON (%s); raw content: %r",
                exc,
                raw_content[:_RAW_RESPONSE_LOG_CHARS],
            )
            raise RagServiceUnavailableError(_SERVICE_NAME, f"response was not valid evaluation JSON: {exc}") from exc

        conversation_pct, conversation_feedback = parsed["conversation"]
        test_ordering_pct, test_ordering_feedback = parsed["test_ordering"]
        quiz_pct, quiz_feedback = parsed["quiz"]

        score = round(
            conversation_pct * CONVERSATION_WEIGHT
            + test_ordering_pct * TEST_ORDERING_WEIGHT
            + quiz_pct * QUIZ_WEIGHT,
            1,
        )

        criteria = [
            _criterion("Conversation quality", conversation_pct, conversation_feedback),
            _criterion("Investigation appropriateness", test_ordering_pct, test_ordering_feedback),
            _criterion("Quiz performance", quiz_pct, quiz_feedback),
        ]

        return SessionEvaluation(
            status="complete",
            score=score,
            summary=parsed["overall_summary"],
            criteria_breakdown=criteria,
        )
