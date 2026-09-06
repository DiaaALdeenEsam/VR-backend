"""Domain-level errors.

These carry no knowledge of HTTP or persistence; the API layer maps them to status
codes (see app/api/error_handlers.py).
"""

from __future__ import annotations

from typing import Any


class DomainError(Exception):
    """Base class for all domain-level errors."""


class NotFoundError(DomainError):
    """Raised when a requested aggregate does not exist."""

    def __init__(self, entity: str, identifier: Any) -> None:
        self.entity = entity
        self.identifier = identifier
        super().__init__(f"{entity} not found: {identifier!r}")


class InvalidChoiceError(DomainError):
    """Raised when a submitted choice_id does not belong to the given question."""

    def __init__(self, question_id: Any, choice_id: Any) -> None:
        self.question_id = question_id
        self.choice_id = choice_id
        super().__init__(f"choice {choice_id!r} does not belong to question {question_id!r}")


class MissingGoldStandardError(DomainError):
    """Raised when evaluating a session whose scenario has no gold_standard set.

    Not given its own registered handler in app/api/error_handlers.py -- the
    generic DomainError -> 400 handler already covers it, since there's
    nothing more specific than "bad request" to say here.
    """

    def __init__(self, scenario_id: Any) -> None:
        self.scenario_id = scenario_id
        super().__init__(f"scenario {scenario_id!r} has no gold_standard set; cannot evaluate")


class VoiceServiceUnavailableError(DomainError):
    """Raised when a remote STT/TTS backend (e.g. the Colab-hosted API) can't be
    reached or fails -- not configured, connection refused/timed out, or an
    error response. Distinct from the generic DomainError -> 400 mapping: this
    is "an upstream service is unreachable/failing", not "bad request", so it
    gets its own handler (see app/api/error_handlers.py) mapping to 502.
    """

    def __init__(self, service: str, reason: str) -> None:
        self.service = service
        self.reason = reason
        super().__init__(f"{service} is unavailable: {reason}")


class SessionBusyError(DomainError):
    """Raised when a doctor sends a new message while a previous reply for
    the same session is still being generated in the background (see
    PostMessageAsyncUseCase). Chosen over silently queueing or cancelling
    the in-flight call -- see that use case's module docstring for why.

    Given its own registered handler in app/api/error_handlers.py, mapping
    to 409 Conflict: distinct from the generic DomainError -> 400 mapping,
    since "the request is fine, but the session isn't ready for it yet" is a
    conflict with current state, not a malformed request.
    """

    def __init__(self, session_id: Any) -> None:
        self.session_id = session_id
        super().__init__(f"session {session_id!r} already has a reply being generated")


class RagServiceUnavailableError(DomainError):
    """Raised when the RAG API (app/infrastructure/rag_client_adapter.py) can't
    be reached or fails -- not configured, connection refused/timed out, or an
    error response. Same shape and reasoning as VoiceServiceUnavailableError.

    Unlike VoiceServiceUnavailableError, this is NOT given a registered
    handler in app/api/error_handlers.py (no 502 mapping) -- by design, this
    exception is meant to be caught inside PostMessageUseCase and turned into
    a graceful degrade (proceed with no evidence) rather than ever reaching
    the API layer. See PostMessageUseCase.execute() for where that happens.
    """

    def __init__(self, service: str, reason: str) -> None:
        self.service = service
        self.reason = reason
        super().__init__(f"{service} is unavailable: {reason}")
