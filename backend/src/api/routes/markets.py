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

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from src.core.logging import get_logger
from src.core.types import utc_iso
from src.kalshi.rest import KalshiRestClient
from src.sports.soccer import is_soccer_ticker, league_display_name


def _dollar_str_to_cents(s: str) -> int:
    """Kalshi sends "0.84" — convert to 84."""
    return int(round(float(s) * 100))

router = APIRouter()
log = get_logger(__name__)


def _feed_market_to_dict(m: Any, live_state: Any) -> dict[str, Any]:
    """Serialize a FeedMarket for the wire.

    Prices come from `live_state.books` — the single source of truth that
    both the WS deltas (SOON/LIVE tier) and the REST poller (FAR tier)
    write into. The static fields on FeedMarket (yes_bid_cents,
    yes_ask_cents from Kalshi's /events summary) are ignored because they
    lie — Kalshi returns null for those on soccer markets even when there
    is a real orderbook (see commit message on the tiering rework).
    """
    book = live_state.books.get(m.ticker)
    yes_bid = book.yes_best_bid if book else None
    yes_ask = book.yes_best_ask if book else None
    # Score header: ESPN's home/away displayName + score. Only meaningful
    # while the game is in progress, but we surface it for post-game rows
    # too (the recent block shows the final score).
    home_name: str | None = None
    away_name: str | None = None
    home_score: int | None = None
    away_score: int | None = None
    if m.espn_event is not None:
        home_name = m.espn_event.home_names[0] if m.espn_event.home_names else None
        away_name = m.espn_event.away_names[0] if m.espn_event.away_names else None
        home_score = m.espn_event.home_stats.score
        away_score = m.espn_event.away_stats.score
    return {
        "ticker": m.ticker,
        "event_ticker": m.event_ticker,
        "event_title": m.event_title,
        "market_title": m.market_title,
        "yes_sub_title": m.yes_sub_title,
        "series": m.series,
        "league": league_display_name(m.series),
        "status": m.status,
        "open_time": utc_iso(m.open_time),
        "close_time": utc_iso(m.close_time),
        "yes_bid_cents": yes_bid,
        "yes_ask_cents": yes_ask,
        "volume": m.volume,
        "bucket": m.bucket,
        "espn_state": m.espn_state,
        "espn_period": m.espn_period,
        "espn_clock": m.espn_clock,
        "espn_status_detail": m.espn_status_detail,
        "home_name": home_name,
        "away_name": away_name,
        "home_score": home_score,
        "away_score": away_score,
    }


@router.get("/markets/feed")
async def get_feed(request: Request) -> dict[str, Any]:
    """Home-page discovery feed — soccer matches grouped by time-state."""
    supervisor = getattr(request.app.state, "supervisor", None)
    if supervisor is None:
        return {"live": [], "upcoming": [], "recent": [], "refreshed_at": None}

    feed = supervisor.market_discovery.get_feed()
    live_state = supervisor.live_state
    return {
        "live": [_feed_market_to_dict(m, live_state) for m in feed.live],
        "upcoming": [_feed_market_to_dict(m, live_state) for m in feed.upcoming],
        "recent": [_feed_market_to_dict(m, live_state) for m in feed.recent],
        "refreshed_at": utc_iso(feed.refreshed_at),
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

    # Subscribe to this market's orderbook stream if we aren't already. This
    # is how the user "discovers" a market we haven't been watching — paste
    # a ticker, hit the route, the WS picks it up for next time.
    try:
        await supervisor.kalshi_ws.add_market_subscriptions([ticker])
    except Exception as e:  # noqa: BLE001 — never fail the read on a sub failure
        log.warning("market_detail_sub_failed", ticker=ticker, error=str(e)[:120])

    # Locked-book guard: if WS deltas got out of sync and we're serving a
    # crossed book (yes_bid + no_bid > 100, impossible in reality), force a
    # one-shot REST resync before returning. Per-ticker rate-limited inside
    # the refresher so a persistently broken book can't hammer Kalshi.
    try:
        await supervisor.market_refresher.resync_locked(ticker)
    except Exception:  # noqa: BLE001
        log.warning("resync_locked_failed", ticker=ticker, exc_info=True)

    book = live_state.books.get(ticker)

    # Discovery has metadata (event_title, yes_sub_title, kickoff time) for
    # every tracked ticker. Look it up so the per-market page can render a
    # useful header instead of just the ticker code.
    meta = _find_in_feed(supervisor.market_discovery.get_feed(), ticker)
    metadata = {
        "event_title": meta.event_title if meta else None,
        "market_title": meta.market_title if meta else None,
        "yes_sub_title": meta.yes_sub_title if meta else None,
        "open_time": utc_iso(meta.open_time) if meta else None,
    }

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
            **metadata,
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
        **metadata,
    }


def _find_in_feed(feed: Any, ticker: str) -> Any:
    """Linear scan across the three feed buckets. ~240 tickers, O(n) is
    cheap and avoids maintaining a parallel index. Returns the FeedMarket
    or None if the ticker isn't tracked (e.g. an outdated discovery cache)."""
    for bucket in (feed.live, feed.upcoming, feed.recent):
        for m in bucket:
            if m.ticker == ticker:
                return m
    return None


@router.get("/markets/{ticker}/trades")
async def get_trades(ticker: str, limit: int = 500) -> dict[str, Any]:
    """Recent trades for the price-history chart.

    We hit Kalshi REST directly (not LiveState — we don't mirror trade
    history) and normalize the wire format here: dollar strings → cents,
    timestamps → ISO. Limit is capped at 1000 so the chart load stays fast.
    """
    if not is_soccer_ticker(ticker):
        raise HTTPException(status_code=400, detail=f"{ticker} is not a soccer market")

    limit = max(1, min(limit, 1000))
    trades: list[dict[str, Any]] = []
    async with KalshiRestClient() as client:
        try:
            data = await client.get_trades(ticker, limit=limit)
        except Exception as e:  # noqa: BLE001
            log.warning("get_trades_failed", ticker=ticker, error=str(e)[:200])
            raise HTTPException(status_code=502, detail=f"kalshi trades fetch failed: {e}") from e

    for t in data.get("trades", []):
        yes_raw = t.get("yes_price_dollars") or t.get("yes_price")
        if yes_raw is None:
            continue
        yes_cents = _dollar_str_to_cents(yes_raw) if isinstance(yes_raw, str) else int(yes_raw)
        count_raw = t.get("count_fp") or t.get("count") or 0
        count = int(float(count_raw))
        trades.append({
            "trade_id": t.get("trade_id"),
            "ts": t.get("created_time"),
            "yes_price": yes_cents,
            "count": count,
            "taker_side": t.get("taker_side"),
        })

    # Kalshi returns newest first; chart wants oldest first.
    trades.reverse()
    return {"ticker": ticker, "trades": trades}
