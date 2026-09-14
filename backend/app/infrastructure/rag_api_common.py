"""Small shared constants/helpers used by every adapter that talks to the
external RAG API (rag_client_adapter.py, rag_patient_generator.py,
rag_llm_evaluator.py) and by rag_tunnel_watcher.py -- kept in one place
rather than repeated per file, since all four talk to the same external
service under the same client contract (docs/backend-rag-handoff.md).
"""

from __future__ import annotations

# Sent as the User-Agent header on every request to the RAG API. The
# temporary nport tunnel fronting it has been observed to reject requests
# carrying certain default HTTP-library User-Agent signatures with a 403 --
# see docs/backend-rag-handoff.md's connection notes ("send an explicit
# application User-Agent such as VR-Project-Backend/1.0; the temporary edge
# may reject Python's default Python-urllib signature with HTTP 403"). This
# project uses httpx, not urllib, so httpx's own default
# ("python-httpx/<version>") is what would otherwise be sent -- not
# confirmed either way whether nport's filter also rejects that specific
# signature, but sending an explicit, identifiable one instead is the doc's
# own recommendation and costs nothing.
RAG_API_USER_AGENT = "VR-Project-Backend/1.0"

# Per docs/backend-rag-handoff.md's client-behavior table: "retry only
# transient 503 failures, and avoid retrying 401 or 422 responses" -- this
# applies uniformly across every route on this service, unlike the
# per-route _RETRYABLE_ERROR_CODES allowlists each adapter defines
# separately for its own transient-error codes.
_VALIDATION_STATUS_CODES = frozenset({401, 422})


def is_validation_status(status_code: int) -> bool:
    """True for a status code that means "this specific request is invalid;
    retrying it unchanged will never succeed" -- as opposed to a transient
    upstream/connection problem. Used by every RAG adapter to decide whether
    a failed response should raise RagValidationError
    (app/domain/exceptions.py) instead of the more general
    RagServiceUnavailableError."""

    return status_code in _VALIDATION_STATUS_CODES
