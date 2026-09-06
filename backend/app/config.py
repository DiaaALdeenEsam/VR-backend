"""Application configuration, loaded from environment variables / a .env file."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
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
    # "colab"   -- app/infrastructure/colab_stt_adapter.py (HTTP call to a
    #              self-hosted Whisper model exposed by the Colab notebook at
    #              colab/leva_stt_tts_notebook.py, via colab_api_base_url).
    # "whisper" -- DEPRECATED, kept for rollback only. app/infrastructure/legacy/
    #              local_whisper_stt_adapter.py (faster-whisper "tiny" model,
    #              forced Arabic, CPU-only; falls back to the stub adapter on
    #              load/inference failure).
    # "stub"    -- app/infrastructure/stub_stt.py (canned placeholder transcript,
    #              no ML dependency at all -- what the test suite uses by default,
    #              see tests/conftest.py).
    stt_backend: Literal["colab", "whisper", "stub"] = "colab"

    # Which TextToSpeechPort implementation app/api/deps.py wires up (same
    # three endpoints as stt_backend).
    # "colab" -- app/infrastructure/colab_tts_adapter.py (HTTP call to a
    #            self-hosted leva-tts model exposed by the Colab notebook at
    #            colab/leva_stt_tts_notebook.py, via colab_api_base_url).
    # "gtts"  -- DEPRECATED, kept for rollback only. app/infrastructure/legacy/
    #            local_tts_adapter.py (gTTS; makes a network call to Google
    #            Translate's TTS endpoint per request -- not offline, despite
    #            the "Local" adapter-class name. Falls back to the stub
    #            adapter's silent placeholder WAV on failure.)
    # "stub"  -- app/infrastructure/stub_tts.py (silent placeholder WAV, no
    #            network/ML dependency -- what the test suite uses by default).
    tts_backend: Literal["colab", "gtts", "stub"] = "colab"

    # Base URL of the Colab-hosted STT/TTS API (see colab/leva_stt_tts_notebook.py),
    # e.g. an ngrok URL like "https://xxxx-xx-xx-xxx-xx.ngrok-free.app". This
    # changes every time the Colab notebook is restarted (a free ngrok tunnel
    # gets a new random URL each run) -- update it here (or in .env) rather
    # than touching any code. Required only when stt_backend and/or
    # tts_backend is "colab"; the colab adapters raise a clear
    # VoiceServiceUnavailableError if it's unset when actually called.
    colab_api_base_url: str | None = None

    # Per-request timeout (seconds) for calls to the Colab API. Deliberately
    # generous relative to a typical local-SDK call: this is a real network
    # hop through ngrok to a Colab GPU runtime, which can be slow (cold model
    # state, free-tier throttling) or drop entirely -- see the colab adapters'
    # own docstrings for how a timeout/connection failure is surfaced.
    colab_api_timeout_seconds: float = 30.0

    # Base URL of the external RAG (retrieval-only) API (see
    # app/infrastructure/rag_client_adapter.py), e.g. a temporary nport tunnel
    # like "https://vr-rag-api.nport.link". Will change later -- update it
    # here (or in .env) rather than touching any code. Required for
    # EvidenceRetriever.retrieve() to work; the adapter raises a clear
    # RagServiceUnavailableError if it's unset when actually called (which
    # PostMessageUseCase catches and degrades from gracefully -- see there).
    rag_api_base_url: str | None = None

    # API key for the RAG API, sent as the `X-API-Key` header. SecretStr
    # (not plain str) deliberately -- this is the first real secret this
    # project's config has held; SecretStr keeps it out of repr()/str() (e.g.
    # an accidental `print(settings)` or an unhandled-exception traceback
    # that includes local variables) so it can't leak that way. Only ever
    # unwrap it with .get_secret_value(), and only at the point of the actual
    # HTTP request in rag_client_adapter.py -- never store or pass around the
    # unwrapped string elsewhere.
    rag_api_key: SecretStr | None = None

    # Connect/read timeouts (seconds) for calls to the RAG API, configured
    # separately per the RAG API's documented contract (connect: 5s, read:
    # 60s -- retrieval can be slow, but a dead/unreachable host should fail
    # fast rather than wait the full read timeout). No retry logic: a single
    # failed attempt raises RagServiceUnavailableError immediately -- see
    # rag_client_adapter.py.
    rag_api_timeout_connect_seconds: float = 5.0
    rag_api_timeout_read_seconds: float = 60.0

    # Retry policy for the RAG API, per its documented contract: retry transient
    # 503s with backoff, never retry 401/422 (see rag_client_adapter.py). 3 total
    # attempts (1 initial + 2 retries) with a 1s exponential backoff base (1s, 2s)
    # is a starting point, not a tuned value -- adjust once real 503 frequency
    # against the live API is observed.
    rag_api_max_attempts: int = 3
    rag_api_retry_backoff_seconds: float = 1.0

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
