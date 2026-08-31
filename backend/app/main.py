"""App factory: router registration, CORS, lifespan (open/close the DB engine)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.error_handlers import register_error_handlers
from app.api.routers import chat, health, questions, scenarios, sessions, test_categories, voice
from app.config import get_settings
from app.infrastructure.db import engine as db_engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    db_engine.init_engine()
    yield
    await db_engine.dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Patient Simulation API",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_error_handlers(app)

    app.include_router(health.router)
    app.include_router(scenarios.router)
    app.include_router(test_categories.router)
    app.include_router(questions.router)
    app.include_router(sessions.router)
    app.include_router(chat.router)
    app.include_router(voice.router)

    return app


app = create_app()
