"""Tests for RagLlmEvaluator (app/infrastructure/rag_llm_evaluator.py).

All HTTP is faked via httpx.MockTransport, injected through the (test-only)
`transport` constructor parameter -- no real network calls, fast and
deterministic. Same pattern as tests/test_rag_client_adapter.py, the sibling
adapter on the same external service.
"""

from __future__ import annotations

import logging

import httpx
import pytest

import app.infrastructure.rag_llm_evaluator as evaluator_mod
from app.config import Settings
from app.domain.exceptions import RagServiceUnavailableError
from app.infrastructure.rag_llm_evaluator import (
    CONVERSATION_WEIGHT,
    QUIZ_WEIGHT,
    SECTION_PASS_THRESHOLD_PCT,
    TEST_ORDERING_WEIGHT,
    RagLlmEvaluator,
)

FAKE_API_KEY = "sk-test-super-secret-do-not-log-me"


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = dict(
        rag_api_base_url="https://rag.test",
        rag_api_key=FAKE_API_KEY,
        rag_api_timeout_connect_seconds=5.0,
        rag_evaluation_read_timeout_seconds=30.0,
        rag_api_max_attempts=3,
        rag_api_retry_backoff_seconds=1.0,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _evaluation_inputs() -> dict:
    return dict(
        case_text="A patient presents with abdominal pain.",
        gold_standard="Acute appendicitis.",
        messages=[
            {"role": "user", "content": "Where does it hurt?"},
            {"role": "assistant", "content": "Lower right side."},
        ],
        ordered_tests=[{"test_id": 1}, {"test_id": 2}],
        answers=[
            {"question_id": 1, "choice_id": 1, "is_correct": True},
            {"question_id": 2, "choice_id": 2, "is_correct": False},
        ],
        relevant_test_ids=[1, 2],
        total_questions=2,
    )


def _chat_response(content: str) -> dict:
    return {
        "object": "chat.completion",
        "model": "gemma4-biomedical-e4b",
        "response_language": "en",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
    }


def _valid_llm_json() -> str:
    return (
        '{"conversation": {"score_pct": 80, "feedback": "Good history taking."}, '
        '"test_ordering": {"score_pct": 100, "feedback": "Both relevant tests ordered."}, '
        '"quiz": {"score_pct": 50, "feedback": "One of two correct."}, '
        '"overall_summary": "Solid overall performance with room to improve on the quiz."}'
    )


@pytest.fixture(autouse=True)
def _ensure_module_logger_enabled() -> None:
    """Works around a suite-wide quirk, not anything specific to this
    adapter: alembic/env.py calls logging.config.fileConfig(...) with the
    default disable_existing_loggers=True on every migration run (see
    tests/conftest.py's `engine` fixture). The first time any DB-touching
    test in the suite runs, every logger that already existed by then --
    including this module's, created the moment pytest collects this file --
    gets `.disabled = True` for the rest of the process. caplog.at_level()
    does not undo that (it only sets the logger's level and the global
    logging.disable() ceiling, never this per-logger flag), so a positive
    "X was logged" assertion silently sees an empty caplog unless something
    clears it first. tests/test_rag_client_adapter.py's own logging tests
    never hit this because they only assert things were NOT logged (true
    vacuously too) -- this module's test_malformed_json_... test below does
    assert something WAS logged, so it needs this.
    """

    logger = logging.getLogger("app.infrastructure.rag_llm_evaluator")
    original = logger.disabled
    logger.disabled = False
    yield
    logger.disabled = original


@pytest.fixture(autouse=True)
def no_backoff_wait(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        calls.append(seconds)

    monkeypatch.setattr(evaluator_mod, "_backoff_sleep", fake_sleep)
    return calls


def _evaluator(handler, **settings_overrides: object) -> RagLlmEvaluator:
    transport = httpx.MockTransport(handler)
    return RagLlmEvaluator(settings=_settings(**settings_overrides), transport=transport)


# ---------- success path: weighted score computed in code -------------------


async def test_successful_evaluation_computes_weighted_score() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_response(_valid_llm_json()))

    evaluator = _evaluator(handler)
    result = await evaluator.evaluate(**_evaluation_inputs())

    assert result.status == "complete"
    expected = round(80 * CONVERSATION_WEIGHT + 100 * TEST_ORDERING_WEIGHT + 50 * QUIZ_WEIGHT, 1)
    assert result.score == expected
    assert result.summary == "Solid overall performance with room to improve on the quiz."

    assert len(result.criteria_breakdown) == 3
    by_name = {c.name: c for c in result.criteria_breakdown}
    assert by_name["Conversation quality"].passed is True
    assert by_name["Conversation quality"].feedback == "Good history taking."
    assert by_name["Investigation appropriateness"].passed is True
    assert by_name["Quiz performance"].passed == (50 >= SECTION_PASS_THRESHOLD_PCT)


async def test_request_body_sends_a_single_user_message_and_detected_language() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_chat_response(_valid_llm_json()))

    evaluator = _evaluator(handler)
    await evaluator.evaluate(**_evaluation_inputs())

    assert captured["body"]["messages"] == [{"role": "user", "content": captured["body"]["messages"][0]["content"]}]
    assert captured["body"]["response_language"] == "en"  # doctor turn above is English
    assert "score_pct" in captured["body"]["messages"][0]["content"]


# ---------- malformed JSON: raises, no fallback score ------------------------


async def test_malformed_json_raises_rag_service_unavailable_and_logs_raw_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_response("this is not JSON at all"))

    evaluator = _evaluator(handler)

    with caplog.at_level(logging.ERROR, logger="app.infrastructure.rag_llm_evaluator"):
        with pytest.raises(RagServiceUnavailableError):
            await evaluator.evaluate(**_evaluation_inputs())

    all_log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "this is not JSON at all" in all_log_text


async def test_missing_required_key_raises_rag_service_unavailable() -> None:
    incomplete = '{"conversation": {"score_pct": 80, "feedback": "ok"}}'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_response(incomplete))

    evaluator = _evaluator(handler)

    with pytest.raises(RagServiceUnavailableError):
        await evaluator.evaluate(**_evaluation_inputs())


async def test_unexpected_response_shape_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    evaluator = _evaluator(handler)

    with pytest.raises(RagServiceUnavailableError):
        await evaluator.evaluate(**_evaluation_inputs())


# ---------- retry policy -----------------------------------------------------


async def test_503_rag_chat_timeout_is_retried_then_succeeds(no_backoff_wait: list[float]) -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] < 2:
            return httpx.Response(503, json={"detail": {"code": "RAG_CHAT_TIMEOUT"}})
        return httpx.Response(200, json=_chat_response(_valid_llm_json()))

    evaluator = _evaluator(handler, rag_api_max_attempts=3, rag_api_retry_backoff_seconds=1.0)
    result = await evaluator.evaluate(**_evaluation_inputs())

    assert calls["count"] == 2
    assert result.status == "complete"
    assert no_backoff_wait == [1.0]


async def test_503_exhausts_retries_and_raises() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(503, json={"detail": {"code": "RAG_CHAT_DEPENDENCY_ERROR"}})

    evaluator = _evaluator(handler, rag_api_max_attempts=3, rag_api_retry_backoff_seconds=0.01)

    with pytest.raises(RagServiceUnavailableError):
        await evaluator.evaluate(**_evaluation_inputs())

    assert calls["count"] == 3


@pytest.mark.parametrize("status_code", [401, 422])
async def test_401_and_422_are_never_retried(status_code: int) -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(status_code, text="bad request")

    evaluator = _evaluator(handler, rag_api_max_attempts=3, rag_api_retry_backoff_seconds=0.01)

    with pytest.raises(RagServiceUnavailableError):
        await evaluator.evaluate(**_evaluation_inputs())

    assert calls["count"] == 1


async def test_connection_timeout_is_not_retried() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        raise httpx.ConnectTimeout("connect timed out", request=request)

    evaluator = _evaluator(handler, rag_api_max_attempts=3, rag_api_retry_backoff_seconds=0.01)

    with pytest.raises(RagServiceUnavailableError):
        await evaluator.evaluate(**_evaluation_inputs())

    assert calls["count"] == 1


async def test_unconfigured_base_url_or_key_raises_without_a_request() -> None:
    evaluator = RagLlmEvaluator(settings=_settings(rag_api_base_url=None))

    with pytest.raises(RagServiceUnavailableError):
        await evaluator.evaluate(**_evaluation_inputs())
