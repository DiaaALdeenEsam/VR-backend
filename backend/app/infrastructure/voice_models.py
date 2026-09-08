"""Startup-time local-GPU STT/TTS model loading + VRAM-aware backend selection.

Called once from app/main.py's lifespan (mirrors
app/infrastructure/db/engine.py's init_engine()/dispose_engine() pair) --
NOT per-request. app/api/deps.py's get_speech_to_text_port()/
get_text_to_speech_port() read the decision made here once at startup for
the rest of the process's lifetime; nothing in this module is re-evaluated
per HTTP request.

Backs Settings.stt_backend/tts_backend's "local_gpu" and "auto" values (see
app/config.py) -- "colab"/"whisper"/"stub" never touch this module at all;
app/api/deps.py's existing branches for those are unchanged.

Model choice: openai-whisper "small" (STT) + leva-tts (TTS) -- the exact
production models colab/leva_stt_tts_notebook.py runs (see that file's CELL
2 for the WHISPER_MODEL_SIZE/WHISPER_LANGUAGE/LEVA_DEFAULT_SPEAKER constants
app/infrastructure/local_gpu_stt_adapter.py and local_gpu_tts_adapter.py
mirror exactly), not an earlier design doc's alternative. An earlier design
pass (docs/stt-tts-local-gpu-design.md) proposed Coqui XTTS-v2 for TTS
before the real Colab notebook's actual model choice and its measured
~3.5GB combined VRAM footprint (T4) were confirmed as ground truth -- that
proposal is explicitly superseded now that matching the real production
models exactly (so the local and Colab paths stay truly interchangeable,
not a redesigned alternative) is possible.

"auto" vs "local_gpu" (forced): both load the exact same models the exact
same way if they load at all -- the only difference is whether the free-VRAM
threshold (Settings.local_gpu_min_free_vram_gb) is treated as a hard
pre-flight gate. "auto": below threshold -> don't even attempt, go straight
to Colab. "local_gpu": an operator explicitly forcing this is accepting a
tighter-than-recommended fit as their own call to make, so the threshold is
skipped -- but a real GPU still has to be present, and the try/except around
the actual model load still falls back to Colab automatically (never
crashes the server) if that bet doesn't pay off, exactly like "auto"'s
own load-failure handling.

STT and TTS are decided/loaded independently of each other, not as one
all-or-nothing choice: a machine with, say, ffmpeg installed but espeak-ng
missing (leva-tts needs both; see colab/leva_stt_tts_notebook.py CELL 1)
should still get local STT with only TTS falling back to Colab -- not both
falling back just because one of the two failed.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from typing import Any, Literal

from app.config import Settings
from app.infrastructure.local_gpu_stt_adapter import (
    WHISPER_LANGUAGE,
    WHISPER_MODEL_SIZE,
    LocalGpuSTTAdapter,
)
from app.infrastructure.local_gpu_tts_adapter import DEFAULT_SPEAKER, LocalGpuTTSAdapter

logger = logging.getLogger(__name__)

# System binaries leva-tts needs per colab/leva_stt_tts_notebook.py CELL 1's install
# comment (espeak-ng, ffmpeg, libsndfile1). libsndfile1 is deliberately not checked
# here via a binary lookup -- it's a shared library, not an executable, and the
# `soundfile` wheel bundles it on the platforms this project ships to; if it were
# genuinely missing, the try/except around the actual load below (importing
# soundfile/leva_tts, or the first real synthesize() call) catches that exactly like
# any other load failure -- no separate check needed for it specifically.
_REQUIRED_TTS_BINARIES = ("espeak-ng", "ffmpeg")

_GiB = 1024**3


@dataclass(frozen=True)
class BackendDecision:
    """What voice_models.init_voice_backend() decided for one port (STT or TTS),
    and why -- logged at startup and available for diagnostics/tests."""

    backend: Literal["local_gpu", "colab"]
    reason: str


_stt_decision: BackendDecision | None = None
_tts_decision: BackendDecision | None = None
_stt_adapter: LocalGpuSTTAdapter | None = None
_tts_adapter: LocalGpuTTSAdapter | None = None
_whisper_model: Any = None
_leva_engine: Any = None


def detect_vram_gb() -> tuple[bool, float, float]:
    """(gpu_available, free_gb, total_gb) -- (False, 0.0, 0.0) if torch isn't
    installed or no CUDA-capable GPU is visible to this process. Both of those
    are ordinary "no local GPU backend here" outcomes, not errors, so this
    never raises; any unexpected exception from torch itself is caught and
    also treated as "no GPU available" rather than propagating.

    Uses torch.cuda.mem_get_info() for the free figure (currently-free VRAM),
    not just torch.cuda.get_device_properties(0).total_memory (installed
    capacity) -- a card's *total* VRAM says nothing about what's actually
    available to this process right now if the OS compositor, another CUDA
    process, or a previous unclean shutdown has already claimed part of it.
    Thresholding against "free" avoids green-lighting a local load sized for
    what the card reports on the box rather than what's actually offered.
    """

    try:
        import torch
    except ImportError:
        return False, 0.0, 0.0

    try:
        if not torch.cuda.is_available():
            return False, 0.0, 0.0
        free_bytes, total_bytes = torch.cuda.mem_get_info(0)
    except Exception:
        logger.exception("torch.cuda VRAM detection raised unexpectedly -- treating as no GPU available.")
        return False, 0.0, 0.0

    return True, free_bytes / _GiB, total_bytes / _GiB


def _missing_tts_binaries() -> list[str]:
    return [name for name in _REQUIRED_TTS_BINARIES if shutil.which(name) is None]


def _load_whisper() -> Any:
    import whisper

    return whisper.load_model(WHISPER_MODEL_SIZE, device="cuda")


def _load_leva_tts() -> tuple[Any, list[str]]:
    from leva_tts import SPEAKERS, LevaTTS

    engine = LevaTTS(device="cuda", preprocess_text=True, verbose=False)
    return engine, list(SPEAKERS)


def _gate(port_backend: str, gpu_available: bool, vram_ok: bool, vram_reason: str) -> tuple[bool, str]:
    """Whether to attempt a local load for one port ("local_gpu" or "auto"), and why/why not."""

    if not gpu_available:
        return False, vram_reason
    if port_backend == "auto":
        return vram_ok, vram_reason
    # port_backend == "local_gpu": forced -- skip the VRAM threshold gate. A real GPU
    # being present is enough to attempt; the caller's try/except around the actual
    # load still falls back to Colab safely if this doesn't pan out.
    return True, vram_reason


def init_voice_backend(settings: Settings) -> None:
    """Runs once from app/main.py's lifespan startup. Decides + loads local models
    for whichever of stt_backend/tts_backend is "local_gpu"/"auto"; leaves
    app/api/deps.py's existing colab/whisper/stub branches completely untouched
    for any other value (this function returns immediately without even
    checking for a GPU in that case)."""

    global _stt_decision, _tts_decision, _stt_adapter, _tts_adapter, _whisper_model, _leva_engine

    wants_stt = settings.stt_backend in ("local_gpu", "auto")
    wants_tts = settings.tts_backend in ("local_gpu", "auto")
    if not wants_stt and not wants_tts:
        return

    gpu_available, free_gb, total_gb = detect_vram_gb()
    threshold = settings.local_gpu_min_free_vram_gb
    vram_ok = gpu_available and free_gb >= threshold

    if not gpu_available:
        vram_reason = "no CUDA-capable GPU detected (torch not installed, or torch.cuda.is_available() is False)"
    else:
        vram_reason = (
            f"{free_gb:.2f}GB free VRAM (of {total_gb:.2f}GB total) "
            f"{'meets' if vram_ok else 'is below'} the {threshold:.2f}GB local_gpu_min_free_vram_gb threshold"
        )
    logger.info("Local GPU voice backend VRAM check: %s.", vram_reason)

    if wants_stt:
        attempt, reason = _gate(settings.stt_backend, gpu_available, vram_ok, vram_reason)
        if not attempt:
            logger.warning("STT_BACKEND=%s: %s -- using Colab STT.", settings.stt_backend, reason)
            _stt_decision = BackendDecision("colab", reason)
        else:
            try:
                _whisper_model = _load_whisper()
            except Exception as exc:
                logger.exception(
                    "STT_BACKEND=%s: local whisper %r load failed -- falling back to Colab STT.",
                    settings.stt_backend,
                    WHISPER_MODEL_SIZE,
                )
                _stt_decision = BackendDecision("colab", f"local load failed: {exc}")
            else:
                _stt_adapter = LocalGpuSTTAdapter(_whisper_model)
                _stt_decision = BackendDecision("local_gpu", reason)
                logger.info(
                    "STT_BACKEND=%s: loaded local whisper %r (language=%r) on GPU -- using local GPU STT.",
                    settings.stt_backend,
                    WHISPER_MODEL_SIZE,
                    WHISPER_LANGUAGE,
                )

    if wants_tts:
        attempt, reason = _gate(settings.tts_backend, gpu_available, vram_ok, vram_reason)
        missing_binaries = _missing_tts_binaries() if attempt else []
        if missing_binaries:
            attempt = False
            reason = f"missing required system binaries for leva-tts: {missing_binaries}"

        if not attempt:
            logger.warning("TTS_BACKEND=%s: %s -- using Colab TTS.", settings.tts_backend, reason)
            _tts_decision = BackendDecision("colab", reason)
        else:
            try:
                _leva_engine, speakers = _load_leva_tts()
            except Exception as exc:
                logger.exception(
                    "TTS_BACKEND=%s: local leva-tts load failed -- falling back to Colab TTS.",
                    settings.tts_backend,
                )
                _tts_decision = BackendDecision("colab", f"local load failed: {exc}")
            else:
                _tts_adapter = LocalGpuTTSAdapter(_leva_engine, speakers, DEFAULT_SPEAKER)
                _tts_decision = BackendDecision("local_gpu", reason)
                logger.info(
                    "TTS_BACKEND=%s: loaded local leva-tts (speakers=%s, default=%r) on GPU -- using local GPU TTS.",
                    settings.tts_backend,
                    speakers,
                    DEFAULT_SPEAKER,
                )


def get_stt_decision() -> BackendDecision | None:
    """None if stt_backend isn't "local_gpu"/"auto" (init_voice_backend() never ran
    its STT branch at all in that case) -- distinct from a BackendDecision whose
    .backend is "colab" (ran, but fell back)."""

    return _stt_decision


def get_tts_decision() -> BackendDecision | None:
    return _tts_decision


def get_local_stt_adapter() -> LocalGpuSTTAdapter | None:
    """The loaded singleton, or None if local STT wasn't attempted or failed to
    load -- app/api/deps.py falls back to ColabSTTAdapter in that case."""

    return _stt_adapter


def get_local_tts_adapter() -> LocalGpuTTSAdapter | None:
    return _tts_adapter


def dispose_voice_backend() -> None:
    """Releases GPU memory explicitly on shutdown -- mirrors
    app/infrastructure/db/engine.py's dispose_engine(). Safe to call even if
    init_voice_backend() never loaded anything (colab/whisper/stub backends, or
    a fallback -- every module global here is already None in that case)."""

    global _stt_decision, _tts_decision, _stt_adapter, _tts_adapter, _whisper_model, _leva_engine

    had_local_model = _whisper_model is not None or _leva_engine is not None

    _stt_adapter = None
    _tts_adapter = None
    _whisper_model = None
    _leva_engine = None
    _stt_decision = None
    _tts_decision = None

    if not had_local_model:
        return

    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass
