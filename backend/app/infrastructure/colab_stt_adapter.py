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
