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
