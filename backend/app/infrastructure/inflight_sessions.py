"""InMemorySessionConcurrencyGuard: the concrete SessionConcurrencyGuard
(app/domain/repositories.py) backing PostMessageAsyncUseCase's concurrency
policy -- a second message for a session already generating a reply is
rejected with SessionBusyError (409), not queued or used to cancel the first
call. See that use case's module docstring for the full reasoning.

Deliberately a plain in-process dict, not a task queue/broker: this is a
single-process FastAPI app with SQLite (confirmed -- no Celery, Redis, or any
other queue infra anywhere in requirements.txt or the app/ tree), and there's
exactly one thing being tracked (a boolean "busy" per session_id) with a
lifetime no longer than one HTTP process's uptime. A real queue would be
solving a problem this project doesn't have yet.

This *is* process-local, ephemeral state, though: a server restart mid-
generation orphans any "pending"/"generating" row in the DB (it stays stuck
at that status, never marked "failed") -- acceptable for this stage, and no
worse than an equivalent in-memory task-queue's crash behavior would be, but
worth knowing about before this sees real traffic.

app/api/deps.py wires a single module-level instance (get_session_concurrency_guard())
shared across every request -- it must be one shared instance, not one per
request, for is_busy()/start() to mean anything across separate HTTP calls.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

from app.domain.repositories import SessionConcurrencyGuard


class InMemorySessionConcurrencyGuard(SessionConcurrencyGuard):
    def __init__(self) -> None:
        self._in_flight: dict[str, asyncio.Task] = {}

    def is_busy(self, session_id: str) -> bool:
        task = self._in_flight.get(session_id)
        return task is not None and not task.done()

    def start(self, session_id: str, coro: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(coro)
        self._in_flight[session_id] = task
        task.add_done_callback(lambda t: self._clear_if_current(session_id, t))

    def _clear_if_current(self, session_id: str, task: asyncio.Task) -> None:
        if self._in_flight.get(session_id) is task:
            del self._in_flight[session_id]


# Process-wide singleton -- see module docstring for why this must be shared,
# not constructed fresh per request the way get_uow() is.
_singleton = InMemorySessionConcurrencyGuard()


def get_singleton() -> InMemorySessionConcurrencyGuard:
    return _singleton


# A second, independent instance for EvaluateSessionAsyncUseCase
# (app/application/use_cases/evaluate_session_async.py) -- deliberately NOT
# the same instance as get_singleton() above, even though both key by the
# same session_id and both are "one background generation in flight per
# session" guards. A message reply generating in the background and an
# evaluation generating in the background are unrelated operations that
# happen to share a session_id; sharing one guard between them would make an
# in-flight message reply block an evaluate call (and vice versa) for a
# reason neither operation actually depends on. See SessionBusyError's
# `operation` parameter (app/domain/exceptions.py) for how the two are told
# apart in the 409 response text once each guard raises for its own kind of
# collision.
_evaluation_singleton = InMemorySessionConcurrencyGuard()


def get_evaluation_singleton() -> InMemorySessionConcurrencyGuard:
    return _evaluation_singleton
