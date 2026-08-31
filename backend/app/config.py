"""Application configuration, loaded from environment variables / a .env file."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite+aiosqlite:///./app.db"
    db_echo: bool = False
    cors_origins: list[str] = ["*"]

    # Which PatientReplyGenerator implementation app/api/deps.py wires up.
    # "qwen"  -- app/infrastructure/qwen_patient_generator.py (local Qwen2.5-0.5B-Instruct,
    #            CPU-friendly; falls back to a placeholder reply if the model can't be loaded/run).
    # "stub"  -- app/infrastructure/patient_reply.py (fixed echo, no ML dependency at all).
    patient_reply_backend: Literal["qwen", "stub"] = "qwen"

    # Which EvaluationGenerator implementation app/api/deps.py wires up for
    # POST /sessions/{id}/evaluate. Only "stub" exists today (see
    # app/infrastructure/stub_evaluator.py) -- deliberately pluggable so a
    # real/local LLM-backed evaluator can be added as a new Literal member
    # later without changing the use case or the API layer.
    evaluation_backend: Literal["stub"] = "stub"

    # Which SpeechToTextPort implementation app/api/deps.py wires up for
    # POST /transcribe, POST /sessions/{id}/chat-voice, and WS /ws/voice.
    # "whisper" -- app/infrastructure/local_whisper_stt_adapter.py (faster-whisper
    #              "tiny" model, forced Arabic, CPU-only; falls back to the stub
    #              adapter on load/inference failure).
    # "stub"    -- app/infrastructure/stub_stt.py (canned placeholder transcript,
    #              no ML dependency at all -- what the test suite uses by default,
    #              see tests/conftest.py).
    stt_backend: Literal["whisper", "stub"] = "whisper"

    # Which TextToSpeechPort implementation app/api/deps.py wires up (same
    # three endpoints as stt_backend).
    # "gtts"  -- app/infrastructure/local_tts_adapter.py (gTTS; note this makes a
    #            network call per request -- not offline, despite the "Local"
    #            adapter-class name matching this project's naming convention.
    #            Falls back to the stub adapter's silent placeholder WAV on
    #            failure. "piper" -- a genuinely offline engine -- was
    #            considered but isn't implemented yet: it needs espeak-ng, a
    #            system dependency that doesn't install via pip alone.)
    # "stub"  -- app/infrastructure/stub_tts.py (silent placeholder WAV, no
    #            network/ML dependency -- what the test suite uses by default).
    tts_backend: Literal["gtts", "stub"] = "gtts"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
