"""WebSocketPushHub: the concrete ReplyPushPort (app/domain/repositories.py)
backing the async reply-generation path's delivery mechanism -- see
PostMessageAsyncUseCase and app/api/routers/voice.py.

WS /ws/voice already gives every session a persistent, bidirectional,
per-session connection (client connects once with ?session_id=..., server can
send it JSON text frames and binary audio frames at will) -- exactly the
shape needed to push a delayed reply without the client polling. This module
just makes that connection reachable from outside the request that accepted
it: voice.py's WS handler registers/unregisters itself here, and
PostMessageAsyncUseCase calls push()/push_bytes() through the ReplyPushPort
interface to deliver a finished background generation, whether or not the
doctor's original message arrived via WS, POST /sessions/{id}/messages, or
POST /sessions/{id}/chat-voice -- the push channel is decoupled from how the
turn was submitted.

Assumes at most one live connection per session_id (matching /ws/voice's own
existing design -- it already keys connections by session_id alone). A push
with no registered connection for that session_id is a silent no-op: the
client wasn't listening (e.g. it only ever used the REST endpoints and never
opened the socket) -- a real gap, not a bug; see PostMessageAsyncUseCase's
module docstring for the REST-polling fallback for exactly that case.

app/api/deps.py wires a single module-level instance
(get_reply_push_port()) shared across every request -- registrations made by
one request's WS connection must be visible to a background task spawned by
a completely different request.

--- Single-writer locking ------------------------------------------------

A given session's WebSocket can have writers from up to three independent
coroutine contexts at once, none of which know about each other: (1) the
main receive loop in app/api/routers/voice.py, sending transcript/reply-
pending frames inline for the frame it just processed; (2)
PostMessageAsyncUseCase._generate_and_push()'s own background task, pushing
the finished reply (and, for voice turns, its synthesized audio) once
generation completes; (3) that same use case's _push_filler_after_delay()
background task, pushing an interim "thinking" event. Starlette's
WebSocket.send_json()/send_bytes() are NOT safe to call concurrently from
multiple coroutines on the same connection -- with uvicorn's `websockets`
backend in particular (still built on websockets.legacy.server.
WebSocketServerProtocol even in that library's latest release -- see
requirements.txt's comment on this), two concurrent writes can race against
the legacy protocol's own internal keepalive pong response and trip its
`assert waiter is None or waiter.cancelled()` invariant in
_drain_helper(), tearing the connection down.

register() creates one asyncio.Lock per session_id alongside its WebSocket;
push()/push_bytes() below acquire it around every send on that connection,
and voice.py's handler acquires the exact same lock object (via get_lock())
around its own inline sends -- so every writer, from any of the three
contexts above, is serialized through one lock per connection, never two
writes racing on the wire at once.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import WebSocket

from app.domain.repositories import ReplyPushPort

logger = logging.getLogger(__name__)


class WebSocketPushHub(ReplyPushPort):
    def __init__(self) -> None:
        self._connections: dict[str, WebSocket] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def register(self, session_id: str, websocket: WebSocket) -> None:
        self._connections[session_id] = websocket
        self._locks[session_id] = asyncio.Lock()

    def unregister(self, session_id: str, websocket: WebSocket) -> None:
        # Only clear the slot if it's still this exact connection -- a second
        # connection for the same session_id (e.g. a client reconnect) may
        # have already replaced it, and we must not tear down the newer one.
        if self._connections.get(session_id) is websocket:
            del self._connections[session_id]
            self._locks.pop(session_id, None)

    def get_lock(self, session_id: str) -> asyncio.Lock | None:
        """The single asyncio.Lock guarding every write to this session's
        WebSocket -- see this module's docstring. Callers that write to the
        connection directly (app/api/routers/voice.py's own inline sends)
        must acquire this around each send_json()/send_bytes()/send_text()
        call, exactly like push()/push_bytes() below already do, so no two
        coroutines ever write to the same connection concurrently. None iff
        no connection is currently registered for this session_id (mirrors
        push()/push_bytes()'s own "no active connection" no-op case).
        """

        return self._locks.get(session_id)

    async def push(self, session_id: str, payload: dict) -> bool:
        websocket = self._connections.get(session_id)
        lock = self._locks.get(session_id)
        if websocket is None or lock is None:
            logger.info("[ws_hub] push_skipped | session_id=%s | reason=no_active_connection", session_id)
            return False
        try:
            async with lock:
                await websocket.send_json(payload)
            logger.info(
                "[ws_hub] push_delivered | session_id=%s | payload_type=%s", session_id, payload.get("type")
            )
            return True
        except Exception:
            logger.warning("[ws_hub] push_failed | session_id=%s | reason=connection likely closed", session_id)
            return False

    async def push_bytes(self, session_id: str, data: bytes) -> bool:
        websocket = self._connections.get(session_id)
        lock = self._locks.get(session_id)
        if websocket is None or lock is None:
            logger.info("[ws_hub] push_bytes_skipped | session_id=%s | reason=no_active_connection", session_id)
            return False
        try:
            async with lock:
                await websocket.send_bytes(data)
            logger.info(
                "[ws_hub] push_bytes_delivered | session_id=%s | bytes=%d", session_id, len(data)
            )
            return True
        except Exception:
            logger.warning(
                "[ws_hub] push_bytes_failed | session_id=%s | reason=connection likely closed", session_id
            )
            return False


# Process-wide singleton -- see module docstring for why this must be shared,
# not constructed fresh per request the way get_uow() is.
_singleton = WebSocketPushHub()


def get_singleton() -> WebSocketPushHub:
    return _singleton
