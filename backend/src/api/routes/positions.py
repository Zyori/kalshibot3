"""Positions API.

Read-only listing of the user's current soccer positions. Single source of
truth is the POSITION table, which position_sync reconciles against Kalshi
every 60s plus on every order placement.

This route never hits Kalshi — keeping the dashboard polling cheap.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.db import get_session
from src.core.types import BetSide, utc_iso
from src.ingestion.market_discovery import MarketFeed
from src.models import Position

router = APIRouter()


def _position_label(ticker: str, side: BetSide, feed: MarketFeed | None) -> str | None:
    """Human-readable outcome label for a position, sourced from the discovery
    feed (single source of truth for market titles). The feed's yes_sub_title is
    always the YES outcome, so the label flips with the held side: YES on Nigeria
    is "Nigeria WIN", NO on Nigeria is "Nigeria NOT WIN" (a bet *against* it).
    "Poland - Nigeria DRAW" / "... NOT DRAW" for the tie market. None when the
    ticker isn't in the current feed (settled/aged out) — UI falls back to ticker.
    """
    if feed is None:
        return None
    row = next(
        (
            m
            for bucket in (feed.live, feed.upcoming, feed.recent)
            for m in bucket
            if m.ticker == ticker
        ),
        None,
    )
    if row is None:
        return None
    sub = (row.yes_sub_title or "").strip()
    negate = side == BetSide.NO
    if sub.lower() in ("tie", "draw"):
        # Derive "Poland - Nigeria" from the event title ("Poland vs Nigeria").
        base = f"{row.event_title.replace(' vs ', ' - ')} DRAW"
        return f"{base.removesuffix(' DRAW')} NOT DRAW" if negate else base
    if not sub:
        return None
    return f"{sub} NOT WIN" if negate else f"{sub} WIN"


@router.get("/positions")
async def list_positions(
    request: Request, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    """Every open position in our DB. position_sync filters to soccer only,
    so this is implicitly soccer-only too."""
    supervisor = getattr(request.app.state, "supervisor", None)
    feed = supervisor.market_discovery.get_feed() if supervisor is not None else None
    rows = (await session.execute(select(Position).order_by(Position.kalshi_ticker))).scalars().all()
    return {
        "positions": [
            {
                "ticker": p.kalshi_ticker,
                "label": _position_label(p.kalshi_ticker, p.side, feed),
                "side": p.side,
                "quantity": p.quantity,
                "avg_entry_price_cents": p.avg_entry_price_cents,
                # Exact fractional avg entry matching kalshi.com (e.g. 57.71):
                # (cost_basis + fees) / quantity. Kalshi's position avg-price
                # is fee-inclusive — cost alone reads ~0.17¢ low per contract.
                # Falls back to the clamped whole-cent value until the next
                # sync backfills cost_basis_cents.
                "avg_entry_price": (
                    round((p.cost_basis_cents + (p.fees_paid_cents or 0)) / p.quantity, 2)
                    if p.cost_basis_cents is not None and p.quantity > 0
                    else p.avg_entry_price_cents
                ),
                "cost_basis_cents": p.cost_basis_cents,
                "current_price_cents": p.current_price_cents,
                "unrealized_pnl_cents": p.unrealized_pnl_cents,
                "realized_pnl_cents": p.realized_pnl_cents,
                "fees_paid_cents": p.fees_paid_cents,
                "last_synced": utc_iso(p.last_synced),
            }
            for p in rows
        ],
    }
