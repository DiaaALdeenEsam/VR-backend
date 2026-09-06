"""Manual, human-driven live test of the chat-voice + WS push flow, end to
end, from the terminal -- no Swagger, no browser.

Opens the WS connection to /ws/voice?session_id=... BEFORE submitting the
message (avoiding the race where the background reply lands before anything
is listening for it -- see app/infrastructure/ws_hub.py: a push with no
registered connection is a silent no-op), then submits the message via
either POST /sessions/{id}/messages (--text) or POST /sessions/{id}/chat-voice
(--audio-file), and prints every WS frame as it arrives, live, not buffered.

Uses the exact frame shapes the real server sends (see
app/application/use_cases/post_message_async.py's push() calls and
app/api/routers/voice.py's WS handler):
  - {"type": "transcript", "text": ...}                              (chat-voice/WS-audio-input turns only)
  - {"type": "thinking", "id": ...}                                  (if generation is still running after
                                                                       Settings.rag_patient_filler_after_seconds)
  - {"type": "reply", "id", "status", "created_at", + "content"}     (async push -- the only path the real
                                                                       server takes; the sole PatientReplyGenerator
                                                                       is the RAG API, always 6-48s-per-call shaped)
  - {"type": "reply", "id", "status", "created_at", + "text",        (synchronous WS-frame shape -- only ever
     "audio_content_type"}                                            seen if a test wires the sync path in;
                                                                       included for robustness, not expected live)
  - {"type": "error", "code"?, "detail": ...}                        (SessionBusyError / other DomainError)
A binary frame (reply audio) follows the "reply" frame only when the turn
went through --audio-file (TTS-enabled) -- never for --text.

Usage:
    PYTHONPATH=. python scripts/test_voice_ws_live.py --scenario-id 2 --text "..."
    PYTHONPATH=. python scripts/test_voice_ws_live.py --session-id <id> --audio-file clip.ogg
    PYTHONPATH=. python scripts/test_voice_ws_live.py --session-id <id> --text "..." --base-url http://127.0.0.1:8199
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import mimetypes
import sys
import time
from pathlib import Path

import httpx
import websockets

# Matches Settings.rag_patient_hard_timeout_seconds (app/config.py) plus a
# margin for scheduling/DB overhead around the call itself, so this script's
# own give-up point is never tighter than the server's own hard deadline.
DEFAULT_TIMEOUT_SECONDS = 65.0 + 10.0


def _ts() -> str:
    return time.strftime("%H:%M:%S", time.localtime()) + f".{int(time.time() * 1000) % 1000:03d}"


def _log(message: str) -> None:
    print(f"[{_ts()}] {message}")


def _ws_url(base_url: str, session_id: str) -> str:
    ws_base = base_url.replace("https://", "wss://").replace("http://", "ws://")
    return f"{ws_base}/ws/voice?session_id={session_id}"


async def _create_session(client: httpx.AsyncClient, scenario_id: int) -> str:
    r = await client.post("/sessions", json={"scenario_id": scenario_id})
    if r.status_code != 201:
        _log(f"ERROR: could not create session (status={r.status_code}): {r.text}")
        raise SystemExit(1)
    session_id = r.json()["session_id"]
    _log(f"Created session {session_id!r} for scenario_id={scenario_id}")
    return session_id


async def _send_text(client: httpx.AsyncClient, session_id: str, text: str) -> dict:
    r = await client.post(f"/sessions/{session_id}/messages", json={"content": text})
    _log(f"POST /sessions/{{id}}/messages -> {r.status_code}")
    if r.status_code != 201:
        _log(f"  body: {r.text}")
        raise SystemExit(1)
    body = r.json()
    _log(f"  immediate response: id={body['id']} status={body['status']!r} content={body['content']!r}")
    return body


async def _send_audio(client: httpx.AsyncClient, session_id: str, audio_path: Path) -> dict:
    mime_type = mimetypes.guess_type(audio_path.name)[0] or "application/octet-stream"
    files = {"file": (audio_path.name, audio_path.read_bytes(), mime_type)}
    r = await client.post(f"/sessions/{session_id}/chat-voice", files=files)
    _log(f"POST /sessions/{{id}}/chat-voice -> {r.status_code}")
    if r.status_code != 200:
        _log(f"  body: {r.text}")
        raise SystemExit(1)
    body = r.json()
    _log(f"  transcribed_text: {body['transcribed_text']!r}")
    reply = body["reply"]
    _log(f"  immediate reply: id={reply['id']} status={reply['status']!r} content={reply['content']!r}")
    if body.get("reply_audio_base64"):
        # Already-final audio (only possible if the server happened to be
        # wired to the synchronous path) -- save it now, same as the WS
        # binary-frame case below.
        _save_audio(base64.b64decode(body["reply_audio_base64"]), body.get("reply_audio_content_type"))
    return reply


def _save_audio(data: bytes, content_type: str | None) -> Path:
    extension = "wav"
    if content_type and "ogg" in content_type:
        extension = "ogg"
    elif content_type and "mpeg" in content_type:
        extension = "mp3"
    path = Path(f"reply_audio_{int(time.time())}.{extension}")
    path.write_bytes(data)
    return path


async def _listen_and_wait_for_reply(ws: websockets.WebSocketClientProtocol, timeout: float) -> dict | None:
    """Prints every frame as it arrives, live. Returns the final "reply" frame's
    JSON payload once its status leaves "pending"/"generating", or None on
    timeout. Keeps listening a short extra window afterward for a possible
    trailing binary audio frame, saving it if one arrives."""

    deadline = time.monotonic() + timeout
    final_payload: dict | None = None

    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        try:
            frame = await asyncio.wait_for(ws.recv(), timeout=remaining)
        except asyncio.TimeoutError:
            break

        if isinstance(frame, bytes):
            path = _save_audio(frame, None)
            _log(f"WS binary frame received ({len(frame)} bytes) -- saved to {path}")
            continue

        payload = json.loads(frame)
        _log(f"WS frame: {payload}")

        if payload.get("type") == "error":
            _log("  -> server reported an error; stopping.")
            return payload

        if payload.get("type") == "reply" and payload.get("status") in ("complete", "failed"):
            final_payload = payload
            break

    if final_payload is None:
        return None

    # Short extra window for a trailing binary audio frame (only sent when
    # the turn went through --audio-file / TTS-enabled path, and only if the
    # reply wasn't "failed"). Not an error if nothing arrives -- expected for
    # --text.
    if final_payload.get("status") == "complete":
        try:
            frame = await asyncio.wait_for(ws.recv(), timeout=5.0)
        except asyncio.TimeoutError:
            _log("(no audio frame followed -- expected for --text turns)")
        else:
            if isinstance(frame, bytes):
                path = _save_audio(frame, None)
                _log(f"WS binary frame received ({len(frame)} bytes) -- saved to {path}")
            else:
                _log(f"WS frame: {json.loads(frame)}")

    return final_payload


async def main(args: argparse.Namespace) -> None:
    async with httpx.AsyncClient(base_url=args.base_url, timeout=30.0) as client:
        session_id = args.session_id or await _create_session(client, args.scenario_id)

        ws_url = _ws_url(args.base_url, session_id)
        _log(f"Opening WS connection: {ws_url}")
        async with websockets.connect(ws_url) as ws:
            _log("WS connected -- listening before sending the message (avoids the push-arrives-first race).")

            if args.text is not None:
                await _send_text(client, session_id, args.text)
            else:
                await _send_audio(client, session_id, Path(args.audio_file))

            _log(f"Waiting for the real reply (timeout={args.timeout:.0f}s)...")
            final_payload = await _listen_and_wait_for_reply(ws, args.timeout)

    print()
    if final_payload is None:
        _log(f"NO REPLY RECEIVED within {args.timeout:.0f}s -- giving up.")
        raise SystemExit(1)

    if final_payload.get("type") == "error":
        _log(f"SERVER ERROR: {final_payload.get('detail')}")
        raise SystemExit(1)

    reply_text = final_payload.get("content", final_payload.get("text", ""))
    _log(f"FINAL STATUS: {final_payload['status']}")
    _log(f"PATIENT REPLY: {reply_text!r}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--session-id", help="Use an existing session instead of creating a new one.")
    parser.add_argument(
        "--scenario-id", type=int, default=2, help="Scenario for a newly-created session (default: 2)."
    )

    message_group = parser.add_mutually_exclusive_group(required=True)
    message_group.add_argument("--text", help="Send a text message via POST /sessions/{id}/messages.")
    message_group.add_argument(
        "--audio-file", help="Send an audio file via POST /sessions/{id}/chat-voice (multipart upload)."
    )

    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Server base URL (default: %(default)s).")
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Seconds to wait for the real reply before giving up (default: {DEFAULT_TIMEOUT_SECONDS:.0f}, "
        "matching Settings.rag_patient_hard_timeout_seconds plus margin).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main(_parse_args()))
