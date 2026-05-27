"""Ledger API — bet history with filters + aggregate stats.

Two endpoints:
  GET /api/ledger        rows, with optional filters + cursor pagination
  GET /api/ledger/stats  aggregate over the same filter shape

Filters are query-string repeatable:
  ?sport=soccer
  ?status=won&status=lost            multiple values OR'd
  ?strategy=mean_reversion
  ?source=human
  ?timing=pre_match
  ?since=2026-05-01T00:00:00Z
  ?until=2026-05-31T23:59:59Z
  ?market=KXWCGAME-26JUN11MEXRSA-MEX
  ?limit=100
  ?cursor=<opaque>

All money fields are integer cents on the wire — frontend formats.

Cross-market isolation: positions and bets are already soccer-only at the
write boundary (bet_service refuses non-soccer tickers), so we don't
re-filter here. If a non-soccer BET ever appeared in the DB it would be a
bug worth surfacing, not silently hiding.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.db import get_session
from src.core.types import utc_iso
from src.models import Bet, Market

router = APIRouter()


def _bet_to_dict(b: Bet, ticker: str | None) -> dict[str, Any]:
    return {
        "id": b.id,
        "sport": b.sport,
        "ticker": ticker,
        "market_id": b.market_id,
        "kalshi_order_id": b.kalshi_order_id,
        "side": b.side,
        "entry_price_cents": b.entry_price_cents,
        "exit_price_cents": b.exit_price_cents,
        "quantity": b.quantity,
        "stake_cents": b.stake_cents,
        "pnl_cents": b.pnl_cents,
        "status": b.status,
        "exit_type": b.exit_type,
        "source": b.source,
        "strategy": b.strategy,
        "confidence": b.confidence,
        "timing": b.timing,
        "human_reasoning": b.human_reasoning,
        "ai_reasoning": b.ai_reasoning,
        "tags": b.tags,
        "placed_at": utc_iso(b.placed_at),
        "settled_at": utc_iso(b.settled_at),
        "created_at": utc_iso(b.created_at),
    }


def _apply_filters(
    stmt,
    *,
    sport: list[str] | None,
    status: list[str] | None,
    strategy: list[str] | None,
    source: list[str] | None,
    timing: list[str] | None,
    market_ticker: str | None,
    since: datetime | None,
    until: datetime | None,
):
    if sport:
        stmt = stmt.where(Bet.sport.in_(sport))
    if status:
        stmt = stmt.where(Bet.status.in_(status))
    if strategy:
        stmt = stmt.where(Bet.strategy.in_(strategy))
    if source:
        stmt = stmt.where(Bet.source.in_(source))
    if timing:
        stmt = stmt.where(Bet.timing.in_(timing))
    if market_ticker:
        # Join via market_id — Bet doesn't store ticker directly.
        stmt = stmt.join(Market, Market.id == Bet.market_id).where(
            Market.kalshi_ticker == market_ticker
        )
    if since:
        stmt = stmt.where(Bet.placed_at >= since)
    if until:
        stmt = stmt.where(Bet.placed_at <= until)
    return stmt


@router.get("/ledger")
async def list_bets(
    sport: list[str] = Query(default_factory=list),
    status: list[str] = Query(default_factory=list),
    strategy: list[str] = Query(default_factory=list),
    source: list[str] = Query(default_factory=list),
    timing: list[str] = Query(default_factory=list),
    market: str | None = Query(default=None),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    cursor: int | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Paginated bet history. Cursor is the last seen bet.id (descending)."""
    stmt = select(Bet, Market.kalshi_ticker).join(
        Market, Market.id == Bet.market_id, isouter=True
    )
    stmt = _apply_filters(
        stmt,
        sport=sport or None,
        status=status or None,
        strategy=strategy or None,
        source=source or None,
        timing=timing or None,
        market_ticker=market,
        since=since,
        until=until,
    )
    if cursor is not None:
        stmt = stmt.where(Bet.id < cursor)
    stmt = stmt.order_by(Bet.placed_at.desc().nulls_last(), Bet.id.desc()).limit(limit + 1)

    rows = (await session.execute(stmt)).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    out = [_bet_to_dict(b, ticker) for b, ticker in rows]
    next_cursor = rows[-1][0].id if has_more and rows else None
    return {"bets": out, "next_cursor": next_cursor}


@router.get("/ledger/stats")
async def ledger_stats(
    sport: list[str] = Query(default_factory=list),
    status: list[str] = Query(default_factory=list),
    strategy: list[str] = Query(default_factory=list),
    source: list[str] = Query(default_factory=list),
    timing: list[str] = Query(default_factory=list),
    market: str | None = Query(default=None),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Aggregate stats over the filtered bet set.

    Returns counts, total P&L (sum of pnl_cents, treating None as 0), win
    rate (won / settled), ROI (P&L / total stake), and a strategy-level
    breakdown for the StrategyBreakdown chart.
    """
    # Build each aggregate query directly with the same filter chain. The
    # previous shape — `select(...).select_from(base.subquery())` — produced
    # a cartesian product between `bet` and the subquery because the
    # aggregate referenced Bet.status directly, which SQLAlchemy resolved
    # against the `bet` table, not the subquery. Inflated total_bets by 3x.
    def _filtered(stmt):
        return _apply_filters(
            stmt,
            sport=sport or None,
            status=status or None,
            strategy=strategy or None,
            source=source or None,
            timing=timing or None,
            market_ticker=market,
            since=since,
            until=until,
        )

    # Counts by status
    status_rows = (
        await session.execute(
            _filtered(select(Bet.status, func.count(Bet.id)).group_by(Bet.status))
        )
    ).all()
    by_status = {row[0]: row[1] for row in status_rows}

    # Aggregates over the whole filtered set (pnl_cents is null for OPEN —
    # SUM ignores nulls, so this gives realized-P&L on settled bets only).
    agg = (
        await session.execute(
            _filtered(select(
                func.coalesce(func.sum(Bet.pnl_cents), 0),
                func.coalesce(func.sum(Bet.stake_cents), 0),
                func.count(Bet.id),
            ))
        )
    ).first()
    total_pnl_cents = int(agg[0]) if agg else 0
    total_stake_cents = int(agg[1]) if agg else 0
    total_bets = int(agg[2]) if agg else 0

    won = by_status.get("won", 0)
    lost = by_status.get("lost", 0)
    settled = won + lost
    win_rate = (won / settled) if settled else None
    roi = (total_pnl_cents / total_stake_cents) if total_stake_cents else None

    # Per-strategy breakdown for StrategyBreakdown chart
    strategy_rows = (
        await session.execute(
            _filtered(select(
                Bet.strategy,
                func.count(Bet.id),
                func.coalesce(func.sum(Bet.pnl_cents), 0),
                func.coalesce(func.sum(Bet.stake_cents), 0),
            ).group_by(Bet.strategy))
        )
    ).all()
    by_strategy = [
        {
            "strategy": row[0],
            "count": int(row[1]),
            "pnl_cents": int(row[2]),
            "stake_cents": int(row[3]),
            "roi": (int(row[2]) / int(row[3])) if int(row[3]) else None,
        }
        for row in strategy_rows
    ]

    return {
        "total_bets": total_bets,
        "by_status": by_status,
        "total_pnl_cents": total_pnl_cents,
        "total_stake_cents": total_stake_cents,
        "win_rate": win_rate,
        "roi": roi,
        "by_strategy": by_strategy,
    }
