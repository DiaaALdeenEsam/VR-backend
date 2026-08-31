"""Central mapping from domain exceptions to HTTP responses.

Registered once in app/main.py so routers stay thin -- they call a use case
and map the result to a response schema, with no try/except business logic.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.domain.exceptions import DomainError, InvalidChoiceError, NotFoundError


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(NotFoundError)
    async def _not_found(request: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(InvalidChoiceError)
    async def _invalid_choice(request: Request, exc: InvalidChoiceError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(DomainError)
    async def _domain_error(request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})
