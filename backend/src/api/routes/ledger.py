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

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import Select, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.db import get_session
from src.core.locks import ledger_guard
from src.core.logging import get_logger
from src.core.types import (
    BetSide,
    BetSource,
    BetStatus,
    Confidence,
    SnapshotPhase,
    Sport,
    Strategy,
    Timing,
    utc_iso,
)
from src.kalshi.rest import KalshiRestClient
from src.kalshi.schemas import Fill as RestFill
from src.models import Bet, BetFill, ComboLeg, Market, TradeSnapshot
from src.services.bet_service import (
    ExternalFillInput,
    record_external_position,
    settle_bets_for_market,
)
from src.sports.combo import uniform_combo_sport
from src.sports.soccer import (
    is_per_game_soccer_ticker,
    is_spread_ticker,
    is_total_goals_ticker,
    league_display_name,
    parse_market_ticker,
    spread_label,
    total_goals_label,
    total_goals_line,
)
from src.sports.tradeable import is_tradeable_ticker

log = get_logger(__name__)

router = APIRouter()


async def _combo_counts(
    session: AsyncSession, bet_ids: list[int]
) -> tuple[dict[int, int], dict[int, int]]:
    """Batch (leg_count, missed_count) per combo bet — avoids N+1 in the list
    view and keeps the metadata-PATCH response label consistent with it.

    A missed leg = a resolved leg whose result differs from the side we backed.
    Both result and side must be non-null: a leg with an unknown side can't be
    judged a miss, so it's excluded (NULL != x is NULL in SQL anyway, but the
    explicit guard documents the intent)."""
    leg_counts: dict[int, int] = {}
    missed_counts: dict[int, int] = {}
    if not bet_ids:
        return leg_counts, missed_counts
    count_rows = (await session.execute(
        select(ComboLeg.bet_id, func.count(ComboLeg.id))
        .where(ComboLeg.bet_id.in_(bet_ids))
        .group_by(ComboLeg.bet_id)
    )).all()
    leg_counts = {bid: n for bid, n in count_rows}
    missed_rows = (await session.execute(
        select(ComboLeg.bet_id, func.count(ComboLeg.id))
        .where(ComboLeg.bet_id.in_(bet_ids))
        .where(ComboLeg.result.is_not(None))
        .where(ComboLeg.side.is_not(None))
        .where(ComboLeg.result != ComboLeg.side)
        .group_by(ComboLeg.bet_id)
    )).all()
    missed_counts = {bid: n for bid, n in missed_rows}
    return leg_counts, missed_counts


async def _combo_leg_sports(
    session: AsyncSession, bet_ids: list[int]
) -> dict[int, str | None]:
    """Per combo bet, the single sport all its legs share (e.g. an all-World-Cup
    parlay → 'soccer'), or None when the legs mix sports. Lets a same-sport
    parlay show that sport's badge while the bet stays Sport.COMBO. Batched."""
    if not bet_ids:
        return {}
    rows = (await session.execute(
        select(ComboLeg.bet_id, ComboLeg.leg_ticker)
        .where(ComboLeg.bet_id.in_(bet_ids))
        .order_by(ComboLeg.bet_id, ComboLeg.leg_index)
    )).all()
    by_bet: dict[int, list[str | None]] = {}
    for bet_id, leg_ticker in rows:
        by_bet.setdefault(bet_id, []).append(leg_ticker)
    return {bid: uniform_combo_sport(tickers) for bid, tickers in by_bet.items()}


def _market_label(
    b: Bet,
    ticker: str | None,
    leg_count: int = 0,
    missed_count: int = 0,
    market_title: str | None = None,
) -> str:
    """Human-readable market: 'League — Home v Away — Selection'. Prefers full
    team names (captured from ESPN at placement), falls back to the 3-letter
    codes (always present for a per-game bet), then to the raw ticker when the
    bet predates these fields or isn't a per-game market. The SIDE (yes/no)
    is rendered separately by the frontend.

    Combos have no per-game codes; they render as 'Parlay (N legs)', with the
    miss count appended once legs are resolved (e.g. 'Parlay (7 legs · 1 missed)')
    so the reason a parlay lost is visible without expanding it."""
    if b.sport == Sport.COMBO:
        if not leg_count:
            return ticker or "Parlay"
        suffix = f" · {missed_count} missed" if missed_count else ""
        return f"Parlay ({leg_count} legs{suffix})"
    # Totals (Over/Under): no team selection, so the per-game branch below can't
    # label them. The line lives in Kalshi's stored title ("Over 1.5 goals
    # scored" → "Over 1.5 goals"), captured at order time. Older bets without a
    # stored title fall back to the matchup-only label from the ticker codes.
    if ticker and is_total_goals_ticker(ticker):
        line = total_goals_line(market_title)
        if line is not None:
            return f"Over {line:g} goals"
        matchup = _totals_matchup(ticker)
        return f"{matchup} — Over goals" if matchup else (ticker or "Over goals")
    # Spreads (goal handicap): like totals, the favorite + line live in Kalshi's
    # stored title ("USA wins by more than 1.5 goals" → "USA -1.5"), captured at
    # import. The side (yes = covers, no = fades) is rendered by the frontend.
    if ticker and is_spread_ticker(ticker):
        label = spread_label(ticker, market_title, None, negate=False)
        return label or ticker
    if b.home_code is None or b.away_code is None:
        return ticker or "—"
    league = league_display_name(b.event_series) or b.event_series or "Soccer"
    home = b.home_name or b.home_code
    away = b.away_name or b.away_code
    # Selection: the team it's on by name/code, or "Draw" for the tie.
    sel_code = b.selection_code
    if sel_code == "TIE":
        selection = "Draw"
    elif sel_code == b.home_code:
        selection = home
    elif sel_code == b.away_code:
        selection = away
    else:
        selection = sel_code or "?"
    return f"{league} — {home} v {away} — {selection}"


def _bet_to_dict(
    b: Bet,
    ticker: str | None,
    market_status: str | None = None,
    leg_count: int = 0,
    missed_count: int = 0,
    leg_sport: str | None = None,
    market_title: str | None = None,
    market_settlement: str | None = None,
) -> dict[str, Any]:
    fees_cents = (b.entry_fees_cents or 0) + (b.exit_fees_cents or 0)
    # Show running net PnL on partial closes. pnl_cents is None while the
    # bet is OPEN; realized_pnl_cents holds the running total as sells
    # close shares. Once terminal, pnl_cents mirrors realized_pnl_cents.
    base_pnl = b.pnl_cents if b.pnl_cents is not None else b.realized_pnl_cents
    net_pnl = base_pnl - fees_cents if base_pnl is not None else None
    return {
        "id": b.id,
        "sport": b.sport,
        # For a combo whose legs are all one sport, the badge sport (e.g.
        # 'soccer' for an all-World-Cup parlay); None for a mixed parlay or a
        # non-combo bet. Display-only — `sport` stays 'combo'.
        "leg_sport": leg_sport,
        "ticker": ticker,
        "market_label": _market_label(b, ticker, leg_count, missed_count, market_title),
        "leg_count": leg_count,
        "missed_leg_count": missed_count,
        "market_status": market_status,
        # Which side the market resolved to (yes/no), or None if unsettled.
        # The held-to-settlement row uses this to show how the held shares
        # actually paid off (100¢ if the bet's side matches, else 0¢) —
        # distinct from the bet's won/lost status, which is net-P&L sign.
        "market_settlement": market_settlement,
        "market_id": b.market_id,
        "kalshi_order_id": b.kalshi_order_id,
        "side": b.side,
        "entry_price_cents": b.entry_price_cents,
        # Exact fractional avg entry (e.g. 57.71) derived from the exact
        # stake_cents — entry_price_cents is the floored whole-cent value and
        # loses sub-cent precision. realized PnL is computed against the exact
        # VWAP server-side, so this is purely a more honest display figure.
        "entry_price": (
            round(b.stake_cents / b.quantity, 2)
            if b.quantity > 0 else b.entry_price_cents
        ),
        "exit_price_cents": b.exit_price_cents,
        "quantity": b.quantity,
        "remaining_quantity": b.remaining_quantity,
        "stake_cents": b.stake_cents,
        "pnl_cents": b.pnl_cents,
        "realized_pnl_cents": b.realized_pnl_cents,
        "entry_fees_cents": b.entry_fees_cents,
        "exit_fees_cents": b.exit_fees_cents,
        "fees_cents": fees_cents,
        "net_pnl_cents": net_pnl,
        "status": b.status,
        "exit_type": b.exit_type,
        "source": b.source,
        "strategy": b.strategy,
        "confidence": b.confidence,
        "timing": b.timing,
        "human_reasoning": b.human_reasoning,
        "ai_reasoning": b.ai_reasoning,
        "tags": b.tags,
        "metadata_edited_at": utc_iso(b.metadata_edited_at),
        "placed_at": utc_iso(b.placed_at),
        "settled_at": utc_iso(b.settled_at),
        "created_at": utc_iso(b.created_at),
    }


def _apply_filters(
    stmt: Select[Any],
    *,
    sport: list[str] | None,
    status: list[str] | None,
    strategy: list[str] | None,
    source: list[str] | None,
    timing: list[str] | None,
    market_ticker: str | None,
    since: datetime | None,
    until: datetime | None,
) -> Select[Any]:
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
        # Filter by a scalar subquery on market_id, NOT a second JOIN. list_bets
        # already LEFT-joins Market for its select columns; a second JOIN here
        # produced an ambiguous `market.kalshi_ticker` that 500'd every ?market=
        # request. The subquery resolves the ticker→id independently of whether
        # the outer query joined Market.
        market_subq = (
            select(Market.id).where(Market.kalshi_ticker == market_ticker).scalar_subquery()
        )
        stmt = stmt.where(Bet.market_id == market_subq)
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
    stmt = select(
        Bet, Market.kalshi_ticker, Market.status, Market.title, Market.settlement
    ).join(
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
    # Order by placed_at, newest first. placed_at is stored as text in SQLite
    # and mixes two formats (older rows have an ISO `T` and `+00:00` suffix,
    # newer rows a space and no tz), so a raw text ORDER BY scrambles them —
    # but julianday() parses every format to a real number and sorts correctly.
    # id is the tiebreaker for rows sharing a timestamp. This (vs the old id-only
    # sort) puts a manually-edited/backdated placed_at in its true chronological
    # spot instead of its insert position.
    order_key = func.julianday(Bet.placed_at)
    if cursor is not None:
        # Keyset pagination matching the sort: continue strictly after the
        # cursor row in (placed_at DESC, id DESC) order. We carry the cursor as
        # the row id and look up its placed_at so the cursor stays a simple int.
        cursor_pa = await session.scalar(select(Bet.placed_at).where(Bet.id == cursor))
        if cursor_pa is not None:
            cursor_key = func.julianday(cursor_pa)
            stmt = stmt.where(
                (order_key < cursor_key)
                | ((order_key == cursor_key) & (Bet.id < cursor))
            )
    stmt = stmt.order_by(order_key.desc(), Bet.id.desc()).limit(limit + 1)

    rows = (await session.execute(stmt)).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    combo_ids = [b.id for b, _t, _s, _ti, _se in rows if b.sport == Sport.COMBO]
    leg_counts, missed_counts = await _combo_counts(session, combo_ids)
    leg_sports = await _combo_leg_sports(session, combo_ids)
    out = [
        _bet_to_dict(
            b, ticker, market_status,
            leg_counts.get(b.id, 0), missed_counts.get(b.id, 0),
            leg_sports.get(b.id), market_title, market_settlement,
        )
        for b, ticker, market_status, market_title, market_settlement in rows
    ]
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
    return await compute_ledger_stats(
        session,
        sport=sport or None,
        status=status or None,
        strategy=strategy or None,
        source=source or None,
        timing=timing or None,
        market=market,
        since=since,
        until=until,
    )


# Literal sub-path — declared before the /ledger/{bet_id}/... routes so it can
# never be shadowed if a bare /ledger/{bet_id} route is ever added.
@router.get("/ledger/tags")
async def list_tags(
    session: AsyncSession = Depends(get_session),
) -> dict[str, list[str]]:
    """Distinct tag strings across all bets — autocomplete source for the
    edit panel. Tags live inside a JSON column, so we flatten in Python
    rather than reach for a DB-specific JSON operator."""
    rows = (await session.execute(select(Bet.tags))).scalars().all()
    seen: set[str] = set()
    for row in rows:
        if not row:
            continue
        # row is the JSON column value — a list[str], or None when unset.
        for t in row:
            if t and t.strip():
                seen.add(t.strip())
    return {"tags": sorted(seen)}


async def compute_ledger_stats(
    session: AsyncSession,
    *,
    sport: list[str] | None = None,
    status: list[str] | None = None,
    strategy: list[str] | None = None,
    source: list[str] | None = None,
    timing: list[str] | None = None,
    market: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> dict[str, Any]:
    """Plain-callable core of /ledger/stats — same numbers, no FastAPI Query()
    params, so the partner-context endpoint (and any service) can reuse it
    without going through the HTTP layer. The route is a thin wrapper."""
    # Build each aggregate query directly with the same filter chain. The
    # previous shape — `select(...).select_from(base.subquery())` — produced
    # a cartesian product between `bet` and the subquery because the
    # aggregate referenced Bet.status directly, which SQLAlchemy resolved
    # against the `bet` table, not the subquery. Inflated total_bets by 3x.
    def _filtered(stmt: Select[Any]) -> Select[Any]:
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
    # Fees come from Kalshi's authoritative per-fill fee_cost summed into
    # bet.entry_fees_cents / exit_fees_cents (default 0, never NULL).
    #
    # Count fees ONLY on settled bets (pnl_cents IS NOT NULL), to match the
    # realized scope of pnl_cents. Summing fees over ALL bets — including OPEN
    # ones whose pnl_cents is null — subtracted open positions' entry fees from
    # net P&L while their P&L contributed nothing, making the Net P&L stat read
    # worse than the realized result on the PnL chart (which is settled-only).
    settled_entry_fees = func.coalesce(func.sum(
        case((Bet.pnl_cents.isnot(None), Bet.entry_fees_cents), else_=0)
    ), 0)
    settled_exit_fees = func.coalesce(func.sum(
        case((Bet.pnl_cents.isnot(None), Bet.exit_fees_cents), else_=0)
    ), 0)
    agg = (
        await session.execute(
            _filtered(select(
                func.coalesce(func.sum(Bet.pnl_cents), 0),
                func.coalesce(func.sum(Bet.stake_cents), 0),
                func.count(Bet.id),
                settled_entry_fees,
                settled_exit_fees,
            ))
        )
    ).first()
    total_pnl_cents = int(agg[0]) if agg else 0
    total_stake_cents = int(agg[1]) if agg else 0
    total_bets = int(agg[2]) if agg else 0
    total_fees_cents = (int(agg[3]) if agg else 0) + (int(agg[4]) if agg else 0)
    total_net_pnl_cents = total_pnl_cents - total_fees_cents

    won = by_status.get("won", 0)
    lost = by_status.get("lost", 0)
    settled = won + lost
    win_rate = (won / settled) if settled else None
    roi = (total_pnl_cents / total_stake_cents) if total_stake_cents else None
    net_roi = (
        total_net_pnl_cents / total_stake_cents if total_stake_cents else None
    )

    # Per-strategy breakdown for StrategyBreakdown chart. Same settled-only fee
    # scope as the totals above, so per-strategy net ROI is consistent with the
    # headline Net P&L.
    strategy_rows = (
        await session.execute(
            _filtered(select(
                Bet.strategy,
                func.count(Bet.id),
                func.coalesce(func.sum(Bet.pnl_cents), 0),
                func.coalesce(func.sum(Bet.stake_cents), 0),
                settled_entry_fees,
                settled_exit_fees,
            ).group_by(Bet.strategy))
        )
    ).all()
    by_strategy = []
    for row in strategy_rows:
        pnl = int(row[2])
        stake = int(row[3])
        fees = int(row[4]) + int(row[5])
        by_strategy.append({
            "strategy": row[0],
            "count": int(row[1]),
            "pnl_cents": pnl,
            "stake_cents": stake,
            "fees_cents": fees,
            "net_pnl_cents": pnl - fees,
            "roi": (pnl / stake) if stake else None,
            "net_roi": ((pnl - fees) / stake) if stake else None,
        })

    return {
        "total_bets": total_bets,
        "by_status": by_status,
        "total_pnl_cents": total_pnl_cents,
        "total_stake_cents": total_stake_cents,
        "total_fees_cents": total_fees_cents,
        "total_net_pnl_cents": total_net_pnl_cents,
        "win_rate": win_rate,
        "roi": roi,
        "net_roi": net_roi,
        "by_strategy": by_strategy,
    }


@router.get("/ledger/{bet_id}/legs")
async def list_bet_legs(
    bet_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Per-leg drill-down for a combo bet, in Kalshi's leg order. Empty for a
    non-combo bet (no legs attached)."""
    rows = (await session.execute(
        select(ComboLeg)
        .where(ComboLeg.bet_id == bet_id)
        .order_by(ComboLeg.leg_index.asc())
    )).scalars().all()
    return {
        "bet_id": bet_id,
        "legs": [
            {
                "leg_index": leg.leg_index,
                "title": leg.leg_title,
                "ticker": leg.leg_ticker,
                "event_ticker": leg.leg_event_ticker,
                "side": leg.side,
                "result": leg.result,
            }
            for leg in rows
        ],
    }


@router.get("/ledger/{bet_id}/fills")
async def list_bet_fills(
    bet_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Per-fill drill-down for one bet. Returns every Kalshi fill attached
    to this bet in chronological order — buy fills first, then sells. Each
    row has Kalshi's authoritative fee_cents (NULL until the fills-sync
    sweep populates it from REST)."""
    rows = (await session.execute(
        select(BetFill)
        .where(BetFill.bet_id == bet_id)
        .order_by(BetFill.created_time.asc().nulls_last(), BetFill.id.asc())
    )).scalars().all()
    return {
        "bet_id": bet_id,
        "fills": [
            {
                "id": f.id,
                "trade_id": f.trade_id,
                "order_id": f.order_id,
                "ticker": f.ticker,
                "side": f.side,
                "action": f.action,
                "price_cents": f.price_cents,
                "quantity_centi": f.quantity_centi,
                "quantity": f.quantity_centi // 100,
                "fee_cents": f.fee_cents,
                "is_taker": f.is_taker,
                "fee_synced_at": utc_iso(f.fee_synced_at),
                "created_time": utc_iso(f.created_time),
            }
            for f in rows
        ],
    }


# Canonical post-mortem order, derived from the enum's member order so it
# can't drift from SnapshotPhase.
_SNAPSHOT_PHASE_ORDER = {p.value: i for i, p in enumerate(SnapshotPhase)}


@router.get("/ledger/{bet_id}/snapshots")
async def list_bet_snapshots(
    bet_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Trade snapshots for one bet — the frozen run-of-play at entry and at
    exit, for exit post-mortems. Ordered entry → exit_open → exit_close.

    A closed bet that scaled out (sold part at 75', rest at 90') has distinct
    exit_open and exit_close minutes; a clean single-sell exit has them equal;
    a bet ridden to settlement has neither exit_* (it never sold). A pre-match
    entry has a null run_of_play with just the market mid. The reader treats
    missing phases as the finding, not an error."""
    rows = (await session.execute(
        select(TradeSnapshot).where(TradeSnapshot.bet_id == bet_id)
    )).scalars().all()
    ordered = sorted(rows, key=lambda s: _SNAPSHOT_PHASE_ORDER.get(s.phase, 99))
    return {
        "bet_id": bet_id,
        "snapshots": [
            {
                "id": s.id,
                "phase": s.phase,
                "captured_at": utc_iso(s.captured_at),
                "game_clock": s.game_clock,
                "score_home": s.score_home,
                "score_away": s.score_away,
                "run_of_play": s.run_of_play_json,
                "market_mid_cents": s.market_mid_cents,
                "price_history": s.price_history_json,
            }
            for s in ordered
        ],
    }


class ForceSettleBody(BaseModel):
    settlement_value_cents: int = Field(ge=0, le=100)


@router.post("/ledger/{bet_id}/force-settle")
async def force_settle_bet(
    bet_id: int,
    body: ForceSettleBody,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Escape hatch when a market settled outside our detection paths.

    Drives the same settle_bets_for_market path the WS lifecycle and the
    sweeper use — single code path. Settles every OPEN bet on the same
    market (Kalshi resolves a market as a whole, not per-bet).

    settlement_value_cents = YES-side payoff (0 = NO won, 100 = YES won,
    50 = void/refund).
    """
    bet = await session.get(Bet, bet_id)
    if bet is None:
        raise HTTPException(404, "bet not found")
    if bet.status != BetStatus.OPEN:
        raise HTTPException(409, f"bet is {bet.status}, not open")

    market = await session.get(Market, bet.market_id)
    if market is None:
        raise HTTPException(500, "bet has no market row")

    settled = await settle_bets_for_market(
        session,
        ticker=market.kalshi_ticker,
        settlement_value_cents=body.settlement_value_cents,
    )
    await session.commit()
    return {
        "settled_count": settled,
        "ticker": market.kalshi_ticker,
        "settlement_value_cents": body.settlement_value_cents,
    }


# === Import from Kalshi ==================================================
#
# Place a bet directly on kalshi.com, then hand-pick it into the ledger here.
# This is the manual counterpart to the deliberately-unwired auto-reconcile
# path (feedback_no_external_fill_reconciliation): the app never silently
# mirrors the Kalshi account, but it imports the fills the user explicitly
# selects. Buys only — a sell isn't an entry; closed-loop matching is out of
# scope (same as the combo log path).

_IMPORT_WINDOW_DAYS = 14
"""How far back importable fills are scanned. The user's framing: "whats not on
our ledger within the last ~2 weeks". Bounds the /portfolio/fills pull."""


def _importable_label(ticker: str, side: BetSide, feed: Any) -> str | None:
    """Readable outcome label for an importable fill. Prefers the live discovery
    feed (full team names); falls back to the codes parsed from the ticker for
    resolved / aged-out markets the feed no longer carries — which is most of an
    import list, so a feed-only label would leave them all as raw tickers.

    The feed's yes_sub_title is the YES outcome, so the label flips with side.
    None only when the ticker is neither in the feed nor a parseable per-game
    ticker (e.g. a futures derivative) — UI then falls back to the raw ticker."""
    label = _feed_label(ticker, side, feed)
    if label is not None:
        return label
    return _ticker_label(ticker, side)


def _feed_label(ticker: str, side: BetSide, feed: Any) -> str | None:
    """Label from the live discovery feed (full team names), or None if the
    ticker isn't currently in it."""
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
    if is_spread_ticker(ticker):
        return spread_label(ticker, sub, row.event_title, negate=negate)
    if sub.lower() in ("tie", "draw"):
        base = f"{row.event_title.replace(' vs ', ' - ')} DRAW"
        return f"{base.removesuffix(' DRAW')} NOT DRAW" if negate else base
    if not sub:
        return None
    return f"{sub} NOT WIN" if negate else f"{sub} WIN"


_TOTALS_MATCHUP_RE = re.compile(r"-\d{2}[A-Z]{3}\d{2}([A-Z]{3})([A-Z]{3})-\d+$")


def _totals_matchup(ticker: str) -> str | None:
    """The 'HOME - AWAY' codes from a totals ticker's matchup block, or None if
    it doesn't match. Same {date}{HOME}{AWAY} block as a game ticker; only the
    suffix (a numeric line slot, not a 3-letter selection) differs."""
    m = _TOTALS_MATCHUP_RE.search(ticker)
    return f"{m.group(1)} - {m.group(2)}" if m else None


def _ticker_label(ticker: str, side: BetSide) -> str | None:
    """Label from the ticker alone (3-letter codes), for markets the feed has
    dropped: 'Intl Friendly — VEN v TUR — Draw'. None for an unparseable ticker.
    Mirrors the ledger's code-fallback label so an imported bet reads the same
    in the picker and the ledger row."""
    parsed = parse_market_ticker(ticker)
    if parsed is None:
        # Totals ticker (no 3-letter selection, so parse_market_ticker rejects
        # it): the line isn't recoverable from the ticker — it lives in Kalshi's
        # label, which an aged-out market no longer carries. Identify it as
        # Over/Under on the matchup so the row isn't a raw ticker.
        if is_total_goals_ticker(ticker):
            matchup = _totals_matchup(ticker)
            ou = "Under" if side == BetSide.NO else "Over"
            return f"{matchup} — {ou} goals" if matchup else None
        # Spread for a market the feed has dropped: the line lives in the title,
        # which an aged-out market no longer carries, so show favorite + side
        # without it (the ledger row backfills the line from the stored title).
        if is_spread_ticker(ticker):
            return spread_label(ticker, None, None, negate=side == BetSide.NO)
        return None
    league = league_display_name(parsed.series) or parsed.series
    matchup = f"{parsed.home_code} v {parsed.away_code}"
    if parsed.selection_code == "TIE":
        selection = "Draw"
    elif parsed.selection_code == parsed.home_code:
        selection = parsed.home_code
    elif parsed.selection_code == parsed.away_code:
        selection = parsed.away_code
    else:
        selection = parsed.selection_code
    base = f"{league} — {matchup} — {selection}"
    # NO = a bet against the outcome (mirrors the YES/NO flip the feed label uses).
    return f"{base} (NO)" if side == BetSide.NO else base


class ImportablePosition(BaseModel):
    """One unlinked Kalshi position — all buys + closing sells on a (ticker, held
    side) folded together — offered for import. `held_quantity` is what's still
    open (bought − sold); `realized_pnl_cents` is the closed-portion P&L already
    banked. `resolved` flags a market that has settled; on import a still-open
    remainder lands terminal with that result. `result` is the YES-side outcome
    (yes/no) when resolved, for the picker's ✓/✗."""

    key: str  # "{ticker}:{side}" — the selection + import key
    ticker: str
    label: str | None
    side: BetSide
    bought_quantity: int
    held_quantity: int
    entry_price_cents: int
    exit_price_cents: int | None
    realized_pnl_cents: int | None
    placed_at: str | None
    resolved: bool
    result: str | None


def _held_price(fill: RestFill, side: BetSide) -> int:
    """The fill's price in the held side's frame. Kalshi's fill carries BOTH
    complementary prices (yes_price + no_price); a position held YES always
    reads yes_price, held NO reads no_price — for buys AND closing sells. This
    is the fix for the sell-misprice bug: a YES position closed via Kalshi's
    `sell no @ 7¢` is the `sell yes @ 93¢` it actually was."""
    return fill.yes_price if side == BetSide.YES else fill.no_price


async def _resolved_settlement_cents(
    client: KalshiRestClient, ticker: str
) -> int | None:
    """Settlement value (cents) for a ticker, or None if it isn't resolved yet.

    Two guards, both money-safety-critical, so this lives in ONE place that
    both the import picker (display) and the import commit (books real P&L) call:
      * Skip any row whose ticker != ours. Kalshi's server-side filter is
        trusted but not load-bearing — a loose filter must never tag a position
        with a FOREIGN market's outcome.
      * Skip a None-valued (scalar / not-yet-normalized) row and keep scanning.
        The latest settlement row can exist before it carries a value; reading
        limit=1 would let that None block discovery of a real valued row. Mirrors
        settlement_sweeper's skip-and-retry. Returns None when none is valued
        yet, leaving the position OPEN for the sweeper.
    """
    try:
        resp = await client.get_settlements(ticker=ticker, limit=10)
    except Exception as e:  # noqa: BLE001
        log.warning("settlement_check_failed", ticker=ticker, error=str(e)[:120])
        return None
    for row in resp.settlements:
        if row.ticker != ticker:
            continue
        if row.settlement_value_cents is not None:
            return row.settlement_value_cents
    return None


async def _market_title(client: KalshiRestClient, ticker: str) -> str | None:
    """Kalshi's human market title ("USA wins by more than 1.5 goals"), where a
    totals/spread line is the only authoritative source. None on any error — the
    caller falls back to a line-less label rather than failing the import."""
    try:
        data = await client.get_market(ticker)
    except Exception as e:  # noqa: BLE001
        log.warning("market_title_fetch_failed", ticker=ticker, error=str(e)[:120])
        return None
    title = data.get("market", {}).get("title")
    return title if isinstance(title, str) else None


async def _gather_positions(
    client: KalshiRestClient, min_ts: int
) -> dict[tuple[str, str], list[RestFill]]:
    """Group every per-game fill in the window by (ticker, held side).

    Held side = the side of the BUYS (what the position is). All fills on that
    ticker whose held-side frame matches a buy belong to the position: a buy is
    its own held side; a closing sell is reported by Kalshi on the OPPOSITE side
    (`sell no` closes a YES hold), so its held side is the complement of the
    reported side. We bucket each fill under the held side and keep its raw Fill
    (carrying both prices) for the importer to price exactly.
    """
    positions: dict[tuple[str, str], list[RestFill]] = {}
    cursor: str | None = None
    while True:
        resp = await client.get_fills(cursor=cursor, min_ts=min_ts)
        for f in resp.fills:
            # Per-game soccer only: match-result moneylines AND total-goals
            # Over/Unders. Excludes combos (own log flow) and futures (deci-cent
            # priced — can't be stored in the whole-cent money core).
            if (
                not is_tradeable_ticker(f.ticker)
                or not is_per_game_soccer_ticker(f.ticker)
            ):
                continue
            # A buy holds its own side; a sell closes the OPPOSITE side it
            # reports. So the position's held side is the buy's side or the
            # sell's complement.
            if f.action == "buy":
                held = f.side
            else:
                held = "no" if f.side == "yes" else "yes"
            positions.setdefault((f.ticker, held), []).append(f)
        cursor = resp.cursor or None
        if not cursor:
            break
    return positions


@router.get("/ledger/importable")
async def list_importable(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Scan recent Kalshi fills for tradeable positions not yet in the ledger.

    Ephemeral by design — recomputed each call, never cached. Pulls
    /portfolio/fills for the last ~2 weeks (cross-market isolation: only
    parseable per-game soccer tickers), folds each (ticker, held side) into one
    position with its true blended entry, exit, realized P&L, and held quantity,
    and drops any (ticker, side) already in the ledger. Each is tagged
    held/resolved so the picker shows outcomes for finished markets.
    """
    min_ts = int(
        (datetime.now(timezone.utc) - timedelta(days=_IMPORT_WINDOW_DAYS)).timestamp()
    )
    async with KalshiRestClient() as client:
        positions = await _gather_positions(client, min_ts)
        if not positions:
            return {"positions": []}

        keys = [f"external-single:{t}:{s}" for (t, s) in positions]
        linked = set((await session.execute(
            select(Bet.client_order_id).where(Bet.client_order_id.in_(keys))
        )).scalars().all())

        supervisor = getattr(request.app.state, "supervisor", None)
        feed = (
            supervisor.market_discovery.get_feed()
            if supervisor is not None else None
        )

        unlinked = {
            k: v for k, v in positions.items()
            if f"external-single:{k[0]}:{k[1]}" not in linked
        }
        # Settlement status per distinct ticker (one query each — small set).
        resolved_result: dict[str, str] = {}
        for ticker in {t for (t, _s) in unlinked}:
            cents = await _resolved_settlement_cents(client, ticker)
            if cents is not None:
                resolved_result[ticker] = "yes" if cents >= 50 else "no"

    out: list[ImportablePosition] = []
    for (ticker, side_str), fills in unlinked.items():
        side = BetSide(side_str)
        buys = [f for f in fills if f.action == "buy"]
        sells = [f for f in fills if f.action == "sell"]
        bought_centi = sum(f.count_centi for f in buys)
        if bought_centi < 100:
            # No whole-contract buy on this side (e.g. only a stray sell, or a
            # sub-contract residual). Nothing importable as a position.
            continue
        sold_centi = sum(f.count_centi for f in sells)
        entry_centi = sum(_held_price(f, side) * f.count_centi for f in buys)
        entry = max(1, min(99, entry_centi // bought_centi))
        exit_price: int | None = None
        realized: int | None = None
        if sold_centi > 0:
            sell_w = sum(_held_price(f, side) * f.count_centi for f in sells)
            exit_price = max(1, min(99, sell_w // sold_centi))
            realized = (
                sell_w - (entry_centi * sold_centi) // bought_centi
            ) // 100
        out.append(ImportablePosition(
            key=f"{ticker}:{side_str}",
            ticker=ticker,
            label=_importable_label(ticker, side, feed),
            side=side,
            bought_quantity=bought_centi // 100,
            held_quantity=max(0, (bought_centi - sold_centi) // 100),
            entry_price_cents=entry,
            exit_price_cents=exit_price,
            realized_pnl_cents=realized,
            placed_at=utc_iso(min(f.created_time for f in buys)),
            resolved=ticker in resolved_result,
            result=resolved_result.get(ticker),
        ))
    out.sort(key=lambda r: r.placed_at or "", reverse=True)
    return {"positions": [r.model_dump() for r in out]}


class ImportBody(BaseModel):
    keys: list[str] = Field(min_length=1, max_length=50)
    """Each key is "{ticker}:{side}" from the importable list."""


@router.post("/ledger/import")
async def import_fills(
    body: ImportBody,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Import the hand-picked Kalshi positions into the ledger as EXTERNAL bets.

    Re-gathers every fill from Kalshi (never trusts client-supplied prices — the
    request carries only "{ticker}:{side}" keys), folds each picked position's
    buys + closing sells into one bet via record_external_position, then — if the
    market has settled and a held remainder is still open — drives the same
    settle_bets_for_market path the sweeper uses, in the same transaction, so a
    resolved remainder lands terminal with no transient OPEN window.

    The supervisor's ledger lock serializes the write+settle against the WS
    handlers (lost-update safety). Idempotent and self-healing on (ticker, side):
    re-importing rebinds the fills and recomputes.
    """
    supervisor = getattr(request.app.state, "supervisor", None)
    lock = supervisor._ledger_write_lock if supervisor is not None else None

    min_ts = int(
        (datetime.now(timezone.utc) - timedelta(days=_IMPORT_WINDOW_DAYS)).timestamp()
    )
    async with KalshiRestClient() as client:
        positions = await _gather_positions(client, min_ts)

        imported: list[dict[str, Any]] = []
        skipped: list[str] = []
        for key in body.keys:
            # key = "{ticker}:{side}". Split on the LAST colon — tickers contain
            # no colon, but split-from-right is robust regardless.
            ticker, _, side_str = key.rpartition(":")
            raw_fills = positions.get((ticker, side_str))
            if raw_fills is None or side_str not in ("yes", "no"):
                skipped.append(key)
                continue
            side = BetSide(side_str)
            fills = [
                ExternalFillInput(
                    trade_id=f.trade_id,
                    order_id=f.order_id,
                    action=f.action,
                    held_price_cents=_held_price(f, side),
                    quantity_centi=f.count_centi,
                    fee_cents=f.fee_cents,
                    created_time=f.created_time,
                )
                for f in raw_fills
            ]
            if not any(f.action == "buy" for f in fills):
                skipped.append(key)
                continue

            # Settlement status — fetched outside the lock (network).
            settle_value = await _resolved_settlement_cents(client, ticker)
            # Totals/spread lines live only in Kalshi's market title — fetch it
            # so the ledger label can show the line. Moneylines label off the
            # ticker codes, so skip the extra call for them.
            market_title = (
                await _market_title(client, ticker)
                if is_total_goals_ticker(ticker) or is_spread_ticker(ticker)
                else None
            )

            async with ledger_guard(lock):
                bet = await record_external_position(
                    session, ticker=ticker, side=side, fills=fills,
                    market_title=market_title,
                )
                # Settle only a still-open held remainder. A position fully
                # closed via sells is already terminal (CLOSED_EARLY) from
                # recompute — don't re-settle it.
                if (
                    settle_value is not None
                    and bet.status == BetStatus.OPEN
                    and bet.remaining_quantity_centi > 0
                ):
                    await settle_bets_for_market(
                        session, ticker=ticker,
                        settlement_value_cents=settle_value,
                    )
                await session.commit()
            await session.refresh(bet)
            imported.append({
                "bet_id": bet.id,
                "ticker": ticker,
                "side": bet.side,
                "quantity": bet.quantity,
                "held_quantity": bet.remaining_quantity,
                "entry_price_cents": bet.entry_price_cents,
                "exit_price_cents": bet.exit_price_cents,
                "status": bet.status,
                "pnl_cents": bet.pnl_cents,
                "realized_pnl_cents": bet.realized_pnl_cents,
            })

    return {"imported": imported, "skipped": skipped}


# === Reflective metadata edit ============================================
#
# A bet's "why" often crystallizes after the trade, not at placement. The
# fields below are user-reflective — they shape AI prompt context but
# never affect money math. Editable forever. Everything else (price,
# quantity, status, fees, PnL) is system-of-record and not touchable
# from this endpoint.

# Allow None on each field so the user can blank a memo or clear a typo'd
# tag list without deleting and re-tagging.
class MetadataPatch(BaseModel):
    strategy: Strategy | None = None
    source: BetSource | None = None
    timing: Timing | None = None
    confidence: Confidence | None = None
    tags: list[str] | None = None
    human_reasoning: str | None = None


@router.patch("/ledger/{bet_id}/metadata")
async def edit_bet_metadata(
    bet_id: int,
    body: MetadataPatch,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Edit reflective metadata on a bet. Money / state fields untouched.

    A field omitted from the request body is left as-is. Passing an
    explicit null clears that field (tags → empty list, memo → null).
    """
    bet = await session.get(Bet, bet_id)
    if bet is None:
        raise HTTPException(404, "bet not found")

    payload = body.model_dump(exclude_unset=True)
    if not payload:
        raise HTTPException(400, "no fields to update")

    if "strategy" in payload:
        bet.strategy = payload["strategy"]
    if "source" in payload:
        bet.source = payload["source"]
    if "timing" in payload:
        bet.timing = payload["timing"]
    if "confidence" in payload:
        bet.confidence = payload["confidence"]
    if "tags" in payload:
        # Normalize to a JSON list. Empty list → store [] so the column
        # reads consistently rather than flipping between [] and null.
        raw = payload["tags"] or []
        cleaned = [t.strip() for t in raw if t and t.strip()]
        # De-dup, preserve first-seen order.
        seen: set[str] = set()
        deduped: list[str] = []
        for t in cleaned:
            if t not in seen:
                seen.add(t)
                deduped.append(t)
        bet.tags = deduped
    if "human_reasoning" in payload:
        memo = payload["human_reasoning"]
        bet.human_reasoning = memo.strip() if memo else None

    bet.metadata_edited_at = datetime.now(timezone.utc)
    bet.version = (bet.version or 1) + 1

    market = await session.get(Market, bet.market_id)
    await session.commit()
    await session.refresh(bet)
    leg_count = 0
    missed_count = 0
    leg_sport: str | None = None
    if bet.sport == Sport.COMBO:
        legs, missed = await _combo_counts(session, [bet.id])
        leg_count = legs.get(bet.id, 0)
        missed_count = missed.get(bet.id, 0)
        leg_sport = (await _combo_leg_sports(session, [bet.id])).get(bet.id)
    return _bet_to_dict(
        bet,
        market.kalshi_ticker if market else None,
        market.status if market else None,
        leg_count,
        missed_count,
        leg_sport,
        market.title if market else None,
        market.settlement if market else None,
    )


