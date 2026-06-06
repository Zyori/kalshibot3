"""Positions API.

Read-only listing of the user's current open positions — soccer single-market
positions and combo (parlay) positions alike. Single source of truth is the
POSITION table, which position_sync reconciles against Kalshi every 60s plus on
every order placement (tracked = soccer + combos, via is_tradeable_ticker).

This route never hits Kalshi — keeping the dashboard polling cheap.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.db import get_session
from src.core.types import BetSide, Sport, position_avg_entry_price, utc_iso
from src.ingestion.market_discovery import MarketFeed
from src.models import Bet, ComboLeg, Position

router = APIRouter()


async def _combo_leg_counts(
    session: AsyncSession, market_ids: list[int]
) -> dict[int, int]:
    """Leg count per combo market_id — for the "Parlay (N legs)" label. Combos
    have no per-game title in the feed, so we count the legs attached to the
    bet(s) on that market. Batched to avoid an N+1 over the position list."""
    if not market_ids:
        return {}
    rows = (await session.execute(
        select(Bet.market_id, func.count(ComboLeg.id))
        .join(ComboLeg, ComboLeg.bet_id == Bet.id)
        .where(Bet.market_id.in_(market_ids))
        .group_by(Bet.market_id)
    )).all()
    return {market_id: n for market_id, n in rows}


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
    """Every open position in our DB — soccer singles and combos. Soccer
    positions get a team/outcome label from the discovery feed; combos get a
    "Parlay (N legs)" label (they have no per-game title)."""
    supervisor = getattr(request.app.state, "supervisor", None)
    feed = supervisor.market_discovery.get_feed() if supervisor is not None else None
    rows = (await session.execute(select(Position).order_by(Position.kalshi_ticker))).scalars().all()
    combo_market_ids = [p.market_id for p in rows if p.sport == Sport.COMBO]
    leg_counts = await _combo_leg_counts(session, combo_market_ids)

    def _label(p: Position) -> str | None:
        if p.sport == Sport.COMBO:
            n = leg_counts.get(p.market_id, 0)
            return f"Parlay ({n} legs)" if n else "Parlay"
        return _position_label(p.kalshi_ticker, p.side, feed)

    return {
        "positions": [
            {
                "ticker": p.kalshi_ticker,
                "label": _label(p),
                "sport": p.sport,
                "side": p.side,
                "quantity": p.quantity,
                "avg_entry_price_cents": p.avg_entry_price_cents,
                "avg_entry_price": position_avg_entry_price(
                    p.cost_basis_cents, p.fees_paid_cents, p.quantity,
                    p.avg_entry_price_cents,
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
