# %% [markdown]
# # Leva STT/TTS server (Colab)
#
# Paste each `# %%` cell below into its own cell in a real Colab notebook, in
# order. Runs OpenAI Whisper (STT) and leva-tts (TTS, Levantine Arabic +
# English) behind one FastAPI app, tunneled publicly via ngrok, so the VR
# simulator backend (see `app/infrastructure/colab_stt_adapter.py` and
# `colab_tts_adapter.py`) can call it as `COLAB_API_BASE_URL`.
#
# **Runtime:** Runtime -> Change runtime type -> GPU (T4 is enough for both
# models). CPU also works, just slower for TTS.
#
# **Every time you restart this notebook you get a new ngrok URL** (unless
# you're on a paid ngrok plan with a reserved domain) -- copy the URL printed
# at the end of CELL 4 into the backend's `.env` as `COLAB_API_BASE_URL`, no
# code changes needed there.
#
# Endpoints exposed:
#   - `GET  /health` -> `{"status": "ok", ...}`
#   - `POST /stt`     multipart file upload -> `{"transcript": "..."}`
#   - `POST /tts`     `{"text": "..."}` JSON -> `audio/wav` bytes

# %% [markdown]
# ## CELL 1 -- installs
#
# System packages leva-tts needs (espeak-ng, ffmpeg, libsndfile1), then the
# Python packages. Installing PyTorch *before* `leva-tts`, matching the
# model's own install instructions, avoids it pulling in an incompatible
# torch build as a transitive dependency.
#
# Colab already ships a recent CUDA-enabled torch build that is normally
# compatible enough -- the pinned `torch`/`torchaudio` reinstall lines are
# commented out below and only needed if importing `leva_tts` later in CELL 2
# fails with a torch/CUDA version error. Uncomment them in that case (it
# takes a few minutes and needs a runtime restart to take effect).

# %%
# !apt-get -qq update && apt-get -qq install -y espeak-ng ffmpeg libsndfile1

# Uncomment only if CELL 2's `import leva_tts` fails with a torch/CUDA
# mismatch -- see https://github.com/MohammedAly22/Leva-TTS for why this pin
# exists (leva-tts requires torch>=2.3,<2.9).
# !pip install -q torch==2.3.0 torchaudio==2.3.0 --index-url https://download.pytorch.org/whl/cu121

# !pip install -q -U openai-whisper
# !pip install -q leva-tts
# !pip install -q fastapi "uvicorn[standard]" python-multipart soundfile pyngrok nest-asyncio

print("Installs done. If you uncommented the torch reinstall line above, restart the runtime now"
      " (Runtime -> Restart runtime) before running CELL 2.")

# %% [markdown]
# ## CELL 2 -- model loading
#
# Loads both models once. `WHISPER_MODEL_SIZE` and `LEVA_DEFAULT_SPEAKER` are
# the two knobs you're most likely to want to change; they're pulled out as
# constants at the top for that reason.
#
# - Whisper sizes (speed/accuracy tradeoff, smallest to largest):
#   `tiny`, `base`, `small`, `medium`, `large-v3`. `small` is a reasonable
#   default on a T4 GPU; drop to `base` or `tiny` if you need lower latency
#   over accuracy, or don't have a GPU.
# - `LEVA_LANGUAGE` forces Arabic transcription in Whisper (matching this
#   project's Arabic-only domain, same choice the backend's own legacy
#   faster-whisper adapter makes) -- set to `None` for auto-detection instead.

# %%
import torch

WHISPER_MODEL_SIZE = "small"  # tiny | base | small | medium | large-v3
WHISPER_LANGUAGE = "ar"  # None to auto-detect instead of forcing Arabic

LEVA_DEFAULT_SPEAKER = "Amina"  # any name from leva_tts.SPEAKERS (printed below)

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
print(f"Using device: {DEVICE}")

# --- Whisper (STT) ---
import whisper  # noqa: E402  (openai-whisper package, imports as `whisper`)

print(f"Loading Whisper model {WHISPER_MODEL_SIZE!r} ...")
whisper_model = whisper.load_model(WHISPER_MODEL_SIZE, device=DEVICE)
print("Whisper loaded.")

# --- leva-tts (TTS) ---
from leva_tts import LevaTTS, SPEAKERS  # noqa: E402

print(f"Available leva-tts speakers: {SPEAKERS}")
assert LEVA_DEFAULT_SPEAKER in SPEAKERS, f"{LEVA_DEFAULT_SPEAKER!r} not in {SPEAKERS!r}"

print("Loading leva-tts model (downloads weights from HuggingFace on first run, ~1-2 min) ...")
leva_tts_engine = LevaTTS(device=DEVICE, preprocess_text=True, verbose=False)
print("leva-tts loaded.")

# %% [markdown]
# ## CELL 3 -- FastAPI app + endpoints
#
# `/stt`: multipart file upload -> `{"transcript": "..."}`. The uploaded
# bytes are written to a temp file because Whisper's `transcribe()` decodes
# audio via ffmpeg from a file path -- it doesn't take raw bytes directly.
#
# `/tts`: `{"text": "...", "speaker": "..."}` (speaker optional, defaults to
# `LEVA_DEFAULT_SPEAKER`) -> raw `audio/wav` bytes. leva-tts returns
# `(numpy.ndarray float32, sample_rate)`; that's encoded to a WAV container
# with `soundfile` before being sent back, so the backend's
# `colab_tts_adapter.py` gets a ready-to-play WAV file, not a bare PCM array.
#
# Every endpoint validates its input and turns failures into a proper
# `HTTPException` (clean JSON `{"detail": ...}`, matching status code) instead
# of letting an unhandled exception fall through as a raw 500 -- see the
# backend's own `VoiceServiceUnavailableError` handling, which expects a
# non-2xx response with a readable body, not a hang or a stack trace.

# %%
import io
import tempfile
import traceback
from pathlib import Path

import soundfile as sf
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

app = FastAPI(title="Leva STT/TTS server")


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "device": DEVICE,
        "whisper_model": WHISPER_MODEL_SIZE,
        "leva_tts_speakers": SPEAKERS,
    }


@app.post("/stt")
async def stt(file: UploadFile = File(...)) -> dict:
    audio_bytes = await file.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="uploaded audio file is empty")

    suffix = Path(file.filename).suffix or ".wav"
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp_file:
            tmp_file.write(audio_bytes)
            tmp_path = tmp_file.name

        result = whisper_model.transcribe(tmp_path, language=WHISPER_LANGUAGE)
        transcript = (result.get("text") or "").strip()
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"transcription failed: {exc}") from exc
    finally:
        if tmp_path is not None:
            Path(tmp_path).unlink(missing_ok=True)

    if not transcript:
        raise HTTPException(status_code=422, detail="transcription produced no text (silent or unrecognizable audio)")

    return {"transcript": transcript}


class TTSRequest(BaseModel):
    text: str
    speaker: str | None = None


@app.post("/tts")
def tts(payload: TTSRequest) -> Response:
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="'text' must not be empty")

    speaker = payload.speaker or LEVA_DEFAULT_SPEAKER
    if speaker not in SPEAKERS:
        raise HTTPException(status_code=400, detail=f"unknown speaker {speaker!r}; choose one of {SPEAKERS}")

    try:
        wav, sample_rate = leva_tts_engine.synthesize(text, speaker=speaker)
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"synthesis failed: {exc}") from exc

    buffer = io.BytesIO()
    sf.write(buffer, wav, sample_rate, format="WAV", subtype="PCM_16")
    return Response(content=buffer.getvalue(), media_type="audio/wav")

# %% [markdown]
# ## CELL 4 -- ngrok tunnel + run server
#
# Runs uvicorn in a background thread (so this cell returns immediately
# instead of blocking the notebook) and opens an ngrok tunnel to it.
#
# ngrok requires a free account + auth token as of 2023: sign up at
# https://dashboard.ngrok.com/signup, then grab your token from
# https://dashboard.ngrok.com/get-started/your-authtoken. This cell first
# tries to read it from a Colab secret named `NGROK_AUTHTOKEN` (Colab's key
# icon in the left sidebar -> Secrets -> add `NGROK_AUTHTOKEN`, recommended
# so it isn't retyped/pasted in plaintext every run); if that secret isn't
# set, it falls back to an interactive password prompt.
#
# **Copy the "Public URL" printed at the bottom into the backend's `.env` as
# `COLAB_API_BASE_URL` (no trailing slash).**

# %%
import threading
import time

import uvicorn
from pyngrok import ngrok

try:
    from google.colab import userdata  # type: ignore

    NGROK_AUTHTOKEN = userdata.get("NGROK_AUTHTOKEN")
except Exception:
    NGROK_AUTHTOKEN = None

if not NGROK_AUTHTOKEN:
    import getpass

    NGROK_AUTHTOKEN = getpass.getpass(
        "Paste your ngrok authtoken (from https://dashboard.ngrok.com/get-started/your-authtoken): "
    )

ngrok.set_auth_token(NGROK_AUTHTOKEN)

PORT = 8000


def _run_server() -> None:
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")


server_thread = threading.Thread(target=_run_server, daemon=True)
server_thread.start()
time.sleep(3)  # give uvicorn a moment to bind before opening the tunnel

# Close any tunnels left open from a previous run of this cell before opening a new one.
ngrok.kill()
public_tunnel = ngrok.connect(PORT, "http")

print("=" * 70)
print(f"Public URL: {public_tunnel.public_url}")
print("=" * 70)
print(f"Set COLAB_API_BASE_URL={public_tunnel.public_url} in the backend's .env, then restart it.")
print(f"Health check: {public_tunnel.public_url}/health")
