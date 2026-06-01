"""Partner API — the terminal cockpit's read/write surface.

The AI partner is a Claude Code terminal session (see .claude/skills/
lutz-partner/). It has no programmatic LLM anywhere in this codebase; it
reaches the app only through these localhost HTTP endpoints.

This module is the partner's data plane:
  GET  /partner/context[?event=]   one-call read of everything it reasons on
  POST /partner/suggestions         write an entry/exit suggestion (U4)

The context endpoint deliberately composes from the *same* route handlers
the dashboard uses (events.get_event, positions.list_positions,
ledger.list_bets) rather than re-querying — so the partner sees byte-for-byte
the numbers the site shows. Single source of truth: if /positions says a
position is +52%, /partner/context says +52%, because it's the same code.

Cross-market isolation is inherited: every composed handler is already
soccer-only.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.db import get_session
from src.core.logging import get_logger
from src.api.routes.events import get_event
from src.api.routes.ledger import _bet_to_dict
from src.api.routes.positions import list_positions
from src.models import Bet, Market

router = APIRouter()
log = get_logger(__name__)

RECENT_TRADES_LIMIT = 20
"""How many recent bets the partner gets as ledger context. Enough to see the
session's shape without flooding the terminal."""


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
    positions = await list_positions(session)

    out: dict[str, Any] = {
        "scope": "event" if event else "book",
        "bankroll_cents": _bankroll_cents(request),
        "positions": positions["positions"],
        "recent_trades": await _recent_trades(session),
    }

    if event is not None:
        # Reuse the exact EventView data path — same run-of-play, same child
        # markets, same per-side position embedding the site renders. Errors
        # (non-soccer 400, not-in-cache 404, supervisor-down 503) propagate.
        out["event"] = await get_event(event, request, session)

    return out
