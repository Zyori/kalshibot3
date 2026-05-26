"""Markets API.

Two endpoints:
  GET /api/markets/feed         the home-page market discovery feed
                                grouped into live / upcoming / recent
  GET /api/markets/{ticker}     one market's current book snapshot

Both read from in-memory state populated by the supervisor — no Kalshi REST
call per request. The discovery poller refreshes every 60s; the orderbook
state lives in LiveState fed by WS deltas.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from src.core.logging import get_logger
from src.sports.soccer import is_soccer_ticker

router = APIRouter()
log = get_logger(__name__)


def _feed_market_to_dict(m: Any) -> dict[str, Any]:
    """Serialize a FeedMarket for the wire. Cents stay as cents, times ISO."""
    return {
        "ticker": m.ticker,
        "event_ticker": m.event_ticker,
        "event_title": m.event_title,
        "market_title": m.market_title,
        "series": m.series,
        "status": m.status,
        "open_time": m.open_time.isoformat() if m.open_time else None,
        "close_time": m.close_time.isoformat() if m.close_time else None,
        "yes_bid_cents": m.yes_bid_cents,
        "yes_ask_cents": m.yes_ask_cents,
        "volume": m.volume,
        "bucket": m.bucket,
    }


@router.get("/markets/feed")
async def get_feed(request: Request) -> dict[str, Any]:
    """Home-page discovery feed — soccer matches grouped by time-state."""
    supervisor = getattr(request.app.state, "supervisor", None)
    if supervisor is None:
        return {"live": [], "upcoming": [], "recent": [], "refreshed_at": None}

    feed = supervisor.market_discovery.get_feed()
    return {
        "live": [_feed_market_to_dict(m) for m in feed.live],
        "upcoming": [_feed_market_to_dict(m) for m in feed.upcoming],
        "recent": [_feed_market_to_dict(m) for m in feed.recent],
        "refreshed_at": feed.refreshed_at.isoformat() if feed.refreshed_at else None,
    }


@router.get("/markets/{ticker}")
async def get_market(ticker: str, request: Request) -> dict[str, Any]:
    """Per-market detail: current orderbook from LiveState.

    Cross-market isolation: refuses non-soccer tickers. Even though Kalshi
    would happily return book data for any ticker, this app's role is
    soccer-only, and accidentally exposing politics market data through
    our UI would blur the line. Hard refuse keeps the rule visible.
    """
    if not is_soccer_ticker(ticker):
        raise HTTPException(
            status_code=400,
            detail=f"{ticker} is not a soccer market this app tracks",
        )

    supervisor = getattr(request.app.state, "supervisor", None)
    if supervisor is None:
        raise HTTPException(status_code=503, detail="supervisor not started")

    live_state = supervisor.live_state
    book = live_state.books.get(ticker)

    # Subscribe to this market's orderbook stream if we aren't already. This
    # is how the user "discovers" a market we haven't been watching — paste
    # a ticker, hit the route, the WS picks it up for next time.
    try:
        await supervisor.kalshi_ws.add_market_subscriptions([ticker])
    except Exception as e:  # noqa: BLE001 — never fail the read on a sub failure
        log.warning("market_detail_sub_failed", ticker=ticker, error=str(e)[:120])

    if book is None:
        # No book yet — caller can re-poll in a moment; the WS will populate.
        return {
            "ticker": ticker,
            "status": "open",
            "yes": [],
            "no": [],
            "yes_best_bid": None,
            "yes_best_ask": None,
            "no_best_bid": None,
            "no_best_ask": None,
            "last_update_ago_s": None,
        }

    import time
    age = (time.monotonic() - book.last_update) if book.last_update else None
    return {
        "ticker": book.ticker,
        "status": book.status,
        "yes": [{"price": p, "qty": q} for p, q in sorted(book.yes.levels.items(), reverse=True)],
        "no":  [{"price": p, "qty": q} for p, q in sorted(book.no.levels.items(), reverse=True)],
        "yes_best_bid": book.yes_best_bid,
        "yes_best_ask": book.yes_best_ask,
        "no_best_bid": book.no_best_bid,
        "no_best_ask": book.no_best_ask,
        "last_update_ago_s": round(age, 2) if age is not None else None,
    }
