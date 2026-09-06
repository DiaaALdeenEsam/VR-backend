"""Central mapping from domain exceptions to HTTP responses.

Registered once in app/main.py so routers stay thin -- they call a use case
and map the result to a response schema, with no try/except business logic.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.domain.exceptions import (
    DomainError,
    InvalidChoiceError,
    NotFoundError,
    SessionBusyError,
    VoiceServiceUnavailableError,
)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(NotFoundError)
    async def _not_found(request: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(InvalidChoiceError)
    async def _invalid_choice(request: Request, exc: InvalidChoiceError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(SessionBusyError)
    async def _session_busy(request: Request, exc: SessionBusyError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(VoiceServiceUnavailableError)
    async def _voice_service_unavailable(request: Request, exc: VoiceServiceUnavailableError) -> JSONResponse:
        # 502: this process is acting as a gateway to the remote Colab-hosted
        # STT/TTS API, and that upstream call is what failed -- distinct from
        # the generic DomainError -> 400 "bad request" mapping below.
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.exception_handler(DomainError)
    async def _domain_error(request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})
