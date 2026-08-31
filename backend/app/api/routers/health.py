from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.deps import get_db_session

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check(session: AsyncSession = Depends(get_db_session)) -> dict[str, str]:
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - exercised only when the DB is actually down
        raise HTTPException(status_code=503, detail=f"database unavailable: {exc}") from exc
    return {"status": "ok", "database": "ok"}
