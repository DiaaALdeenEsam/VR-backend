"""Background watcher for the RAG API tunnel's liveness -- see
Settings.rag_tunnel_watch_enabled/_interval_seconds/_timeout_seconds
(app/config.py) for what it does, why it's disabled by default, and how it
relates to get_settings()'s process-wide @lru_cache.

Started/stopped from app/main.py's lifespan, mirroring
app/infrastructure/voice_models.py's init_.../dispose_... pair -- the one
structural difference being this runs continuously as a background
asyncio.Task rather than making one decision at startup and being done.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from app.config import Settings, get_settings, refresh_settings
from app.infrastructure.rag_api_common import RAG_API_USER_AGENT

logger = logging.getLogger(__name__)

_task: asyncio.Task[None] | None = None


async def _probe_readyz(base_url: str, timeout_seconds: float) -> bool:
    """True iff GET {base_url}/readyz returned 200. No API key sent --
    docs/backend-rag-handoff.md: "Health and readiness do not require a
    key." Any connection-level failure (DNS, refused, timeout, TLS) is
    treated the same as a non-200 response: the tunnel isn't currently
    healthy, full stop -- this function only answers "is it up", the caller
    decides what to do about "no"."""

    try:
        async with httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_seconds),
            headers={"User-Agent": RAG_API_USER_AGENT},
        ) as client:
            response = await client.get("/readyz")
        return response.status_code == 200
    except httpx.RequestError:
        return False


async def _check_once(timeout_seconds: float) -> None:
    settings = get_settings()
    base_url = settings.rag_api_base_url
    if not base_url:
        return

    if await _probe_readyz(base_url, timeout_seconds):
        return

    # /readyz failed against the currently-cached URL -- before concluding
    # anything, check whether .env now says something different. Settings()
    # (the class, not get_settings()) deliberately bypasses the @lru_cache
    # to force a genuine re-read of .env/the environment right now, not
    # whatever was cached at process startup or the last refresh.
    fresh_base_url = Settings().rag_api_base_url
    if fresh_base_url and fresh_base_url != base_url:
        logger.warning(
            "[rag_tunnel_watch] readyz_failed_url_changed | old_url=%s | new_url=%s -- "
            "refreshing cached settings so the next request uses the new URL",
            base_url,
            fresh_base_url,
        )
        refresh_settings()
    else:
        logger.warning(
            "[rag_tunnel_watch] readyz_failed | url=%s -- RAG_API_BASE_URL in .env is "
            "unchanged, nothing to refresh; the tunnel itself appears to be down",
            base_url,
        )


async def _watch_loop(interval_seconds: float, timeout_seconds: float) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await _check_once(timeout_seconds)
        except Exception:
            # A bug in the watcher itself must never take down the loop or
            # the process -- this is best-effort background housekeeping,
            # not a request path anything else depends on.
            logger.exception("[rag_tunnel_watch] check_failed_unexpectedly")


def start(settings: Settings) -> None:
    """Starts the background watch loop iff Settings.rag_tunnel_watch_enabled
    is True and a RAG API base URL is configured; a no-op otherwise -- safe
    to call unconditionally from app/main.py's lifespan every process
    startup, same pattern as voice_models.init_voice_backend()."""

    global _task
    if not settings.rag_tunnel_watch_enabled or not settings.rag_api_base_url:
        return
    if _task is not None:
        return  # already running -- defensive; lifespan only calls this once in practice

    logger.info(
        "[rag_tunnel_watch] started | interval_s=%.0f | timeout_s=%.0f",
        settings.rag_tunnel_watch_interval_seconds,
        settings.rag_tunnel_watch_timeout_seconds,
    )
    _task = asyncio.create_task(
        _watch_loop(settings.rag_tunnel_watch_interval_seconds, settings.rag_tunnel_watch_timeout_seconds)
    )


async def stop() -> None:
    """Cancels the watch loop and waits for it to actually stop. Safe to
    call even if start() was never called, or was a no-op -- nothing to
    cancel in that case."""

    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    _task = None
