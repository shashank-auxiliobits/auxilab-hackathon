"""Liveness and readiness probes."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from ap_invoice.api.deps import DBSession

router = APIRouter(tags=["health"])


@router.get("/health/live", summary="Liveness probe")
async def live() -> dict[str, str]:
    """Process is up. Does not touch external dependencies."""
    return {"status": "ok"}


@router.get("/health/ready", summary="Readiness probe")
async def ready(db: DBSession) -> dict[str, str]:
    """Ready to serve traffic — verifies database connectivity."""
    await db.execute(text("SELECT 1"))
    return {"status": "ready", "database": "ok"}
