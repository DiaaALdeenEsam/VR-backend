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
"""

from __future__ import annotations

import logging

from fastapi import WebSocket

from app.domain.repositories import ReplyPushPort

logger = logging.getLogger(__name__)


class WebSocketPushHub(ReplyPushPort):
    def __init__(self) -> None:
        self._connections: dict[str, WebSocket] = {}

    def register(self, session_id: str, websocket: WebSocket) -> None:
        self._connections[session_id] = websocket

    def unregister(self, session_id: str, websocket: WebSocket) -> None:
        # Only clear the slot if it's still this exact connection -- a second
        # connection for the same session_id (e.g. a client reconnect) may
        # have already replaced it, and we must not tear down the newer one.
        if self._connections.get(session_id) is websocket:
            del self._connections[session_id]

    async def push(self, session_id: str, payload: dict) -> bool:
        websocket = self._connections.get(session_id)
        if websocket is None:
            return False
        try:
            await websocket.send_json(payload)
            return True
        except Exception:
            logger.warning("ws_hub: push failed for session_id=%s (connection likely closed)", session_id)
            return False

    async def push_bytes(self, session_id: str, data: bytes) -> bool:
        websocket = self._connections.get(session_id)
        if websocket is None:
            return False
        try:
            await websocket.send_bytes(data)
            return True
        except Exception:
            logger.warning("ws_hub: push_bytes failed for session_id=%s (connection likely closed)", session_id)
            return False


# Process-wide singleton -- see module docstring for why this must be shared,
# not constructed fresh per request the way get_uow() is.
_singleton = WebSocketPushHub()


def get_singleton() -> WebSocketPushHub:
    return _singleton
