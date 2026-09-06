"""EvidenceRetriever adapter that calls the external RAG (retrieval-only) API
over HTTP (POST /v1/rag/query).

Differs from the colab adapters (colab_stt_adapter.py / colab_tts_adapter.py)
in two ways, both intentional:
  1. Logging never includes the response body or retrieved `text` content,
     even truncated -- only the status code and a short generic message.
     The colab adapters' `response.text[:500]` pattern is NOT copied here:
     that's fine for a transcript/audio error, but this API returns clinical
     evidence text that shouldn't end up in application logs.
  2. Retrieval failure is expected to be non-fatal to the caller. This
     adapter still raises RagServiceUnavailableError on any failure (never
     silently returns an empty list itself) -- but see
     PostMessageUseCase.execute(), which is expected to catch that and
     proceed with no evidence rather than let a RAG outage break a chat turn.
     Keeping the "raise on failure" behavior here (rather than swallowing it
     in the adapter) keeps the graceful-degradation decision at the call
     site, where it's visible, instead of hidden inside the adapter.

Retry policy, per the RAG API's documented client contract (see
for-backend-team.md): retry transient `503` responses with backoff, up to
Settings.rag_api_max_attempts total attempts. `401`/`422` (and connection-level
errors/timeouts) are never retried -- a single attempt raises
RagServiceUnavailableError immediately, since those aren't "transient"
failures a retry would plausibly fix.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from app.config import Settings, get_settings
from app.domain.entities import Evidence
from app.domain.exceptions import RagServiceUnavailableError
from app.domain.repositories import EvidenceRetriever

logger = logging.getLogger(__name__)

_SERVICE_NAME = "RAG API"


async def _backoff_sleep(seconds: float) -> None:
    """Isolated seam around asyncio.sleep so tests can monkeypatch just the
    retry backoff (skip the wait, record the calls) without touching the
    shared asyncio.sleep used elsewhere in the process."""

    await asyncio.sleep(seconds)


def _parse_evidence(payload: object) -> list[Evidence]:
    """Parses the /v1/rag/query response body into a list of Evidence.

    The documented contract describes "a ranked list of evidence items" with
    per-item fields (id, rank, text, distance, metadata.*) but doesn't pin
    down whether the top-level JSON is a bare array or an object wrapping
    one -- accept either shape defensively: a bare list, or a dict with the
    items under one of a few plausible keys. Verify against the real API's
    actual response shape once available and simplify this if it turns out
    to always be one specific shape.
    """

    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = None
        for key in ("results", "items", "evidence", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                items = value
                break
        if items is None:
            raise ValueError("response body did not contain a recognizable list of evidence items")
    else:
        raise ValueError("response body was neither a JSON array nor an object")

    evidence: list[Evidence] = []
    for item in items:
        metadata = item.get("metadata") or {}
        evidence.append(
            Evidence(
                id=str(item["id"]),
                rank=int(item["rank"]),
                text=item["text"],
                distance=float(item["distance"]),
                chapter=metadata.get("chapter"),
                section=metadata.get("section"),
                content_type=metadata.get("content_type"),
                source_file=metadata.get("source_file"),
                title=metadata.get("title"),
            )
        )
    return evidence


class RagClientAdapter(EvidenceRetriever):
    """EvidenceRetriever backed by an HTTP call to the RAG API's POST /v1/rag/query."""

    def __init__(self, settings: Settings | None = None, transport: httpx.BaseTransport | None = None) -> None:
        self._settings = settings or get_settings()
        # Only ever set in tests, via httpx.MockTransport -- real callers (app/api/deps.py)
        # never pass this, so httpx.AsyncClient falls back to its normal network transport.
        self._transport = transport

    async def retrieve(
        self,
        query: str,
        top_k: int = 5,
        content_types: list[str] | None = None,
    ) -> list[Evidence]:
        base_url = self._settings.rag_api_base_url
        api_key = self._settings.rag_api_key
        if not base_url or not api_key:
            raise RagServiceUnavailableError(
                _SERVICE_NAME,
                "RAG_API_BASE_URL and/or RAG_API_KEY is not configured (see .env.example).",
            )

        body: dict[str, object] = {"query": query, "top_k": top_k}
        if content_types:
            body["content_types"] = content_types

        # Connect/read configured separately per the RAG API's documented contract
        # (connect: 5s, read: 60s) -- a dead/unreachable host should fail fast
        # rather than wait the full read budget. write/pool reuse the read/connect
        # values respectively; httpx.Timeout has no single-purpose default for
        # those two, and there's no more specific guidance to configure them from.
        timeout = httpx.Timeout(
            connect=self._settings.rag_api_timeout_connect_seconds,
            read=self._settings.rag_api_timeout_read_seconds,
            write=self._settings.rag_api_timeout_read_seconds,
            pool=self._settings.rag_api_timeout_connect_seconds,
        )
        headers = {"X-API-Key": api_key.get_secret_value()}

        # Connection-level errors and non-503 statuses raise immediately (single
        # attempt, no retry) -- only a 503 response body loops back for another
        # attempt, up to max_attempts total, per the documented client contract.
        max_attempts = max(1, self._settings.rag_api_max_attempts)
        response: httpx.Response | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                async with httpx.AsyncClient(
                    base_url=base_url, timeout=timeout, headers=headers, transport=self._transport
                ) as client:
                    response = await client.post("/v1/rag/query", json=body)
            except httpx.TimeoutException as exc:
                logger.warning("RAG API request timed out")
                raise RagServiceUnavailableError(_SERVICE_NAME, "request timed out") from exc
            except httpx.RequestError as exc:
                # Log only the exception's type/class, never str(exc) -- httpx request
                # errors can embed the request URL (which may carry query params) and,
                # in some cases, echo back parts of the request; keep this generic.
                logger.warning("RAG API request failed: %s", type(exc).__name__)
                raise RagServiceUnavailableError(_SERVICE_NAME, "connection failed") from exc

            if response.status_code == 200:
                break

            if response.status_code == 503 and attempt < max_attempts:
                backoff_seconds = self._settings.rag_api_retry_backoff_seconds * (2 ** (attempt - 1))
                logger.warning(
                    "RAG API returned HTTP 503 (attempt %d/%d) -- retrying in %.1fs",
                    attempt,
                    max_attempts,
                    backoff_seconds,
                )
                await _backoff_sleep(backoff_seconds)
                continue

            logger.warning("RAG API returned HTTP %s", response.status_code)
            raise RagServiceUnavailableError(_SERVICE_NAME, f"returned HTTP {response.status_code}")

        assert response is not None  # the loop above always either breaks or raises

        try:
            payload = response.json()
            return _parse_evidence(payload)
        except (ValueError, KeyError, TypeError) as exc:
            logger.warning("RAG API returned an unexpected response body")
            raise RagServiceUnavailableError(_SERVICE_NAME, "response body was not in the expected shape") from exc
