"""Tests for the local-GPU voice backend: app/infrastructure/voice_models.py's
VRAM detection/decision/fallback logic, and app/api/deps.py's
get_speech_to_text_port()/get_text_to_speech_port() wiring for
STT_BACKEND/TTS_BACKEND "local_gpu"/"auto".

None of this needs a real GPU, torch, whisper, or leva-tts installed --
torch.cuda is faked via a `types.SimpleNamespace` injected into
sys.modules["torch"] (undone automatically by pytest's monkeypatch after each
test), and the two "load the real model" functions
(voice_models._load_whisper/_load_leva_tts) are monkeypatched directly for the
success-path tests. The failure-path tests that rely on whisper/leva-tts
genuinely not being importable in this environment are real, not mocked --
see test_stt_falls_back_to_colab_when_whisper_package_missing and
test_tts_falls_back_to_colab_when_leva_tts_package_missing below; they
document the exact fallback this project depends on for a machine that
hasn't installed the optional local-GPU requirements (see requirements.txt's
"Local-GPU voice pipeline" section).
"""

from __future__ import annotations

import sys
import types

import pytest

from app.api import deps
from app.config import Settings
from app.infrastructure import voice_models

# ---------- test doubles ----------


def _fake_torch(
    *,
    cuda_available: bool,
    free_bytes: int = 0,
    total_bytes: int = 0,
    raise_on_mem_get_info: bool = False,
) -> types.SimpleNamespace:
    def is_available() -> bool:
        return cuda_available

    def mem_get_info(device: int = 0) -> tuple[int, int]:
        if raise_on_mem_get_info:
            raise RuntimeError("simulated driver failure")
        return free_bytes, total_bytes

    def empty_cache() -> None:
        pass

    cuda_ns = types.SimpleNamespace(is_available=is_available, mem_get_info=mem_get_info, empty_cache=empty_cache)
    return types.SimpleNamespace(cuda=cuda_ns)


def _settings(**overrides) -> Settings:
    defaults = dict(stt_backend="colab", tts_backend="colab", local_gpu_min_free_vram_gb=4.5)
    defaults.update(overrides)
    return Settings(**defaults)


@pytest.fixture(autouse=True)
def _reset_voice_backend_state():
    """voice_models.py holds process-wide module-level state (mirrors how
    app/infrastructure/inflight_sessions.py/ws_hub.py are process-wide
    singletons) -- reset it before and after every test in this file so tests
    don't leak state into each other."""

    voice_models.dispose_voice_backend()
    yield
    voice_models.dispose_voice_backend()


# ---------- detect_vram_gb() ----------


def test_detect_vram_reports_no_gpu_when_torch_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    # sys.modules[name] = None is Python's documented "block this import" sentinel --
    # import machinery raises ImportError immediately without touching whatever the real
    # module is. Deliberately NOT `monkeypatch.delitem(...)`: torch is actually installed
    # in this venv (a leftover CPU-only build -- see backend/docs/stt-tts-local-gpu-design.md),
    # and its native C extension keeps global state across a process's lifetime -- deleting
    # it from sys.modules and letting a later `import torch` re-run torch's init code
    # crashes with "Only a single TORCH_LIBRARY can be used ..." rather than cleanly
    # reimporting. Setting the sys.modules entry to None sidesteps that entirely.
    monkeypatch.setitem(sys.modules, "torch", None)

    gpu_available, free_gb, total_gb = voice_models.detect_vram_gb()

    assert gpu_available is False
    assert free_gb == 0.0
    assert total_gb == 0.0


def test_detect_vram_reports_no_gpu_when_cuda_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(cuda_available=False))

    gpu_available, free_gb, total_gb = voice_models.detect_vram_gb()

    assert gpu_available is False
    assert free_gb == 0.0
    assert total_gb == 0.0


def test_detect_vram_reports_real_free_and_total_gb(monkeypatch: pytest.MonkeyPatch) -> None:
    gib = 1024**3
    monkeypatch.setitem(
        sys.modules, "torch", _fake_torch(cuda_available=True, free_bytes=int(7.4 * gib), total_bytes=8 * gib)
    )

    gpu_available, free_gb, total_gb = voice_models.detect_vram_gb()

    assert gpu_available is True
    assert free_gb == pytest.approx(7.4, abs=0.01)
    assert total_gb == pytest.approx(8.0, abs=0.01)


def test_detect_vram_treats_unexpected_cuda_exception_as_no_gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(cuda_available=True, raise_on_mem_get_info=True))

    gpu_available, free_gb, total_gb = voice_models.detect_vram_gb()

    assert gpu_available is False
    assert free_gb == 0.0
    assert total_gb == 0.0


# ---------- init_voice_backend(): gating / decisions ----------


def test_colab_backends_never_touch_gpu_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    """settings.stt_backend/tts_backend == "colab" (or "whisper"/"gtts"/"stub") should make
    init_voice_backend() a no-op -- app/api/deps.py's existing branches handle those
    entirely on their own."""

    monkeypatch.setitem(sys.modules, "torch", None)  # would raise if this were even imported

    voice_models.init_voice_backend(_settings(stt_backend="colab", tts_backend="gtts"))

    assert voice_models.get_stt_decision() is None
    assert voice_models.get_tts_decision() is None
    assert voice_models.get_local_stt_adapter() is None
    assert voice_models.get_local_tts_adapter() is None


def test_auto_falls_back_to_colab_when_no_gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "torch", None)

    voice_models.init_voice_backend(_settings(stt_backend="auto", tts_backend="auto"))

    assert voice_models.get_stt_decision().backend == "colab"
    assert voice_models.get_tts_decision().backend == "colab"
    assert "no CUDA-capable GPU" in voice_models.get_stt_decision().reason
    assert voice_models.get_local_stt_adapter() is None
    assert voice_models.get_local_tts_adapter() is None


def test_auto_falls_back_to_colab_when_vram_below_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    gib = 1024**3
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(cuda_available=True, free_bytes=int(3.4 * gib), total_bytes=4 * gib))

    voice_models.init_voice_backend(_settings(stt_backend="auto", tts_backend="auto", local_gpu_min_free_vram_gb=4.5))

    decision = voice_models.get_stt_decision()
    assert decision.backend == "colab"
    assert "3.40GB" in decision.reason
    assert "4.50GB" in decision.reason
    assert voice_models.get_local_stt_adapter() is None


def test_auto_loads_local_models_when_vram_sufficient(monkeypatch: pytest.MonkeyPatch) -> None:
    gib = 1024**3
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(cuda_available=True, free_bytes=int(7.4 * gib), total_bytes=8 * gib))
    monkeypatch.setattr(voice_models, "_load_whisper", lambda: "FAKE_WHISPER_MODEL")
    monkeypatch.setattr(voice_models, "_load_leva_tts", lambda: ("FAKE_LEVA_ENGINE", ["Amina", "Other"]))
    monkeypatch.setattr(voice_models, "_missing_tts_binaries", lambda: [])

    voice_models.init_voice_backend(_settings(stt_backend="auto", tts_backend="auto", local_gpu_min_free_vram_gb=4.5))

    stt_decision = voice_models.get_stt_decision()
    tts_decision = voice_models.get_tts_decision()
    assert stt_decision.backend == "local_gpu"
    assert tts_decision.backend == "local_gpu"
    assert "7.40GB" in stt_decision.reason

    stt_adapter = voice_models.get_local_stt_adapter()
    tts_adapter = voice_models.get_local_tts_adapter()
    assert stt_adapter is not None
    assert tts_adapter is not None


def test_local_gpu_forced_skips_vram_threshold_but_still_needs_a_gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    gib = 1024**3
    # Deliberately below the 4.5GB threshold -- "local_gpu" (forced) should still attempt.
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(cuda_available=True, free_bytes=int(1.0 * gib), total_bytes=4 * gib))
    monkeypatch.setattr(voice_models, "_load_whisper", lambda: "FAKE_WHISPER_MODEL")

    voice_models.init_voice_backend(_settings(stt_backend="local_gpu", tts_backend="colab", local_gpu_min_free_vram_gb=4.5))

    assert voice_models.get_stt_decision().backend == "local_gpu"
    assert voice_models.get_local_stt_adapter() is not None


def test_local_gpu_forced_still_falls_back_without_a_real_gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "torch", None)

    voice_models.init_voice_backend(_settings(stt_backend="local_gpu", tts_backend="colab"))

    assert voice_models.get_stt_decision().backend == "colab"
    assert voice_models.get_local_stt_adapter() is None


def test_stt_and_tts_are_decided_independently(monkeypatch: pytest.MonkeyPatch) -> None:
    """A machine with enough VRAM but a missing TTS system dependency should still get
    local STT, with only TTS falling back -- not an all-or-nothing decision."""

    gib = 1024**3
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(cuda_available=True, free_bytes=int(7.4 * gib), total_bytes=8 * gib))
    monkeypatch.setattr(voice_models, "_load_whisper", lambda: "FAKE_WHISPER_MODEL")
    monkeypatch.setattr(voice_models, "_missing_tts_binaries", lambda: ["espeak-ng"])

    voice_models.init_voice_backend(_settings(stt_backend="auto", tts_backend="auto", local_gpu_min_free_vram_gb=4.5))

    assert voice_models.get_stt_decision().backend == "local_gpu"
    assert voice_models.get_local_stt_adapter() is not None

    tts_decision = voice_models.get_tts_decision()
    assert tts_decision.backend == "colab"
    assert "espeak-ng" in tts_decision.reason
    assert voice_models.get_local_tts_adapter() is None


def test_stt_falls_back_to_colab_when_whisper_package_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real (unmocked) failure path: the `whisper` package genuinely isn't installed in
    this test environment (it's an optional, not-yet-added-to-this-venv dependency -- see
    requirements.txt's "Local-GPU voice pipeline" section) -- this is exactly the
    "missing local-GPU deps" scenario voice_models.py must degrade from cleanly rather
    than crash the server on."""

    gib = 1024**3
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(cuda_available=True, free_bytes=int(7.4 * gib), total_bytes=8 * gib))
    monkeypatch.delitem(sys.modules, "whisper", raising=False)

    voice_models.init_voice_backend(_settings(stt_backend="auto", tts_backend="colab", local_gpu_min_free_vram_gb=4.5))

    decision = voice_models.get_stt_decision()
    assert decision.backend == "colab"
    assert "local load failed" in decision.reason
    assert voice_models.get_local_stt_adapter() is None


def test_tts_falls_back_to_colab_when_leva_tts_package_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real (unmocked) failure path -- same reasoning as the whisper test above, for
    the `leva_tts` package. System-binary checks are bypassed here (mocked to "present")
    so this test isolates the *pip package* missing case specifically, not the system
    dependency case (covered by test_stt_and_tts_are_decided_independently's sibling
    concept above and the missing-binaries test below)."""

    gib = 1024**3
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(cuda_available=True, free_bytes=int(7.4 * gib), total_bytes=8 * gib))
    monkeypatch.setattr(voice_models, "_missing_tts_binaries", lambda: [])
    monkeypatch.delitem(sys.modules, "leva_tts", raising=False)

    voice_models.init_voice_backend(_settings(stt_backend="colab", tts_backend="auto", local_gpu_min_free_vram_gb=4.5))

    decision = voice_models.get_tts_decision()
    assert decision.backend == "colab"
    assert "local load failed" in decision.reason
    assert voice_models.get_local_tts_adapter() is None


def test_missing_system_binaries_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(voice_models.shutil, "which", lambda name: None if name == "espeak-ng" else "/usr/bin/" + name)

    missing = voice_models._missing_tts_binaries()

    assert missing == ["espeak-ng"]


def test_dispose_voice_backend_resets_all_state(monkeypatch: pytest.MonkeyPatch) -> None:
    gib = 1024**3
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(cuda_available=True, free_bytes=int(7.4 * gib), total_bytes=8 * gib))
    monkeypatch.setattr(voice_models, "_load_whisper", lambda: "FAKE_WHISPER_MODEL")
    monkeypatch.setattr(voice_models, "_load_leva_tts", lambda: ("FAKE_LEVA_ENGINE", ["Amina"]))
    monkeypatch.setattr(voice_models, "_missing_tts_binaries", lambda: [])
    voice_models.init_voice_backend(_settings(stt_backend="auto", tts_backend="auto", local_gpu_min_free_vram_gb=4.5))
    assert voice_models.get_local_stt_adapter() is not None

    voice_models.dispose_voice_backend()

    assert voice_models.get_stt_decision() is None
    assert voice_models.get_tts_decision() is None
    assert voice_models.get_local_stt_adapter() is None
    assert voice_models.get_local_tts_adapter() is None


def test_dispose_voice_backend_is_a_safe_noop_when_nothing_loaded() -> None:
    voice_models.dispose_voice_backend()  # must not raise


# ---------- app/api/deps.py wiring ----------


def test_get_speech_to_text_port_uses_local_adapter_when_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = object()
    monkeypatch.setattr(deps, "get_settings", lambda: _settings(stt_backend="local_gpu"))
    monkeypatch.setattr(deps.voice_models, "get_local_stt_adapter", lambda: sentinel)

    assert deps.get_speech_to_text_port() is sentinel


def test_get_speech_to_text_port_falls_back_to_colab_when_local_adapter_not_loaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.infrastructure.colab_stt_adapter import ColabSTTAdapter

    monkeypatch.setattr(deps, "get_settings", lambda: _settings(stt_backend="auto"))
    monkeypatch.setattr(deps.voice_models, "get_local_stt_adapter", lambda: None)

    result = deps.get_speech_to_text_port()

    assert isinstance(result, ColabSTTAdapter)


def test_get_text_to_speech_port_uses_local_adapter_when_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = object()
    monkeypatch.setattr(deps, "get_settings", lambda: _settings(tts_backend="local_gpu"))
    monkeypatch.setattr(deps.voice_models, "get_local_tts_adapter", lambda: sentinel)

    assert deps.get_text_to_speech_port() is sentinel


def test_get_text_to_speech_port_falls_back_to_colab_when_local_adapter_not_loaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.infrastructure.colab_tts_adapter import ColabTTSAdapter

    monkeypatch.setattr(deps, "get_settings", lambda: _settings(tts_backend="auto"))
    monkeypatch.setattr(deps.voice_models, "get_local_tts_adapter", lambda: None)

    result = deps.get_text_to_speech_port()

    assert isinstance(result, ColabTTSAdapter)


# ---------- LocalGpuSTTAdapter / LocalGpuTTSAdapter: port contract ----------
#
# Fake "model"/"engine" objects standing in for the real whisper/leva-tts objects --
# these adapters never call into torch/whisper/leva-tts directly (voice_models.py loads
# the real thing and passes it in), so the port contract itself is fully testable without
# either package installed.


class _FakeWhisperModel:
    def __init__(self, text: str | None = None, raise_exc: Exception | None = None) -> None:
        self._text = text
        self._raise_exc = raise_exc

    def transcribe(self, path: str, language: str) -> dict:
        if self._raise_exc is not None:
            raise self._raise_exc
        return {"text": self._text}


async def test_local_gpu_stt_adapter_returns_transcript() -> None:
    from app.infrastructure.local_gpu_stt_adapter import LocalGpuSTTAdapter

    adapter = LocalGpuSTTAdapter(_FakeWhisperModel(text="مرحبا"))

    text = await adapter.transcribe(b"\x00\x01\x02\x03", filename="clip.wav")

    assert text == "مرحبا"


async def test_local_gpu_stt_adapter_rejects_empty_audio() -> None:
    from app.domain.exceptions import VoiceServiceUnavailableError
    from app.infrastructure.local_gpu_stt_adapter import LocalGpuSTTAdapter

    adapter = LocalGpuSTTAdapter(_FakeWhisperModel(text="unused"))

    with pytest.raises(VoiceServiceUnavailableError):
        await adapter.transcribe(b"", filename="clip.wav")


async def test_local_gpu_stt_adapter_raises_when_transcript_is_empty() -> None:
    from app.domain.exceptions import VoiceServiceUnavailableError
    from app.infrastructure.local_gpu_stt_adapter import LocalGpuSTTAdapter

    adapter = LocalGpuSTTAdapter(_FakeWhisperModel(text="   "))

    with pytest.raises(VoiceServiceUnavailableError):
        await adapter.transcribe(b"\x00\x01", filename="clip.wav")


async def test_local_gpu_stt_adapter_wraps_inference_exception() -> None:
    from app.domain.exceptions import VoiceServiceUnavailableError
    from app.infrastructure.local_gpu_stt_adapter import LocalGpuSTTAdapter

    adapter = LocalGpuSTTAdapter(_FakeWhisperModel(raise_exc=RuntimeError("cuda OOM")))

    with pytest.raises(VoiceServiceUnavailableError, match="cuda OOM"):
        await adapter.transcribe(b"\x00\x01", filename="clip.wav")


class _FakeLevaEngine:
    def __init__(self, raise_exc: Exception | None = None) -> None:
        self._raise_exc = raise_exc

    def synthesize(self, text: str, speaker: str):
        if self._raise_exc is not None:
            raise self._raise_exc
        import numpy as np

        return np.zeros(1600, dtype=np.float32), 16_000


async def test_local_gpu_tts_adapter_returns_wav_bytes() -> None:
    from app.infrastructure.local_gpu_tts_adapter import DEFAULT_SPEAKER, LocalGpuTTSAdapter

    adapter = LocalGpuTTSAdapter(_FakeLevaEngine(), speakers=[DEFAULT_SPEAKER, "Other"])

    audio = await adapter.synthesize("مرحبا")

    assert isinstance(audio, bytes)
    assert audio[:4] == b"RIFF"
    assert audio[8:12] == b"WAVE"


async def test_local_gpu_tts_adapter_rejects_empty_text() -> None:
    from app.domain.exceptions import VoiceServiceUnavailableError
    from app.infrastructure.local_gpu_tts_adapter import DEFAULT_SPEAKER, LocalGpuTTSAdapter

    adapter = LocalGpuTTSAdapter(_FakeLevaEngine(), speakers=[DEFAULT_SPEAKER])

    with pytest.raises(VoiceServiceUnavailableError):
        await adapter.synthesize("   ")


async def test_local_gpu_tts_adapter_wraps_synthesis_exception() -> None:
    from app.domain.exceptions import VoiceServiceUnavailableError
    from app.infrastructure.local_gpu_tts_adapter import DEFAULT_SPEAKER, LocalGpuTTSAdapter

    adapter = LocalGpuTTSAdapter(_FakeLevaEngine(raise_exc=RuntimeError("cuda OOM")), speakers=[DEFAULT_SPEAKER])

    with pytest.raises(VoiceServiceUnavailableError, match="cuda OOM"):
        await adapter.synthesize("مرحبا")


def test_local_gpu_tts_adapter_falls_back_to_default_speaker_if_given_an_invalid_one() -> None:
    from app.infrastructure.local_gpu_tts_adapter import DEFAULT_SPEAKER, LocalGpuTTSAdapter

    adapter = LocalGpuTTSAdapter(_FakeLevaEngine(), speakers=[DEFAULT_SPEAKER], speaker="not-a-real-speaker")

    assert adapter._speaker == DEFAULT_SPEAKER
