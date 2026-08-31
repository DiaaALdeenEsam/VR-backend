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
