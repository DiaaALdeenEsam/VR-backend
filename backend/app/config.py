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

    # --- Patient-reply settings ---------------------------------------------
    #
    # app/api/deps.py's get_patient_reply_generator() has exactly one
    # implementation: app/infrastructure/rag_patient_generator.py (external
    # RAG patient-chat API, POST /v1/rag/patient/chat). There used to be a
    # Settings.patient_reply_backend toggle here selecting between this, a
    # local Qwen2.5-0.5B-Instruct model, and a fixed-echo stub -- removed
    # (not narrowed to a single-member Literal) once live reproduction
    # confirmed Qwen hallucinates/breaks character systematically and the RAG
    # API's anti-leak boundary was separately verified to hold cleanly. There
    # is no other backend to configure, so there is nothing left to make a
    # setting out of -- a Literal with one member and a matching default
    # would be pure ceremony, since nothing would ever branch on it again.
    # (StubPatientReplyGenerator, app/infrastructure/patient_reply.py, still
    # exists and is still used -- but only via tests/conftest.py's direct
    # dependency_overrides, never selected through this file.)
    #
    # The settings below configure that one implementation. They reuse
    # rag_api_base_url / rag_api_key / rag_api_timeout_connect_seconds /
    # rag_api_max_attempts / rag_api_retry_backoff_seconds above -- POST
    # /v1/rag/patient/chat is the same external service, same base URL and
    # key, as POST /v1/rag/query (app/infrastructure/rag_client_adapter.py).

    # Persona sent on every patient-chat call (see docs/backend-rag-handoff.md's
    # "eight runtime personas" -- default is that route's own default). Fixed
    # per-deployment rather than per-request: the domain's PatientReplyGenerator
    # port has no concept of "persona" (see RagPatientReplyGenerator's module
    # docstring for why that's fine).
    rag_patient_persona: str = "middle_man"

    # Read timeout for a single patient-chat call. Set from real measurement,
    # not the handoff doc's generic guidance (150s) -- live-tested latency
    # against the real API ranged 6-48s across single-turn calls; 60s gives
    # headroom above the observed worst case without matching the use case's
    # own hard timeout below exactly (so a slow-but-still-alive HTTP response
    # isn't racing byte-for-byte against the orchestration timeout that also
    # has to account for scheduling/DB overhead around the call itself).
    rag_patient_read_timeout_seconds: float = 60.0

    # PostMessageAsyncUseCase's own hard deadline for the whole generate_reply()
    # call (adapter-internal retries included). Slightly above
    # rag_patient_read_timeout_seconds for the same reason noted there.
    rag_patient_hard_timeout_seconds: float = 65.0

    # How long to wait before pushing an interim "still thinking" filler event
    # over the session's WS channel, if the real reply isn't ready yet. Picked
    # from the observed latency distribution: roughly half of live-tested calls
    # finished faster than this, so most turns never see the filler at all;
    # the rest get an early acknowledgement instead of dead air.
    rag_patient_filler_after_seconds: float = 15.0

    # Fixed, in-character line pushed (a) as the filler at
    # rag_patient_filler_after_seconds if the real reply still isn't ready, and
    # (b) as the final assistant message content if generation ultimately
    # times out or fails -- see PostMessageAsyncUseCase. Deliberately generic
    # (never claims specific symptoms, never breaks character) since it has to
    # make sense standing alone regardless of which scenario or turn it lands
    # on.
    rag_patient_fallback_reply: str = "آسف، هل يمكنك إعادة السؤال؟ شردت شوي."

    # Which EvaluationGenerator implementation app/api/deps.py wires up for
    # POST /sessions/{id}/evaluate.
    # "rag_llm" -- the default. app/infrastructure/rag_llm_evaluator.py, an
    #              LLM-judge backed by the external RAG API's clinician-chat
    #              route (POST /v1/rag/chat, same service/base URL/key as
    #              RAG_API_BASE_URL / RAG_API_KEY below). Runs asynchronously
    #              via EvaluateSessionAsyncUseCase, same shape as the
    #              RAG-API-backed patient reply path.
    # "stub"     -- DEPRECATED escape hatch, kept only until "rag_llm" is
    #              confirmed working in production; not otherwise selected.
    #              app/infrastructure/stub_evaluator.py -- deterministic,
    #              rule-based placeholder grading, no ML/network dependency.
    #              Do not remove without confirming first (see that module's
    #              docstring).
    evaluation_backend: Literal["stub", "rag_llm"] = "rag_llm"

    # Read timeout for a single evaluation call to /v1/rag/chat
    # (app/infrastructure/rag_llm_evaluator.py). Set higher than
    # RAG_PATIENT_READ_TIMEOUT_SECONDS's observed 6-48s: the evaluation
    # prompt carries the full grading rubric plus case/transcript/test/quiz
    # data (see app/infrastructure/evaluation_prompt.py) rather than one
    # short conversational turn, and docs/backend-rag-handoff.md's own
    # generic guidance for direct chat is "at least" a 150s read timeout --
    # 90s/100s below stays comfortably under that ceiling while still
    # catching a genuine hang meaningfully faster than 150s would; revisit
    # once real /v1/rag/chat evaluation latency has been observed in
    # production the way the patient route's 6-48s range was.
    rag_evaluation_read_timeout_seconds: float = 90.0

    # EvaluateSessionAsyncUseCase's own hard deadline for the whole
    # evaluate() call (adapter-internal retries included). Slightly above
    # rag_evaluation_read_timeout_seconds for the same reason
    # rag_patient_hard_timeout_seconds sits above
    # rag_patient_read_timeout_seconds -- a slow-but-still-alive HTTP
    # response shouldn't race byte-for-byte against the orchestration
    # timeout that also has to account for scheduling/DB overhead around the
    # call itself.
    rag_evaluation_hard_timeout_seconds: float = 100.0

    # Which SpeechToTextPort implementation app/api/deps.py wires up for
    # POST /transcribe, POST /sessions/{id}/chat-voice, and WS /ws/voice.
    # "colab"     -- app/infrastructure/colab_stt_adapter.py (HTTP call to a
    #                self-hosted Whisper model exposed by the Colab notebook at
    #                colab/leva_stt_tts_notebook.py, via colab_api_base_url).
    # "whisper"   -- DEPRECATED, kept for rollback only. app/infrastructure/legacy/
    #                local_whisper_stt_adapter.py (faster-whisper "tiny" model,
    #                forced Arabic, CPU-only; falls back to the stub adapter on
    #                load/inference failure). NOT the same thing as "local_gpu"
    #                below -- this is a different, smaller, CPU-only model kept
    #                for a different reason (a light dependency-free rollback),
    #                not a GPU-accelerated equivalent of the Colab backend.
    # "local_gpu" -- app/infrastructure/local_gpu_stt_adapter.py: the exact same
    #                openai-whisper "small" model (device="cuda") the Colab
    #                notebook runs, loaded in-process instead of called over
    #                HTTP. Loaded once at startup by
    #                app/infrastructure/voice_models.py (see app/main.py's
    #                lifespan) -- forces an attempt to load regardless of
    #                local_gpu_min_free_vram_gb (an operator explicitly forcing
    #                this accepts a tighter-than-recommended fit), but still
    #                falls back to "colab" behavior automatically, without
    #                crashing the server, if the load fails for any reason (no
    #                GPU, missing deps, OOM, ...).
    # "auto"      -- Same local model as "local_gpu", but only attempted if
    #                voice_models.py's startup VRAM check (torch.cuda.mem_get_info())
    #                clears local_gpu_min_free_vram_gb first; falls straight to
    #                "colab" behavior otherwise, without attempting to load.
    # "stub"      -- app/infrastructure/stub_stt.py (canned placeholder transcript,
    #                no ML dependency at all -- what the test suite uses by default,
    #                see tests/conftest.py).
    stt_backend: Literal["colab", "whisper", "stub", "local_gpu", "auto"] = "colab"

    # Which TextToSpeechPort implementation app/api/deps.py wires up (same
    # endpoints as stt_backend).
    # "colab"     -- app/infrastructure/colab_tts_adapter.py (HTTP call to a
    #                self-hosted leva-tts model exposed by the Colab notebook at
    #                colab/leva_stt_tts_notebook.py, via colab_api_base_url).
    # "gtts"      -- DEPRECATED, kept for rollback only. app/infrastructure/legacy/
    #                local_tts_adapter.py (gTTS; makes a network call to Google
    #                Translate's TTS endpoint per request -- not offline, despite
    #                the "Local" adapter-class name. Falls back to the stub
    #                adapter's silent placeholder WAV on failure.)
    # "local_gpu" -- app/infrastructure/local_gpu_tts_adapter.py: the exact same
    #                leva-tts model/speaker (default "Amina") the Colab notebook
    #                runs, loaded in-process instead of called over HTTP. Same
    #                startup-loading/fallback behavior as STT's "local_gpu"
    #                above, decided independently of it (STT can end up local
    #                while TTS falls back to Colab, or vice versa, e.g. if only
    #                one of the two system dependencies leva-tts needs is
    #                missing -- see voice_models.py).
    # "auto"      -- Same local model as "local_gpu", gated by the same startup
    #                VRAM check as STT's "auto" above.
    # "stub"      -- app/infrastructure/stub_tts.py (silent placeholder WAV, no
    #                network/ML dependency -- what the test suite uses by default).
    tts_backend: Literal["colab", "gtts", "stub", "local_gpu", "auto"] = "colab"

    # Minimum free VRAM (GB) required for STT_BACKEND=auto / TTS_BACKEND=auto to
    # choose local GPU loading over the Colab fallback -- checked once at process
    # startup by app/infrastructure/voice_models.py via torch.cuda.mem_get_info()
    # (currently-free VRAM, not just the card's total capacity -- see that
    # module's docstring for why "free right now" is the number that matters).
    # Whisper "small" + leva-tts together -- the exact production models
    # colab/leva_stt_tts_notebook.py runs -- measured ~3.5GB combined on Colab's
    # T4 GPU. This threshold is set above that measured figure, not at it, to
    # leave headroom for CUDA context overhead, VRAM fragmentation, and whatever
    # else may already be resident on the target GPU (compositor, other
    # processes) by the time this process starts. STT_BACKEND=local_gpu /
    # TTS_BACKEND=local_gpu (forced, not "auto") skip this check entirely --
    # see the "local_gpu" bullets above.
    local_gpu_min_free_vram_gb: float = 4.5

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
