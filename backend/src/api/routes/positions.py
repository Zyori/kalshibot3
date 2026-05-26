"""Positions API.

Read-only listing of the user's current soccer positions. Single source of
truth is the POSITION table, which position_sync reconciles against Kalshi
every 60s plus on every order placement.

This route never hits Kalshi — keeping the dashboard polling cheap.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.db import get_session
from src.models import Position

router = APIRouter()


@router.get("/positions")
async def list_positions(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Every open position in our DB. position_sync filters to soccer only,
    so this is implicitly soccer-only too."""
    rows = (await session.execute(select(Position).order_by(Position.kalshi_ticker))).scalars().all()
    return {
        "positions": [
            {
                "ticker": p.kalshi_ticker,
                "side": p.side,
                "quantity": p.quantity,
                "avg_entry_price_cents": p.avg_entry_price_cents,
                "current_price_cents": p.current_price_cents,
                "unrealized_pnl_cents": p.unrealized_pnl_cents,
                "last_synced": p.last_synced.isoformat() if p.last_synced else None,
            }
            for p in rows
        ],
    }
