"""Partner API — the terminal cockpit's read/write surface.

The AI partner is a Claude Code terminal session (see .claude/skills/
lutz-partner/). It has no programmatic LLM anywhere in this codebase; it
reaches the app only through these localhost HTTP endpoints.

This module is the partner's data plane:
  GET  /partner/context[?event=]   one-call read of everything it reasons on
  POST /partner/suggestions        write an entry/exit suggestion → amber card

The context endpoint deliberately composes from the *same* serializers the
dashboard uses (events.get_event, positions.list_positions,
ledger._bet_to_dict) rather than re-deriving — so the partner sees byte-for-
byte the numbers the site shows. Single source of truth: if /positions says a
position is +52%, /partner/context says +52%, because it's the same code.

The write endpoint creates SUGGESTION rows (kind entry|exit) and broadcasts a
`suggestion` app event so the browser surfaces an amber card. It is the only
thing the partner can do beyond reading — and even then it cannot place an
order: a suggestion is a staged card the human still confirms.

Cross-market isolation is enforced/inherited on every path: soccer tickers
only, never non-soccer positions or markets.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.db import get_session
from src.core.logging import get_logger
from src.core.types import (
    BetSide,
    Confidence,
    Sport,
    Strategy,
    SuggestionKind,
    SuggestionStatus,
    Urgency,
    utc_iso,
)
from src.api.routes.events import get_event
from src.api.routes.ledger import _bet_to_dict, compute_ledger_stats
from src.api.routes.positions import list_positions
from src.models import Bet, Market, Position, Suggestion
from src.sports.soccer import is_soccer_ticker

router = APIRouter()
log = get_logger(__name__)

RECENT_TRADES_LIMIT = 100
"""How many recent bets the partner gets as ledger context — enough detail to
read the recent arc, while history_stats carries the all-time aggregates."""


async def _recent_trades(session: AsyncSession) -> list[dict[str, Any]]:
    """Last N bets, newest first — same select shape + serializer as
    GET /ledger (Bet left-joined to Market, ordered by id desc, rendered by
    _bet_to_dict). Querying directly rather than calling list_bets() because
    that handler's params are FastAPI Query() defaults that only resolve
    through the HTTP layer, not a plain function call."""
    stmt = (
        select(Bet, Market.kalshi_ticker, Market.status)
        .join(Market, Market.id == Bet.market_id, isouter=True)
        .order_by(Bet.id.desc())
        .limit(RECENT_TRADES_LIMIT)
    )
    rows = (await session.execute(stmt)).all()
    return [_bet_to_dict(b, ticker, status) for b, ticker, status in rows]


def _bankroll_cents(request: Request) -> int | None:
    """The single bankroll source: app.state.kalshi_balance_cents, refreshed by
    health.refresh_balance() on a ~10s TTL. May be slightly stale; fine for a
    human-in-the-loop read. There is no balance service to call."""
    return getattr(request.app.state, "kalshi_balance_cents", None)


def _price_series(request: Request, ticker: str | None) -> list[dict[str, Any]]:
    """Recent mid trajectory for a market, oldest first, as
    [{"mid_cents": int}, …]. Empty when the buffer has nothing yet (just
    subscribed / just restarted) or no buffer exists (tests). Best-effort like
    the bankroll read — never raises. We drop the monotonic timestamp on the
    wire: it's process-relative and meaningless to a reader; order + cadence
    carry the trajectory."""
    if ticker is None:
        return []
    ph = getattr(request.app.state, "price_history", None)
    if ph is None:
        return []
    return [{"mid_cents": mid} for _, mid in ph.series(ticker)]


@router.get("/partner/context")
async def partner_context(
    request: Request,
    event: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """One-call context package for the partner.

    With `?event=`: run-of-play backbone + that event's child markets +
    per-side positions (via the same handler EventView uses), plus the
    global open-position list, recent trades, and bankroll.

    Without `event`: the global book — all open positions + recent trades +
    bankroll, no run-of-play (the partner asks about a specific game when it
    wants the live read).

    Soccer-only and read-only, inherited from the composed handlers. An
    unknown/non-soccer event ticker is refused by get_event (400/404), which
    propagates unchanged — the partner must not see non-soccer state.
    """
    positions = await list_positions(request, session)
    # Attach each position's recent mid trajectory so the partner reads the tape,
    # not just the snapshot ("47 climbing from 30" vs "falling from 55").
    for p in positions["positions"]:
        p["price_history"] = _price_series(request, p.get("ticker"))

    out: dict[str, Any] = {
        "scope": "event" if event else "book",
        "bankroll_cents": _bankroll_cents(request),
        "positions": positions["positions"],
        "recent_trades": await _recent_trades(session),
        # Aggregate behavioral history so the partner can spot patterns the last
        # 20 trades don't show — win-rate and net P&L overall AND per strategy.
        # Same single source the Settings/Ledger charts use (ledger_stats), so
        # the numbers match the site. This is what lets LUTZ say "your draw bets
        # are -EV over 30 trades" instead of eyeballing recent rows.
        "history_stats": await compute_ledger_stats(session),
    }

    if event is not None:
        # Reuse the exact EventView data path — same run-of-play, same child
        # markets, same per-side position embedding the site renders. Errors
        # (non-soccer 400, not-in-cache 404, supervisor-down 503) propagate.
        ev = await get_event(event, request, session)
        for m in ev.get("markets", []):
            m["price_history"] = _price_series(request, m.get("ticker"))
        out["event"] = ev
        # Recent news tagged to THIS game's teams — injuries, lineups,
        # suspensions LUTZ should factor into the read. Scoped to the two teams
        # so it's signal, not the whole board.
        out["event_news"] = _news_for_event(request, ev)

    return out


def _news_for_event(request: Request, event_payload: dict[str, Any]) -> list[dict[str, Any]]:
    """WC news tagged to either team in this game. Empty when no news poller, no
    match, or non-WC teams. Headline + published + url — enough for LUTZ to read
    and ask the user about."""
    news = getattr(request.app.state, "espn_news", None)
    if news is None:
        return []
    live = event_payload.get("live") or {}
    teams = {n for n in (live.get("home_name"), live.get("away_name")) if n}
    if not teams:
        return []
    return [
        {"headline": a.headline, "published": utc_iso(a.published), "url": a.url}
        for a in news.for_teams(teams)
    ]


# === Write: suggestions ===================================================


class SuggestionBody(BaseModel):
    """What the terminal partner POSTs to stage an amber card. Validated hard:
    the partner is trusted code, but a typo'd price or side is a real-money
    foot-gun and a 422 is cheaper than a bad card."""

    kind: SuggestionKind
    ticker: str
    side: BetSide
    suggested_price_cents: int = Field(ge=1, le=99)
    suggested_size_cents: int = Field(ge=0)
    strategy: Strategy
    justification: str = Field(min_length=1)
    confidence: Confidence
    urgency: Urgency = Urgency.MEDIUM
    kelly_fraction_bps: int | None = None
    estimated_edge_bps: int | None = None
    ai_probability_pct: int | None = Field(default=None, ge=0, le=100)
    market_probability_pct: int | None = Field(default=None, ge=0, le=100)
    expires_at: datetime | None = None


@router.post("/partner/suggestions")
async def create_suggestion(
    body: SuggestionBody,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Create a pending entry|exit suggestion and broadcast it to the browser.

    Guards, in order:
      - soccer-only ticker (cross-market isolation)
      - ticker resolves to a known market (a suggestion on a phantom market is
        a bug; we do NOT auto-create markets here)
      - kind=exit must reference a held (ticker, side) position. This is a
        bug-guard, not a risk gate (mirrors the order route's ghost-share
        philosophy): the partner shouldn't propose selling something you don't
        hold. The load-bearing race guard is /orders/place at execution time;
        a position can still close after this passes, which the frontend hides
        and /orders/place ultimately refuses.

    No order is placed. The row lands status=pending and surfaces as an amber
    card the human confirms.
    """
    if not is_soccer_ticker(body.ticker):
        raise HTTPException(status_code=400, detail=f"{body.ticker} is not a soccer market")

    market_id = await session.scalar(
        select(Market.id).where(Market.kalshi_ticker == body.ticker)
    )
    if market_id is None:
        raise HTTPException(
            status_code=404,
            detail=f"no market {body.ticker} in the ledger — can't suggest on it",
        )

    if body.kind is SuggestionKind.EXIT:
        held = await session.scalar(
            select(Position.quantity)
            .where(Position.market_id == market_id, Position.side == body.side)
        )
        if not held:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"exit suggestion for {body.side.value.upper()} {body.ticker} "
                    f"but no such position is held"
                ),
            )

    suggestion = Suggestion(
        sport=Sport.SOCCER,
        market_id=market_id,
        kind=body.kind,
        side=body.side,
        suggested_price_cents=body.suggested_price_cents,
        suggested_size_cents=body.suggested_size_cents,
        strategy=body.strategy,
        justification=body.justification,
        confidence=body.confidence,
        urgency=body.urgency,
        status=SuggestionStatus.PENDING,
        kelly_fraction_bps=body.kelly_fraction_bps,
        estimated_edge_bps=body.estimated_edge_bps,
        ai_probability_pct=body.ai_probability_pct,
        market_probability_pct=body.market_probability_pct,
        expires_at=body.expires_at,
    )
    session.add(suggestion)
    await session.commit()
    await session.refresh(suggestion)

    # Broadcast post-commit so the browser's refetch reads the row we just
    # wrote. Discrete event → the frontend invalidates ['suggestions']; this
    # mirrors supervisor._broadcast_position_synced (direct app-event call, no
    # _serialize builder). Best-effort: a missing broadcaster (no supervisor in
    # tests) must not fail the write.
    # Use the validated Pydantic enums (body.*) for serialization, not the
    # refreshed ORM attributes: the columns are String, so a refreshed
    # `suggestion.kind` reads back as a plain str with no .value. body.kind is
    # the real enum. SuggestionStatus.PENDING is a constant we set above.
    broadcast = getattr(request.app.state, "broadcast", None)
    if broadcast is not None:
        await broadcast.broadcast_app_event(
            {
                "type": "suggestion",
                "suggestion_id": suggestion.id,
                "kind": body.kind.value,
                "ticker": body.ticker,
            }
        )

    return {
        "suggestion_id": suggestion.id,
        "kind": body.kind.value,
        "ticker": body.ticker,
        "side": body.side.value,
        "status": SuggestionStatus.PENDING.value,
    }


def _suggestion_to_dict(s: Suggestion, ticker: str | None) -> dict[str, Any]:
    """Serialize a SUGGESTION for the frontend cards. Money in integer cents;
    the frontend formats. `ticker` is the market ticker (joined in)."""
    return {
        "id": s.id,
        "kind": s.kind,
        "ticker": ticker,
        "side": s.side,
        "suggested_price_cents": s.suggested_price_cents,
        "suggested_size_cents": s.suggested_size_cents,
        "strategy": s.strategy,
        "justification": s.justification,
        "confidence": s.confidence,
        "urgency": s.urgency,
        "status": s.status,
        "ai_probability_pct": s.ai_probability_pct,
        "market_probability_pct": s.market_probability_pct,
        "estimated_edge_bps": s.estimated_edge_bps,
        "created_at": utc_iso(s.created_at),
        "expires_at": utc_iso(s.expires_at),
    }


@router.get("/partner/suggestions")
async def list_suggestions(
    status: str = "pending",
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Suggestions for the frontend to cold-load (default: pending only).

    The browser bootstraps the amber cards from here, then keeps them fresh by
    invalidating ['suggestions'] on the WS `suggestion` event. Newest first.
    """
    stmt = (
        select(Suggestion, Market.kalshi_ticker)
        .join(Market, Market.id == Suggestion.market_id, isouter=True)
        .where(Suggestion.status == status)
        .order_by(Suggestion.id.desc())
    )
    rows = (await session.execute(stmt)).all()
    return {"suggestions": [_suggestion_to_dict(s, ticker) for s, ticker in rows]}


@router.post("/partner/suggestions/{suggestion_id}/dismiss")
async def dismiss_suggestion(
    suggestion_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Mark a suggestion rejected (the user dismissed the card). Idempotent-ish:
    a missing id is a 404; an already-terminal one is left as-is. Broadcasts so
    other open tabs drop the card too."""
    suggestion = await session.get(Suggestion, suggestion_id)
    if suggestion is None:
        raise HTTPException(status_code=404, detail="no such suggestion")
    if suggestion.status == SuggestionStatus.PENDING:
        suggestion.status = SuggestionStatus.REJECTED
        await session.commit()

    broadcast = getattr(request.app.state, "broadcast", None)
    if broadcast is not None:
        await broadcast.broadcast_app_event(
            {"type": "suggestion", "suggestion_id": suggestion_id, "dismissed": True}
        )
    return {"suggestion_id": suggestion_id, "status": SuggestionStatus.REJECTED.value}
