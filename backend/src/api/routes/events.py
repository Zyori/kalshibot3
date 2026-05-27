"""Event API — one URL per game, child markets nested inside.

Where /api/markets/{ticker} is per-outcome (NGR / ZIM / TIE rendered as
separate pages), /api/events/{event_ticker} aggregates: returns the
event-level metadata plus every child market under it, each with current
top-of-book + the user's current position on that side.

The frontend uses this to render one page per game with a tab strip per
market — matches how Kalshi presents events.

Cross-market isolation: refuses non-soccer event tickers. The child
market list is sourced from MarketDiscovery's bucketed feed, which is
already soccer-only.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.db import get_session
from src.core.logging import get_logger
from src.core.types import utc_iso
from src.models import Market, Position
from src.sports.soccer import is_soccer_ticker, league_display_name

router = APIRouter()
log = get_logger(__name__)


@router.get("/events/{event_ticker}")
async def get_event(
    event_ticker: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Return event metadata + every child market under it.

    A child market here means one of the YES-outcome contracts in a 3-way
    moneyline (NGR / ZIM / TIE) or a 2-way market (YES / NO). Each child
    includes its current top-of-book (from LiveState) and the user's
    position on that ticker (from DB), all in one round-trip so the
    EventView page can render the full game in one fetch.
    """
    if not is_soccer_ticker(event_ticker):
        raise HTTPException(
            status_code=400,
            detail=f"{event_ticker} is not a soccer event",
        )

    supervisor = getattr(request.app.state, "supervisor", None)
    if supervisor is None:
        raise HTTPException(status_code=503, detail="supervisor not started")

    feed = supervisor.market_discovery.get_feed()
    # Linear scan across buckets — ~240 tickers, O(n) is cheap. Returns the
    # FeedMarket rows that share this event_ticker (the children).
    children = [
        m for bucket in (feed.live, feed.upcoming, feed.recent)
        for m in bucket
        if m.event_ticker == event_ticker
    ]
    if not children:
        raise HTTPException(
            status_code=404,
            detail=f"event {event_ticker} not in current discovery cache",
        )

    # Event-level fields from any child (they share event_title, open_time,
    # etc.). Pick the first child; sort children later for stable display.
    head = children[0]

    # Subscribe to every child market over WS — this is the same enroll-
    # on-view path the per-market route used. The supervisor's tier classifier
    # will keep these subscribed as long as they're SOON/LIVE; FAR tickers
    # will get the REST polling cadence.
    child_tickers = [m.ticker for m in children]
    try:
        await supervisor.kalshi_ws.add_market_subscriptions(child_tickers)
    except Exception as e:  # noqa: BLE001 — never fail the read on a sub failure
        log.warning("event_subscribe_failed", event=event_ticker, error=str(e)[:120])

    # Bulk-load positions for these tickers in one query.
    rows = (
        await session.execute(
            select(Position, Market.kalshi_ticker)
            .join(Market, Market.id == Position.market_id)
            .where(Market.kalshi_ticker.in_(child_tickers))
        )
    ).all()
    pos_by_ticker_side: dict[tuple[str, str], Position] = {
        (ticker, p.side): p for p, ticker in rows
    }

    live_state = supervisor.live_state

    def child_dict(m: Any) -> dict[str, Any]:
        book = live_state.books.get(m.ticker)
        yes_pos = pos_by_ticker_side.get((m.ticker, "yes"))
        no_pos = pos_by_ticker_side.get((m.ticker, "no"))
        # Only one side can be held at a time (position_sync nets them) but
        # surface whichever exists for completeness.
        held = yes_pos or no_pos
        return {
            "ticker": m.ticker,
            "yes_sub_title": m.yes_sub_title,
            "market_title": m.market_title,
            "status": m.status,
            "yes_bid_cents": book.yes_best_bid if book else None,
            "yes_ask_cents": book.yes_best_ask if book else None,
            "no_bid_cents": book.no_best_bid if book else None,
            "no_ask_cents": book.no_best_ask if book else None,
            "position": (
                None
                if held is None
                else {
                    "side": held.side,
                    "quantity": held.quantity,
                    "avg_entry_price_cents": held.avg_entry_price_cents,
                    "current_price_cents": held.current_price_cents,
                    "unrealized_pnl_cents": held.unrealized_pnl_cents,
                }
            ),
        }

    # Sort children by ticker suffix so the tab order is stable across
    # refreshes. For 3-way moneylines this puts NGR/TIE/ZIM in alphabetical
    # order — not perfect but stable.
    children.sort(key=lambda m: m.ticker)

    return {
        "event_ticker": event_ticker,
        "event_title": head.event_title,
        "series": head.series,
        "league": league_display_name(head.series),
        "open_time": utc_iso(head.open_time),
        "close_time": utc_iso(head.close_time),
        "bucket": head.bucket,
        "espn_state": head.espn_state,
        "espn_period": head.espn_period,
        "espn_clock": head.espn_clock,
        "espn_status_detail": head.espn_status_detail,
        "markets": [child_dict(m) for m in children],
    }
