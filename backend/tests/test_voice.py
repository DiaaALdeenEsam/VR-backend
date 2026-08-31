"""Tests for the voice pipeline: TranscribeAudioUseCase, ProcessVoiceChatUseCase,
POST /transcribe, POST /sessions/{id}/chat-voice, and WS /ws/voice.

Everything except the WS tests uses the shared async `client`/`app`/`seed_ids`
fixtures from conftest.py, which already override get_speech_to_text_port and
get_text_to_speech_port with the stub adapters -- fast, deterministic, no
network/ML dependency.

The WS tests are fully self-contained (see the big comment above them for
why) and do not use those shared fixtures.
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path

from app.infrastructure.stub_stt import StubSTTAdapter
from app.infrastructure.stub_tts import StubTTSAdapter

BACKEND_DIR = Path(__file__).resolve().parent.parent

# ---------- ports, tested directly (no DB, no HTTP) ----------


async def test_stub_stt_adapter_returns_labeled_placeholder() -> None:
    adapter = StubSTTAdapter()

    text = await adapter.transcribe(b"\x00\x01\x02\x03", filename="clip.wav")

    assert isinstance(text, str)
    assert text.strip() != ""
    assert "clip.wav" in text


async def test_stub_tts_adapter_returns_valid_wav_bytes() -> None:
    adapter = StubTTSAdapter()

    audio = await adapter.synthesize("أي نص")

    assert isinstance(audio, bytes)
    assert len(audio) > 0
    assert audio[:4] == b"RIFF"  # WAV container magic bytes
    assert audio[8:12] == b"WAVE"


# ---------- TranscribeAudioUseCase / POST /transcribe ----------


async def test_transcribe_endpoint_returns_text(client) -> None:
    files = {"file": ("clip.wav", b"\x00\x01\x02\x03", "audio/wav")}

    response = await client.post("/transcribe", files=files)

    assert response.status_code == 200
    body = response.json()
    assert body["text"].strip() != ""


# ---------- ProcessVoiceChatUseCase / POST /sessions/{id}/chat-voice ----------


async def test_chat_voice_endpoint_returns_transcript_reply_and_audio(client, seed_ids) -> None:
    create_resp = await client.post("/sessions", json={"scenario_id": seed_ids["scenario_id"]})
    session_id = create_resp.json()["session_id"]

    files = {"file": ("clip.wav", b"\x00\x01\x02\x03", "audio/wav")}
    response = await client.post(f"/sessions/{session_id}/chat-voice", files=files)

    assert response.status_code == 200
    body = response.json()
    assert body["transcribed_text"].strip() != ""
    assert body["reply"]["role"] == "assistant"
    assert body["reply"]["content"].strip() != ""
    assert body["reply_audio_content_type"] == "audio/wav"  # stub backend in tests

    audio_bytes = base64.b64decode(body["reply_audio_base64"])
    assert audio_bytes[:4] == b"RIFF"


async def test_chat_voice_persists_both_messages(client, seed_ids) -> None:
    create_resp = await client.post("/sessions", json={"scenario_id": seed_ids["scenario_id"]})
    session_id = create_resp.json()["session_id"]

    files = {"file": ("clip.wav", b"\x00\x01\x02\x03", "audio/wav")}
    await client.post(f"/sessions/{session_id}/chat-voice", files=files)

    review_resp = await client.get(f"/sessions/{session_id}")
    messages = review_resp.json()["messages"]

    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[1]["role"] == "assistant"


async def test_chat_voice_unknown_session_is_404(client) -> None:
    files = {"file": ("clip.wav", b"\x00\x01\x02\x03", "audio/wav")}

    response = await client.post("/sessions/does-not-exist/chat-voice", files=files)

    assert response.status_code == 404


# ---------- WS /ws/voice ----------
#
# httpx.AsyncClient's ASGITransport (used by the `client` fixture above) is
# HTTP-only -- it can't perform a WebSocket upgrade. Testing the WS route
# needs fastapi.testclient.TestClient instead, which is synchronous and runs
# the whole app in its own event loop via an anyio blocking portal, in its
# own thread. That thread-and-loop boundary is incompatible with the shared
# `engine`/`session_factory` fixtures above (an in-memory SQLite engine is
# bound to the loop that created it; TestClient's portal loop is a different
# one). So these tests build their own fully self-contained setup instead:
#   - a real temp *file* SQLite DB (not `:memory:`) -- file-based storage has
#     no loop/thread affinity, so it can be migrated+seeded from one
#     throwaway setup step here, then read again later from TestClient's
#     separate portal loop, with no engine object shared between the two.
#   - Settings pointed at that file (and at stub backends) via monkeypatched
#     env vars, set *before* running migrations (env.py reads DATABASE_URL
#     from Settings, not from alembic.ini) and *before* create_app(), with
#     get_settings()'s lru_cache cleared before and after.


def _run_migrations() -> None:
    """Sync on purpose: alembic's command.upgrade() -> our env.py's
    run_migrations_online() calls asyncio.run() internally, which raises if
    called from within an already-running event loop. Call this before any
    asyncio.run()/async fixture in the calling test, never from inside one.
    """

    from alembic import command
    from alembic.config import Config

    alembic_cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.upgrade(alembic_cfg, "head")


async def _seed_one_scenario() -> int:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlmodel.ext.asyncio.session import AsyncSession

    from app.config import get_settings
    from app.infrastructure.db.models import ScenarioModel

    engine = create_async_engine(get_settings().database_url)
    try:
        session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with session_factory() as session:
            scenario = ScenarioModel(name="سيناريو WS", case_text="نص الحالة.", gold_standard="معيار")
            session.add(scenario)
            await session.commit()
            assert scenario.id is not None
            return scenario.id
    finally:
        await engine.dispose()


def _prepare_ws_test_app(tmp_path: Path, monkeypatch, db_name: str):
    """Points DATABASE_URL/*_BACKEND at a fresh temp-file DB + stub backends,
    runs migrations and seeds one scenario, then returns (app, scenario_id).
    Callers must call get_settings.cache_clear() again after the test.
    """

    from app.config import get_settings
    from app.main import create_app

    db_path = tmp_path / db_name
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv("STT_BACKEND", "stub")
    monkeypatch.setenv("TTS_BACKEND", "stub")
    monkeypatch.setenv("PATIENT_REPLY_BACKEND", "stub")
    get_settings.cache_clear()

    _run_migrations()
    scenario_id = asyncio.run(_seed_one_scenario())

    return create_app(), scenario_id


def test_voice_websocket_round_trip(tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from app.config import get_settings

    app, scenario_id = _prepare_ws_test_app(tmp_path, monkeypatch, "ws_test.db")

    try:
        with TestClient(app) as test_client:
            create_resp = test_client.post("/sessions", json={"scenario_id": scenario_id})
            assert create_resp.status_code == 201
            session_id = create_resp.json()["session_id"]

            with test_client.websocket_connect(f"/ws/voice?session_id={session_id}") as ws:
                ws.send_bytes(b"\x00\x01\x02\x03")

                transcript_frame = ws.receive_json()
                assert transcript_frame["type"] == "transcript"
                assert transcript_frame["text"].strip() != ""

                reply_frame = ws.receive_json()
                assert reply_frame["type"] == "reply"
                assert reply_frame["text"].strip() != ""
                assert reply_frame["audio_content_type"] == "audio/wav"

                audio_bytes = ws.receive_bytes()
                assert audio_bytes[:4] == b"RIFF"
    finally:
        get_settings.cache_clear()


def test_voice_websocket_unknown_session_closes_with_error(tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from app.config import get_settings

    app, _scenario_id = _prepare_ws_test_app(tmp_path, monkeypatch, "ws_test_404.db")

    try:
        with TestClient(app) as test_client:
            with test_client.websocket_connect("/ws/voice?session_id=does-not-exist") as ws:
                ws.send_bytes(b"\x00\x01\x02\x03")
                error_frame = ws.receive_json()
                assert error_frame["type"] == "error"
    finally:
        get_settings.cache_clear()
