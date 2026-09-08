# Local-GPU STT/TTS Loading: Analysis + Design (target: RTX 5060, 8GB VRAM)

Status: design only, nothing here is implemented yet.
Target deployment machine: RTX 5060, 8GB VRAM (a specific spec given for sizing
decisions -- the detection logic itself is written to be general-purpose and
must not hardcode assumptions about this specific card; it should read
whatever GPU is actually present at runtime).

---

## Part 1 — Current model loading, as it actually exists in this repo

### 1.1 `colab_stt_adapter.py` (full contents)

```python
"""STT adapter that calls a self-hosted Whisper model exposed as an HTTP API
by the Colab notebook at colab/leva_stt_tts_notebook.py (POST /stt), tunneled
via ngrok. Replaces legacy/local_whisper_stt_adapter.py as the default
SpeechToTextPort implementation -- see Settings.stt_backend.

Deliberately does NOT fall back to StubSTTAdapter the way the local adapter
it replaces did. That adapter's silent stub fallback made sense for a local
model that might fail to load once and then always work the same way after;
this is a real network hop to a free Colab/ngrok tunnel that can be slow,
rate-limited, or simply not running (a restarted notebook gets a new ngrok
URL that COLAB_API_BASE_URL hasn't been updated to yet) on any given request.
Silently returning a placeholder transcript in that situation would let a
trainee's real spoken input vanish without any indication something is
wrong. Instead this raises VoiceServiceUnavailableError, which
app/api/error_handlers.py maps to a 502 with a clear `detail` message --
"a clear error rather than hanging or crashing" per this adapter's design
brief, not a silent degrade.
"""

from __future__ import annotations

import logging

import httpx

from app.config import Settings, get_settings
from app.domain.exceptions import VoiceServiceUnavailableError
from app.domain.repositories import SpeechToTextPort

logger = logging.getLogger(__name__)

_SERVICE_NAME = "Colab STT API"


class ColabSTTAdapter(SpeechToTextPort):
    """STT backed by an HTTP call to the Colab notebook's POST /stt endpoint."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    async def transcribe(self, audio_bytes: bytes, filename: str | None = None) -> str:
        base_url = self._settings.colab_api_base_url
        if not base_url:
            raise VoiceServiceUnavailableError(
                _SERVICE_NAME,
                "COLAB_API_BASE_URL is not configured -- set it to the ngrok URL printed by "
                "the Colab notebook (see .env.example).",
            )

        files = {"file": (filename or "audio.wav", audio_bytes, "application/octet-stream")}
        timeout = httpx.Timeout(self._settings.colab_api_timeout_seconds)

        try:
            async with httpx.AsyncClient(
                base_url=base_url, timeout=timeout, headers={"ngrok-skip-browser-warning": "true"}
            ) as client:
                response = await client.post("/stt", files=files)
        except httpx.TimeoutException as exc:
            logger.warning("Colab STT request timed out after %.1fs", self._settings.colab_api_timeout_seconds)
            raise VoiceServiceUnavailableError(
                _SERVICE_NAME, f"request timed out after {self._settings.colab_api_timeout_seconds:.0f}s"
            ) from exc
        except httpx.RequestError as exc:
            logger.warning("Colab STT request failed: %s", exc)
            raise VoiceServiceUnavailableError(_SERVICE_NAME, f"connection failed ({exc})") from exc

        if response.status_code != 200:
            logger.warning("Colab STT returned HTTP %s: %s", response.status_code, response.text[:500])
            raise VoiceServiceUnavailableError(
                _SERVICE_NAME, f"returned HTTP {response.status_code}: {response.text[:200]}"
            )

        try:
            payload = response.json()
            transcript = payload["transcript"]
        except (ValueError, KeyError, TypeError) as exc:
            logger.warning("Colab STT returned an unexpected response body: %s", response.text[:500])
            raise VoiceServiceUnavailableError(
                _SERVICE_NAME, "response did not contain the expected {'transcript': str} body"
            ) from exc

        if not isinstance(transcript, str) or not transcript.strip():
            raise VoiceServiceUnavailableError(_SERVICE_NAME, "returned an empty transcript")

        return transcript
```

**Contract:** `POST {COLAB_API_BASE_URL}/stt`, multipart form field `file` (raw
audio bytes, any container -- whatever the Colab-side Whisper load accepts),
header `ngrok-skip-browser-warning: true`. Response: `200` JSON
`{"transcript": "<str>"}`. Any non-200, non-JSON, missing key, or empty
transcript is treated as a hard failure → `VoiceServiceUnavailableError` → 502
(app/api/error_handlers.py). No retries, no fallback to a stub.

### 1.2 `colab_tts_adapter.py` (full contents)

```python
"""TTS adapter that calls a self-hosted leva-tts model exposed as an HTTP API
by the Colab notebook at colab/leva_stt_tts_notebook.py (POST /tts), tunneled
via ngrok. Replaces legacy/local_tts_adapter.py as the default
TextToSpeechPort implementation -- see Settings.tts_backend.

Returns WAV bytes, same as every other TextToSpeechPort implementation in
this codebase -- the Colab notebook's /tts endpoint is written to return WAV
specifically so app/api/deps.py's get_tts_content_type() can stay a fixed
"audio/wav" constant with no changes needed here.

Deliberately does NOT fall back to StubTTSAdapter -- see colab_stt_adapter.py's
module docstring for the reasoning (identical here): this is a real network
hop that can fail in ways a local model didn't, and silently returning a
silent placeholder clip would hide that failure from the trainee instead of
surfacing it. Raises VoiceServiceUnavailableError instead, mapped to a 502 by
app/api/error_handlers.py.
"""

from __future__ import annotations

import logging

import httpx

from app.config import Settings, get_settings
from app.domain.exceptions import VoiceServiceUnavailableError
from app.domain.repositories import TextToSpeechPort

logger = logging.getLogger(__name__)

_SERVICE_NAME = "Colab TTS API"


class ColabTTSAdapter(TextToSpeechPort):
    """TTS backed by an HTTP call to the Colab notebook's POST /tts endpoint."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    async def synthesize(self, text: str) -> bytes:
        base_url = self._settings.colab_api_base_url
        if not base_url:
            raise VoiceServiceUnavailableError(
                _SERVICE_NAME,
                "COLAB_API_BASE_URL is not configured -- set it to the ngrok URL printed by "
                "the Colab notebook (see .env.example).",
            )

        timeout = httpx.Timeout(self._settings.colab_api_timeout_seconds)

        try:
            async with httpx.AsyncClient(
                base_url=base_url, timeout=timeout, headers={"ngrok-skip-browser-warning": "true"}
            ) as client:
                response = await client.post("/tts", json={"text": text})
        except httpx.TimeoutException as exc:
            logger.warning("Colab TTS request timed out after %.1fs", self._settings.colab_api_timeout_seconds)
            raise VoiceServiceUnavailableError(
                _SERVICE_NAME, f"request timed out after {self._settings.colab_api_timeout_seconds:.0f}s"
            ) from exc
        except httpx.RequestError as exc:
            logger.warning("Colab TTS request failed: %s", exc)
            raise VoiceServiceUnavailableError(_SERVICE_NAME, f"connection failed ({exc})") from exc

        if response.status_code != 200:
            logger.warning("Colab TTS returned HTTP %s: %s", response.status_code, response.text[:500])
            raise VoiceServiceUnavailableError(
                _SERVICE_NAME, f"returned HTTP {response.status_code}: {response.text[:200]}"
            )

        audio_bytes = response.content
        if not audio_bytes:
            raise VoiceServiceUnavailableError(_SERVICE_NAME, "returned an empty audio response")

        return audio_bytes
```

**Contract:** `POST {COLAB_API_BASE_URL}/tts`, JSON body `{"text": "<str>"}`.
Response: `200` with raw WAV bytes as the body (`response.content`, not JSON).
Empty body or non-200 → `VoiceServiceUnavailableError` → 502. No retries, no
stub fallback.

Both adapters share one base URL (`COLAB_API_BASE_URL`) and one timeout
(`COLAB_API_TIMEOUT_SECONDS`, default 30s) — the Colab notebook exposes both
routes from the same ngrok tunnel.

### 1.3 `app/infrastructure/legacy/local_whisper_stt_adapter.py` (full contents)

```python
"""DEPRECATED -- superseded by app/infrastructure/colab_stt_adapter.py (calls
a self-hosted Whisper model over HTTP; see colab/leva_stt_tts_notebook.py).

Kept only for rollback: set STT_BACKEND=whisper to switch app/api/deps.py's
get_speech_to_text_port() back to this adapter. Not otherwise imported by
anything. Do not build new features against this -- ask before deleting it.

Local STT adapter backed by faster-whisper (a CTranslate2 reimplementation
of OpenAI's Whisper).

Runs fully offline on CPU once the "tiny" model weights are cached (~75MB,
downloaded from Hugging Face on first use) -- unlike the TTS side, no network
call happens per transcription request. Decoding arbitrary audio containers
(webm/mp3/wav/...) works without a system ffmpeg install: faster-whisper uses
the `av` package, which bundles its own ffmpeg libraries in the wheel.

Language is forced to Arabic (language="ar") rather than auto-detected,
matching this project's Arabic-only domain, and skipping the extra
language-ID pass for speed.

Failure handling: nothing here ever raises out to the use case. If the model
can't be loaded (no network on first run, out of memory, faster-whisper/deps
missing, ...) or a transcription attempt fails, this falls back to
StubSTTAdapter instead.
"""

from __future__ import annotations

import asyncio
import io
import logging
import threading

from app.domain.repositories import SpeechToTextPort
from app.infrastructure.stub_stt import StubSTTAdapter

logger = logging.getLogger(__name__)

MODEL_SIZE = "tiny"
LANGUAGE = "ar"
BEAM_SIZE = 1

_load_lock = threading.Lock()
_model = None
_load_failed = False


def _ensure_loaded() -> bool:
    """Loads the faster-whisper model once per process. Returns True iff ready to use."""

    global _model, _load_failed

    if _model is not None:
        return True
    if _load_failed:
        return False

    with _load_lock:
        if _model is not None:
            return True
        if _load_failed:
            return False

        try:
            from faster_whisper import WhisperModel

            _model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")
        except Exception:
            logger.exception(
                "Failed to load faster-whisper model %r -- falling back to the stub STT adapter.",
                MODEL_SIZE,
            )
            _load_failed = True
            return False

        return True


def _transcribe_sync(audio_bytes: bytes) -> str | None:
    """Blocking inference call -- always run this via asyncio.to_thread, never awaited directly."""

    if not _ensure_loaded():
        return None

    assert _model is not None
    try:
        segments, _info = _model.transcribe(io.BytesIO(audio_bytes), language=LANGUAGE, beam_size=BEAM_SIZE)
        text = " ".join(segment.text.strip() for segment in segments).strip()
        return text or None
    except Exception:
        logger.exception("faster-whisper transcription failed.")
        return None


class LocalWhisperSTTAdapter(SpeechToTextPort):
    """STT backed by a local faster-whisper "tiny" model (CPU, Arabic-forced)."""

    def __init__(self) -> None:
        self._fallback = StubSTTAdapter()

    async def transcribe(self, audio_bytes: bytes, filename: str | None = None) -> str:
        text = await asyncio.to_thread(_transcribe_sync, audio_bytes)
        if text:
            return text
        return await self._fallback.transcribe(audio_bytes, filename)
```

**Is it wired up?** Yes, but not as the default. `app/api/deps.py`'s
`get_speech_to_text_port()` (lines 299–313) explicitly branches on
`settings.stt_backend`:

```python
def get_speech_to_text_port() -> SpeechToTextPort:
    settings = get_settings()
    if settings.stt_backend == "stub":
        return StubSTTAdapter()
    if settings.stt_backend == "whisper":
        return LocalWhisperSTTAdapter()
    return ColabSTTAdapter(settings)
```

So `LocalWhisperSTTAdapter` is **live, reachable code** — set
`STT_BACKEND=whisper` and it's what serves every voice request — but it is
**not the production default** (`Settings.stt_backend` defaults to `"colab"`)
and its own docstring, `.env.example`, and `config.py` all mark it
"DEPRECATED, kept only for rollback." It is real infrastructure, deliberately
demoted, not dead/unreachable code. It is CPU-only (`device="cpu"`), forces
`"tiny"`, and forces Arabic — none of that is reusable as-is for a GPU-loaded,
larger-model design; it's evidence that faster-whisper is already a proven
dependency in this codebase, not a candidate implementation to extend.

### 1.4 Existing backend-selection pattern for STT/TTS

```
backend\app\api\deps.py:300:    """Picks the SpeechToTextPort implementation via Settings.stt_backend.
backend\app\api\deps.py:309:    if settings.stt_backend == "stub":
backend\app\api\deps.py:311:    if settings.stt_backend == "whisper":
backend\app\config.py:128:    stt_backend: Literal["colab", "whisper", "stub"] = "colab"
backend\app\config.py:142:    tts_backend: Literal["colab", "gtts", "stub"] = "colab"
```

This is the important finding for Part 2: **the exact pattern this task would
otherwise have to invent already exists.**
`Settings.stt_backend: Literal["colab", "whisper", "stub"]` and
`Settings.tts_backend: Literal["colab", "gtts", "stub"]`
(`backend/app/config.py:128,142`), read by `app/api/deps.py`'s
`get_speech_to_text_port()` / `get_text_to_speech_port()`, mirror exactly how
`Settings.evaluation_backend: Literal["stub", "rag_llm"]` picks
`get_evaluation_generator()`'s implementation, and how a now-deleted
`patient_reply_backend` setting used to pick between RAG/Qwen/stub before
Qwen was removed (see `config.py`'s long comment on
`get_patient_reply_generator`). `.env.example` documents `STT_BACKEND` /
`TTS_BACKEND` with the same three-way shape.

**Consequence for Part 2's design:** adding local-GPU loading should extend
this existing `Literal` (e.g. add `"local_gpu"` and/or `"auto"` members) and
add one more branch to each `get_..._port()` function, not invent a parallel
mechanism.

### 1.5 STT/TTS models and libraries actually present in the repo

From `requirements.txt`:

```
faster-whisper==1.0.3      # legacy STT: CTranslate2-based, no system ffmpeg needed (bundled via the `av` wheel)
gtts==2.5.4                # legacy TTS: tiny pure-Python client for Google Translate's TTS endpoint (network
                            # call per request, not offline -- see legacy/local_tts_adapter.py's docstring)
miniaudio==1.71            # legacy TTS: decodes gTTS's MP3 output to raw PCM so legacy/local_tts_adapter.py
                            # can re-encode it as WAV -- ships prebuilt wheels, no system ffmpeg needed
httpx==0.27.2               # used by colab_stt_adapter.py / colab_tts_adapter.py for the Colab API calls
```

- **STT model referenced:** faster-whisper, size **`"tiny"`** (hardcoded in
  `local_whisper_stt_adapter.py`, `MODEL_SIZE = "tiny"`). No `small`/`medium`/
  `large` variant is referenced anywhere in the repo today.
- **TTS engine referenced:** gTTS (Google Translate's free TTS endpoint — a
  network client, not a local model) + `miniaudio` for MP3→WAV re-encoding.
  No local neural TTS engine (Coqui/XTTS/Piper/etc.) is referenced anywhere.
- **`torch`:** *not* in `requirements.txt` — its removal is already committed
  (see `config.py`/comments referring to the deleted Qwen2.5-0.5B-Instruct
  local-LLM path). Not needed by anything currently in `requirements.txt`:
  `faster-whisper`'s runtime is CTranslate2, which has its own CUDA/cuDNN
  bindings independent of PyTorch, and gTTS is a pure network client.
- **"leva-tts"** (the Colab-hosted model name in `colab_tts_adapter.py`'s
  docstring) is not a library in this repo at all — it lives entirely in
  `colab/leva_stt_tts_notebook.py` on the Colab side, opaque to this codebase.

### 1.6 Port/interface definitions (`app/domain/repositories.py`)

```python
class SpeechToTextPort(ABC):
    """Port for transcribing spoken audio into text.

    Takes raw audio bytes rather than a domain entity -- there's no "Audio"
    domain concept here, just an external capability the application layer
    calls into, the same shape as PatientReplyGenerator/EvaluationGenerator.
    """

    @abstractmethod
    async def transcribe(self, audio_bytes: bytes, filename: str | None = None) -> str: ...


class TextToSpeechPort(ABC):
    """Port for synthesizing spoken audio from text.

    Returns raw audio bytes. Every adapter returns WAV -- the gTTS-backed
    adapter re-encodes gTTS's native MP3 output to WAV before returning it
    (see local_tts_adapter.py) -- so app/api/deps.py's get_tts_content_type()
    is a fixed "audio/wav" rather than something resolved per-adapter, even
    though the port signature itself carries no format metadata.
    """

    @abstractmethod
    async def synthesize(self, text: str) -> bytes: ...
```

Two abstract methods total, both `async`, both taking/returning plain
`bytes`/`str` — no entity coupling. This is the entire contract any new
local-GPU adapter has to satisfy (§2.5 below).

### 1.7 Call path — where GPU loading would plug in

**STT:**
- `POST /transcribe` → `app/api/routers/voice.py:transcribe()` →
  `TranscribeAudioUseCase.execute()` (`app/application/use_cases/transcribe_audio.py`,
  a thin 5-line pass-through, no DB) → `SpeechToTextPort.transcribe()`.
- `POST /sessions/{id}/chat-voice` and `WS /ws/voice` → both resolve
  `get_process_voice_chat_use_case` → `ProcessVoiceChatAsyncUseCase.execute()`
  (`app/application/use_cases/process_voice_chat_async.py`), whose **first
  line** is:
  ```python
  transcribed_text = await self._speech_to_text.transcribe(audio_bytes, filename)
  ```

**TTS:**
- Only reached from the voice path, never from plain `POST /messages`.
  `get_process_voice_chat_use_case` composes
  `get_post_message_use_case_with_tts`, which passes a `TextToSpeechPort`
  into `PostMessageAsyncUseCase`. Inside `PostMessageAsyncUseCase._generate_and_push`
  (`app/application/use_cases/post_message_async.py:212-218`), *after* the
  patient reply text is finalized:
  ```python
  if not failed and self._text_to_speech is not None:
      try:
          audio = await self._text_to_speech.synthesize(final.content)
      except Exception:
          logger.warning(...)
      else:
          await self._push_port.push_bytes(session_id, audio)
  ```

**Synchronous vs. async — explicit finding, flagged as a design question for
the next task, not solved here:**
- **STT is synchronous, inline, in the request path.** `ProcessVoiceChatAsyncUseCase.execute()`
  directly `await`s `transcribe()` before doing anything else; the router
  awaits the whole use case before responding. The module docstring for
  `ProcessVoiceChatAsyncUseCase` says this outright: *"STT still runs
  synchronously (fast; not the latency problem this exists for)"*. A
  local-GPU STT model that's slower than the current Colab round-trip (cold
  start, VRAM contention, a bigger model than Colab's) would sit directly in
  this synchronous path and add latency to every voice request's initial
  HTTP/WS response — this is not a background task today.
- **TTS already runs inside the async/background flow.** It's called from
  `_generate_and_push`, the coroutine `SessionConcurrencyGuard.start()`
  schedules in the background (`post_message_async.py:149`) — it never blocks
  the request/response cycle; a slow local TTS model only delays the
  WS push, not any HTTP response.
- **Net:** loading TTS locally is latency-safe by the existing architecture;
  loading STT locally is not automatically so — a local STT model slower than
  Colab's would need moving into the async/background flow (mirroring how TTS
  and patient-reply generation already work) to avoid regressing the
  synchronous request path. Flagged here, not addressed by this design.

---

## Part 2 — Design: local-GPU loading for an 8GB RTX 5060, Colab fallback

Everything below is designed against the **8GB RTX 5060 target spec given for
this task**, not against whatever machine this analysis happens to run on.
The detection logic itself (§2.2) reads whatever GPU is actually present at
runtime and is not hardcoded to this card's numbers — the 8GB figure is used
only to pick *candidate models and thresholds* that make sense for that
target, and those thresholds are ordinary config values, not assumptions
baked into the detection code path.

### 2.1 Model selection for an 8GB budget (STT + TTS share the card)

**STT candidates — faster-whisper (already a proven dependency in this repo,
§1.5), CTranslate2-backed, GPU via `device="cuda"`:**

| Model | Compute type | Approx. VRAM | Source |
|---|---|---|---|
| `small` | `float16` | ~2GB | **Known figure** — matches OpenAI's own Whisper VRAM table (`small` ≈ 2GB), and CTranslate2 is not larger than stock PyTorch Whisper at fp16. |
| `small` | `int8_float16` | ~1–1.5GB | **Estimated** — CTranslate2's published benchmarks show int8 quantization typically halves fp16 memory; not a number I've directly verified for this exact model size. |
| `medium` | `float16` | ~4–5GB | **Estimated, extrapolated from a known figure** — OpenAI's Whisper VRAM table lists stock `medium` at ≈5GB; CTranslate2's own README benchmark (on `large-v2`) shows its fp16 path running measurably lower than stock PyTorch Whisper, so ~4–5GB is a reasonable estimate, not a number I have a direct citation for at `medium` specifically. |
| `medium` | `int8_float16` | ~2–3GB | **Estimated** — same halving logic as `small` int8 above. |

**TTS candidates — Arabic support is a hard requirement here (per this
project's Arabic-only clinical domain), which sharply narrows real options:**

| Engine | Approx. VRAM | Arabic support | Source |
|---|---|---|---|
| Coqui **XTTS-v2** | ~4–5GB | **Yes** — one of XTTS-v2's 17 built-in languages, natively supported, no fine-tuning needed. Also does zero-shot voice cloning. | **Known figure** — widely documented in Coqui's own model card/community reports as needing ~4GB minimum, more comfortable at 6GB+; I'm treating ~4–5GB as the realistic single-inference (not batched) figure. |
| **Piper** (ONNX, via `onnxruntime-gpu`) | ~0.3–0.5GB | Partial — community-trained Arabic voices exist (e.g. `ar_JO`) but are not officially maintained, and quality/naturalness is noticeably below XTTS-v2. Piper is also designed to be fast enough on **CPU** that GPU offload buys little. | **Estimated**, from Piper's small ONNX model files (tens of MB) and typical ONNX Arabic-similar-size TTS runtime footprints — not a number I have a hard citation for. |
| Generic Coqui **VITS**-style single-speaker model | ~1.5–2GB | Depends entirely on which checkpoint — Coqui's official model zoo has no first-party Arabic VITS model; would mean sourcing/training a community checkpoint. | **Estimated**, typical for this model family's size class. |

**Recommendation: faster-whisper `small`, `float16` (STT, ~2GB) + Coqui
XTTS-v2 (TTS, ~4–5GB).**

- **Total: ~6–7GB of 8GB**, leaving **~1–2GB headroom** for the OS/display
  compositor, CUDA context overhead (typically several hundred MB just for
  the driver/runtime itself), and any concurrent request batching — not
  maxing out the card, as asked.
- **Why XTTS-v2 over Piper for TTS despite the larger footprint:** Arabic
  support is a hard requirement, and Piper's Arabic voices are
  community-sourced and lower quality — a real regression from what the
  Colab-hosted "leva-tts" model presumably already delivers for training
  scenarios where the simulated patient's voice matters to the trainee
  experience. XTTS-v2 is the only candidate here with genuine, natively
  supported, production-quality Arabic synthesis.
- **Why `small` over `medium` for STT:** `medium` int8 (~2–3GB) would still
  technically fit alongside XTTS-v2, but stacks two "estimated, not verified"
  numbers on top of each other with very little headroom left to absorb
  either estimate being wrong. `small` at fp16 is the STT tier with the
  *most confidently known* VRAM figure (§ table above) — trading a modest
  amount of STT accuracy for a design that leaves real slack for the one
  number (XTTS-v2's footprint) that's already the tightest fit and least
  precisely known.
- **Fallback pairing if headroom needs to be larger, or XTTS-v2's real
  measured footprint on the actual card comes in high:** `small`/`int8_float16`
  STT (~1–1.5GB) + Piper Arabic voice (~0.3–0.5GB) ≈ 2GB total, enormous
  headroom, but a real quality regression on the TTS side — a fallback of
  last resort, not the primary recommendation.

### 2.2 VRAM detection (general-purpose, not hardcoded to any one card)

Two `torch.cuda` calls, used together, not either alone:

```python
import torch

if not torch.cuda.is_available():
    # no CUDA-capable GPU visible to this process at all -- decide "colab" and stop here.
    ...

device = torch.cuda.current_device()
total_bytes = torch.cuda.get_device_properties(device).total_memory
free_bytes, _total_bytes_again = torch.cuda.mem_get_info(device)
```

- `get_device_properties(0).total_memory` — the card's *installed* VRAM
  (8GB on the target RTX 5060, but this code never hardcodes that number —
  it's read from the device at runtime, so the exact same code runs correctly
  on a 4GB card, a 24GB card, or no card at all).
- `torch.cuda.mem_get_info()` — **currently free** VRAM, i.e. installed minus
  whatever the OS/driver/other processes have already claimed. **This is the
  number the "auto" decision must threshold against, not `total_memory`** —
  a technically-8GB card already running a display compositor, another CUDA
  process, or a previous unclean shutdown that left VRAM pinned is not
  actually offering 8GB to this server. Using `total_memory` alone would
  green-light a local load that then fails or OOMs against models sized for
  what the card reports on its box, not what's actually available right now.
  This distinction matters generally (any shared or already-loaded GPU), not
  just for this specific target card.
- Both calls happen **once, at process startup** (§2.4) — not per-request.
  VRAM can still shift after that point (§2.4's partial-failure case covers
  this), but re-checking on every `/transcribe` call would add latency to
  the very request path §1.7 already flagged as synchronous, for a value
  that's meant to be decided once for the process's lifetime anyway.

**Dependency tradeoff — `torch` must come back into `requirements.txt`:**
`torch` was deliberately removed from `requirements.txt` when the local Qwen
patient-reply path was deleted (§1.5) — re-adding it is not free. Two
sub-questions:

1. **Is a full `torch` reinstall the right way to get VRAM detection alone?**
   No — if VRAM detection were the *only* reason to add a dependency, a
   `subprocess` call to `nvidia-smi --query-gpu=memory.total,memory.free
   --format=csv,noheader,nounits` (already installed with any NVIDIA driver,
   zero extra pip weight) would be the lighter choice.
2. **But it isn't the only reason here.** The recommended TTS engine
   (Coqui XTTS-v2, §2.1) **is itself built on PyTorch** and pulls in a CUDA
   build of `torch` as a hard dependency regardless of what detection method
   is chosen. Given that, using `torch.cuda` for detection is the design that
   adds *zero* net new dependencies beyond what XTTS-v2 already requires,
   versus adding both `torch` (for XTTS-v2) *and* a separate `subprocess`/
   `nvidia-smi`-parsing code path (for detection) that duplicates information
   `torch.cuda` already exposes. **Recommendation: `torch.cuda`, reusing the
   dependency XTTS-v2 needs anyway** — the `nvidia-smi` subprocess approach
   is worth keeping in mind only if a future TTS choice drops the PyTorch
   dependency entirely, at which point paying for `torch` just for detection
   would no longer be justified.

### 2.3 Backend selection pattern

Extend the existing `Literal`s in `app/config.py` (§1.4) rather than
introducing a parallel mechanism:

```python
stt_backend: Literal["colab", "whisper", "stub", "local_gpu", "auto"] = "colab"
tts_backend: Literal["colab", "gtts", "stub", "local_gpu", "auto"] = "colab"
```

- `"colab"` — current default, unchanged.
- `"local_gpu"` — force local GPU loading; if VRAM turns out insufficient or
  loading fails, this should still be an explicit operator choice (§2.4
  covers what happens if it fails, but choosing it isn't itself gated on
  detection the way `"auto"` is).
- `"auto"` — detect once at startup (§2.2), choose `"local_gpu"` behavior if
  the combined STT+TTS footprint fits within *free* VRAM with the safety
  margin below, else behave exactly like `"colab"`.
- `"whisper"` / `"gtts"` / `"stub"` — untouched, kept for the same rollback/
  test-fixture purposes they already serve.

**`"auto"` logic, decided once at startup, logged clearly with the actual
numbers observed (not just a boolean):**

```
"detected 7.4GB free VRAM (8.0GB total) -- STT(faster-whisper/small/fp16, ~2.0GB)
 + TTS(XTTS-v2, ~5.0GB) = ~7.0GB needed, 0.4GB margin < 1.0GB safety threshold
 -- falling back to Colab STT/TTS."
```
vs., on a card with more headroom:
```
"detected 7.4GB free VRAM (8.0GB total) -- STT+TTS need ~7.0GB, margin 0.4GB
 -- below the 1.0GB safety threshold, falling back to Colab."
```
vs. no GPU at all:
```
"no CUDA-capable GPU detected (torch.cuda.is_available() == False) --
 falling back to Colab STT/TTS."
```

A concrete safety-margin setting, e.g. `LOCAL_GPU_VRAM_SAFETY_MARGIN_GB: float
= 1.0`, keeps `"auto"` from wedging itself right up against the free-VRAM
ceiling — deliberately not maxing out the card, same principle as §2.1's
pairing choice. This decision is computed once in the startup hook (§2.4) and
cached for the process's lifetime — mirrors how `Settings` itself is already
a singleton via `@lru_cache get_settings()` (`app/config.py:199-201`).

### 2.4 Model loading lifecycle

**Where:** `app/main.py`'s existing `lifespan` context manager
(`app/main.py:17-21`) is the only startup-time resource hook this project
already has:

```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    db_engine.init_engine()
    yield
    await db_engine.dispose_engine()
```

The proposed shape follows this exact pattern — an `init_.../dispose_...`
pair called from `lifespan`, not a new mechanism:

```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    db_engine.init_engine()
    voice_models.init_voice_backend()   # new -- runs the auto-detect decision (§2.3) once,
                                         # loads local STT/TTS models into module-level
                                         # singletons if "local_gpu"/"auto"-chose-local,
                                         # no-ops immediately if the resolved backend is "colab"
    yield
    await db_engine.dispose_engine()
    voice_models.dispose_voice_backend()  # release GPU memory explicitly on shutdown
```

`get_speech_to_text_port()` / `get_text_to_speech_port()`
(`app/api/deps.py:299-327`) then read the *already-resolved* backend decision
made at startup (a module-level singleton, the same shape
`inflight_sessions.get_singleton()` / `ws_hub.get_singleton()` already use in
this same file for other process-wide state) rather than re-running detection
per request — consistent with §2.2's "once, not per-request" requirement.

**Partial-failure case — VRAM free at startup-check time, but claimed by
something else before the model finishes loading (or any other local-load
failure: driver issue, missing CUDA libs, OOM mid-`from_pretrained`):**

- The failure must be caught **inside `init_voice_backend()`**, at the point
  of the actual `WhisperModel(..., device="cuda", ...)` /
  `Xtts.load_checkpoint(...)`-style call — not left to surface later inside a
  request.
- On any exception there: log it clearly (what was attempted, what failed),
  and **fall back to constructing the Colab adapters instead** for that
  process's lifetime — exactly the same object `get_speech_to_text_port()` /
  `get_text_to_speech_port()` already return today when `stt_backend`/
  `tts_backend == "colab"`. No crash, no partial state (e.g. STT loaded but
  TTS failed should fall each port back to Colab independently, not force
  both to Colab just because one failed) — each port resolves its own
  outcome.
- This fallback belongs in the *startup* hook, not per-request retry logic:
  once the process has decided (and logged) "local GPU it is," every request
  for that process's lifetime should use exactly that decision — a
  request-time fallback-and-retry would reintroduce the per-request VRAM
  check §2.2 explicitly avoids, and would make the "auto" decision silently
  inconsistent across requests within the same process.

### 2.5 Interface compatibility

Confirmed in §1.6: `SpeechToTextPort.transcribe(audio_bytes, filename=None) ->
str` and `TextToSpeechPort.synthesize(text) -> bytes` are the entire
contract. A `LocalGpuSTTAdapter(SpeechToTextPort)` /
`LocalGpuTTSAdapter(TextToSpeechPort)` pair implementing exactly these two
methods (backed by the module-level singleton models `init_voice_backend()`
loaded, run via `asyncio.to_thread` the same way
`local_whisper_stt_adapter.py` already does for its own blocking
`_transcribe_sync` call) is a **swappable backend**, not a rewrite of any
caller: `TranscribeAudioUseCase`, `ProcessVoiceChatAsyncUseCase`, and
`PostMessageAsyncUseCase` all depend only on the abstract ports (§1.7), never
on `ColabSTTAdapter`/`ColabTTSAdapter` directly — the entire wiring change is
confined to `app/api/deps.py`'s two `get_..._port()` functions, exactly as
the `PATIENT_REPLY_BACKEND`/`EVALUATION_BACKEND` precedent already
establishes for this codebase.

### 2.6 Requirements/deployment notes

**New pip packages needed** (none of this is installed in the repo's current
`requirements.txt`, per §1.5):

```
torch==2.5.1+cu121            # exact CUDA build/index depends on the RTX 5060 machine's
                               # installed driver/CUDA version -- verify on that machine (§ checklist)
                               # rather than assuming cu121 is correct sight-unseen
faster-whisper==1.0.3          # already in requirements.txt (legacy CPU path) -- same pin, now
                               # also loaded with device="cuda" for the local-GPU adapter
TTS==0.22.0                    # Coqui TTS package (ships XTTS-v2); pin is my best estimate of a
                               # recent stable release, NOT independently verified against PyPI here
```

Also required: CTranslate2's CUDA runtime libraries (`nvidia-cublas-cu12`,
`nvidia-cudnn-cu12`) — faster-whisper GPU mode needs these installed
separately from whatever CUDA libs `torch` itself bundles; a known, common
faster-whisper GPU deployment gotcha (`libcudnn_ops_infer.so`-not-found-style
errors) worth calling out explicitly rather than assuming `torch`'s presence
alone satisfies it.

**This design targets a machine this analysis was explicitly told not to
query — everything below needs real verification on the actual RTX 5060
box before going live:**

- The exact CUDA/driver version installed there, to pin the correct `torch`
  wheel (`cu121` above is a placeholder, not a verified value).
- **Every VRAM figure in §2.1's tables** — several are marked "estimated,"
  and even the "known" ones are for the base model family, not this exact
  quantization/version combination. Real `torch.cuda.mem_get_info()` readings
  before/after each model load on that machine are the only trustworthy
  numbers.
- Whether XTTS-v2's Arabic output quality, on real trainee-facing scenario
  text, is actually an acceptable substitute for the current Colab-hosted
  "leva-tts" model — a subjective/product judgment this design can't make
  from VRAM math alone.
- Whether faster-whisper's CTranslate2 CUDA libs and `torch`'s own bundled
  CUDA libs coexist cleanly in one venv on that machine (the cuDNN-version
  mismatch class of issue flagged above) — this is a real, commonly reported
  friction point, not a hypothetical.

---

## What to implement and verify on the actual RTX 5060 machine, by priority

1. **(Verify first, before writing any adapter code)** On the real machine:
   install `torch` with the CUDA build matching its actual driver, then run
   `torch.cuda.is_available()` and `torch.cuda.mem_get_info()` to confirm the
   detection primitives in §2.2 report sane numbers at all, before anything
   else depends on them.
2. **(Verify)** Load faster-whisper `small` at `device="cuda",
   compute_type="float16"` on that machine and read `mem_get_info()`
   before/after to get a real VRAM number, replacing §2.1's estimate.
3. **(Verify)** Load Coqui XTTS-v2 the same way and do the same measurement —
   this is the least-certain number in the whole design and the one the 8GB
   budget is tightest against.
4. **(Verify)** Confirm faster-whisper's CUDA/cuDNN libs and `torch`'s own
   don't conflict in one venv on that machine (§2.6's known gotcha) — resolve
   before writing the startup-loading code, not after it fails in the field.
5. **(Implement)** `Settings.stt_backend`/`tts_backend` `Literal` extension +
   `LOCAL_GPU_VRAM_SAFETY_MARGIN_GB` setting (§2.3), following the exact
   existing pattern in `config.py`/`.env.example`.
6. **(Implement)** `app/infrastructure/voice_models.py` (or similar) —
   `init_voice_backend()`/`dispose_voice_backend()`, the auto-detect decision
   + clear logging (§2.3), wired into `app/main.py`'s `lifespan` (§2.4).
7. **(Implement)** `LocalGpuSTTAdapter` / `LocalGpuTTSAdapter` implementing
   `SpeechToTextPort`/`TextToSpeechPort` (§2.5), backed by the singletons
   `init_voice_backend()` loaded.
8. **(Implement)** Wire the two new branches into `get_speech_to_text_port()`/
   `get_text_to_speech_port()` in `app/api/deps.py`.
9. **(Decide, separate follow-up task per §1.7)** Whether local STT needs to
   move out of `ProcessVoiceChatAsyncUseCase`'s synchronous inline call into
   the async/background flow the way TTS and patient-reply generation
   already work — depends on step 2's real measured latency for `small` on
   that GPU, not on VRAM fit alone.
