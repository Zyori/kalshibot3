"""Health endpoint.

Reports the three things the dashboard needs to know on every page load:
  - Which Kalshi environment we're talking to (demo vs production)
  - Whether the DB connection is up
  - Whether Kalshi auth is working (and the current balance if so)

The Kalshi auth result is cached in app.state by the startup lifespan and
refreshed on demand here. Hitting Kalshi from inside the health route would
make /api/health rate-limit-sensitive — bad pattern for a polled endpoint.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import text

from src.core.db import get_session_factory

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    """Single endpoint the dashboard polls to render the environment banner.

    Never raises — if anything inside is broken, report it in the response so
    the UI can communicate "backend up, X subsystem down" rather than showing
    a generic "backend offline."
    """
    app_state = request.app.state
    settings = app_state.settings

    # DB liveness — single SELECT 1 round-trip.
    db_ok = False
    db_error: str | None = None
    try:
        factory = get_session_factory()
        async with factory() as session:
            await session.execute(text("SELECT 1"))
        db_ok = True
    except Exception as e:  # noqa: BLE001 — defensive: health must never raise
        db_error = str(e)[:200]

    # Kalshi auth status comes from the lifespan probe stored in app.state.
    # Don't re-probe here — that would burn a rate-limit slot per page load.
    kalshi = {
        "ok": bool(getattr(app_state, "kalshi_auth_ok", False)),
        "checked_at": getattr(app_state, "kalshi_auth_checked_at", None),
        "balance_cents": getattr(app_state, "kalshi_balance_cents", None),
        "error": getattr(app_state, "kalshi_auth_error", None),
    }

    return {
        "app": "kalshibot3",
        "environment": settings.environment.value,
        "status": "alive",
        "db": {"ok": db_ok, "error": db_error},
        "kalshi": kalshi,
    }
