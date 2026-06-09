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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.db import get_session
from src.core.types import BetSide, Sport, position_avg_entry_price, utc_iso
from src.ingestion.market_discovery import MarketFeed
from src.models import Bet, ComboLeg, Position
from src.sports.combo import combo_leg_pick, uniform_combo_sport
from src.sports.soccer import is_total_goals_ticker, total_goals_label

router = APIRouter()


class ComboInfo:
    """Per combo market: leg count, shared sport (or None if mixed), and a
    compact pick list for the card chips."""

    __slots__ = ("count", "sport", "legs")

    def __init__(
        self, count: int, sport: str | None, legs: list[dict[str, Any]]
    ) -> None:
        self.count = count
        self.sport = sport
        self.legs = legs


async def _combo_info(
    session: AsyncSession, market_ids: list[int]
) -> dict[int, ComboInfo]:
    """Leg count, shared sport, and compact per-leg picks for each combo
    market_id, in one batched query (no N+1 over the position list).

    A pick = {pick, side, result}: the short label (combo_leg_pick), the backed
    side, and the resolved result (None while pending) so the card can show
    ✓/✗ as legs settle."""
    if not market_ids:
        return {}
    rows = (await session.execute(
        select(
            Bet.market_id,
            ComboLeg.leg_title,
            ComboLeg.leg_ticker,
            ComboLeg.side,
            ComboLeg.result,
        )
        .join(ComboLeg, ComboLeg.bet_id == Bet.id)
        .where(Bet.market_id.in_(market_ids))
        .order_by(Bet.market_id, ComboLeg.leg_index)
    )).all()
    tickers: dict[int, list[str | None]] = {}
    legs: dict[int, list[dict[str, Any]]] = {}
    for market_id, title, ticker, side, result in rows:
        tickers.setdefault(market_id, []).append(ticker)
        legs.setdefault(market_id, []).append({
            "pick": combo_leg_pick(title, ticker),
            "side": side,
            "result": result,
        })
    return {
        mid: ComboInfo(len(legs[mid]), uniform_combo_sport(tickers[mid]), legs[mid])
        for mid in legs
    }


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
    if is_total_goals_ticker(ticker):
        return total_goals_label(sub, row.event_title, negate=negate)
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
    combo_info = await _combo_info(session, combo_market_ids)

    def _label(p: Position) -> str | None:
        if p.sport == Sport.COMBO:
            info = combo_info.get(p.market_id)
            n = info.count if info else 0
            return f"Parlay ({n} legs)" if n else "Parlay"
        return _position_label(p.kalshi_ticker, p.side, feed)

    def _row(p: Position) -> dict[str, Any]:
        info = combo_info.get(p.market_id) if p.sport == Sport.COMBO else None
        return {
            "ticker": p.kalshi_ticker,
            "label": _label(p),
            "sport": p.sport,
            # Same-sport parlay → that sport for the badge; None otherwise.
            "leg_sport": info.sport if info else None,
            # Compact per-leg picks for the card chips (combos only).
            "legs": info.legs if info else None,
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

    return {"positions": [_row(p) for p in rows]}
