"""Tests for RagClientAdapter (app/infrastructure/rag_client_adapter.py).

All HTTP is faked via httpx.MockTransport, injected through the (test-only)
`transport` constructor parameter -- no real network calls, fast and
deterministic. The retry backoff itself is monkeypatched out (see
`no_backoff_wait` below) so retry tests don't actually sleep.
"""

from __future__ import annotations

import logging

import httpx
import pytest

import app.infrastructure.rag_client_adapter as rag_mod
from app.config import Settings
from app.domain.exceptions import RagServiceUnavailableError
from app.infrastructure.rag_client_adapter import RagClientAdapter

FAKE_API_KEY = "sk-test-super-secret-do-not-log-me"
FAKE_QUERY = "ما هي فحوصات مريض التهاب الكبد الفيروسي الحاد النادرة جدا"


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = dict(
        rag_api_base_url="https://rag.test",
        rag_api_key=FAKE_API_KEY,
        rag_api_timeout_connect_seconds=5.0,
        rag_api_timeout_read_seconds=60.0,
        rag_api_max_attempts=3,
        rag_api_retry_backoff_seconds=1.0,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _evidence_body() -> list[dict]:
    return [
        {
            "id": "ev-1",
            "rank": 1,
            "text": "يشكو المريض من ألم في الجانب الأيمن العلوي من البطن منذ يومين.",
            "distance": 0.12,
            "metadata": {
                "chapter": "Hepatology",
                "section": "History",
                "content_type": "history",
                "source_file": "hepatology.pdf",
                "title": "Viral Hepatitis",
            },
        }
    ]


@pytest.fixture(autouse=True)
def no_backoff_wait(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Replaces the real backoff sleep with a no-op that records the requested
    durations, so retry tests run instantly instead of actually waiting."""

    calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        calls.append(seconds)

    monkeypatch.setattr(rag_mod, "_backoff_sleep", fake_sleep)
    return calls


def _adapter(handler, **settings_overrides: object) -> RagClientAdapter:
    transport = httpx.MockTransport(handler)
    return RagClientAdapter(settings=_settings(**settings_overrides), transport=transport)


async def test_successful_request_returns_parsed_evidence() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_evidence_body())

    adapter = _adapter(handler)
    evidence = await adapter.retrieve(query=FAKE_QUERY)

    assert len(evidence) == 1
    assert evidence[0].id == "ev-1"
    assert evidence[0].content_type == "history"


async def test_empty_result_set_returns_empty_list() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    adapter = _adapter(handler)
    evidence = await adapter.retrieve(query=FAKE_QUERY)

    assert evidence == []


async def test_503_is_retried_with_backoff_then_succeeds(no_backoff_wait: list[float]) -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] < 3:
            return httpx.Response(503, text="service unavailable")
        return httpx.Response(200, json=_evidence_body())

    adapter = _adapter(handler, rag_api_max_attempts=3, rag_api_retry_backoff_seconds=1.0)
    evidence = await adapter.retrieve(query=FAKE_QUERY)

    assert calls["count"] == 3
    assert len(evidence) == 1
    # exponential backoff: 1s then 2s between the 3 attempts.
    assert no_backoff_wait == [1.0, 2.0]


async def test_503_exhausts_retries_and_raises() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(503, text="service unavailable")

    adapter = _adapter(handler, rag_api_max_attempts=3, rag_api_retry_backoff_seconds=0.01)

    with pytest.raises(RagServiceUnavailableError):
        await adapter.retrieve(query=FAKE_QUERY)

    assert calls["count"] == 3


@pytest.mark.parametrize("status_code", [401, 422])
async def test_401_and_422_are_never_retried(status_code: int) -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(status_code, text="bad request")

    adapter = _adapter(handler, rag_api_max_attempts=3, rag_api_retry_backoff_seconds=0.01)

    with pytest.raises(RagServiceUnavailableError):
        await adapter.retrieve(query=FAKE_QUERY)

    assert calls["count"] == 1


async def test_connection_timeout_is_not_retried() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        raise httpx.ConnectTimeout("connect timed out", request=request)

    adapter = _adapter(handler, rag_api_max_attempts=3, rag_api_retry_backoff_seconds=0.01)

    with pytest.raises(RagServiceUnavailableError):
        await adapter.retrieve(query=FAKE_QUERY)

    assert calls["count"] == 1


async def test_unconfigured_base_url_or_key_raises_without_a_request() -> None:
    adapter = RagClientAdapter(settings=_settings(rag_api_base_url=None))

    with pytest.raises(RagServiceUnavailableError):
        await adapter.retrieve(query=FAKE_QUERY)


async def test_logging_never_includes_api_key_full_query_or_full_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Covers rule from for-backend-team.md: never log the API key or complete
    retrieved/query text, even on failure paths."""

    long_clinical_text = (
        "نص سريري طويل جدا لا يجب أن يظهر كاملا في السجلات تحت أي ظرف من الظروف على الإطلاق"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        # Fail with a 401 whose body echoes back the clinical text -- proves the
        # adapter's logging doesn't just forward response.text like the colab
        # adapters do.
        return httpx.Response(401, text=long_clinical_text)

    adapter = _adapter(handler, rag_api_max_attempts=1)

    with caplog.at_level(logging.WARNING, logger="app.infrastructure.rag_client_adapter"):
        with pytest.raises(RagServiceUnavailableError):
            await adapter.retrieve(query=FAKE_QUERY)

    all_log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert FAKE_API_KEY not in all_log_text
    assert FAKE_QUERY not in all_log_text
    assert long_clinical_text not in all_log_text


async def test_logging_on_503_retry_does_not_include_api_key_or_query(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="service unavailable")

    adapter = _adapter(handler, rag_api_max_attempts=2, rag_api_retry_backoff_seconds=0.01)

    with caplog.at_level(logging.WARNING, logger="app.infrastructure.rag_client_adapter"):
        with pytest.raises(RagServiceUnavailableError):
            await adapter.retrieve(query=FAKE_QUERY)

    all_log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert FAKE_API_KEY not in all_log_text
    assert FAKE_QUERY not in all_log_text
