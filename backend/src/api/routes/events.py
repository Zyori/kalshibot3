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

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.db import get_session
from src.core.logging import get_logger
from src.core.types import utc_iso
from src.kalshi.rest import KalshiRestClient
from src.models import Market, Position
from src.sports.run_of_play import live_payload
from src.sports.soccer import (
    is_soccer_ticker,
    kalshi_category_url,
    league_display_name,
    total_goals_threshold,
    total_series_for_game,
)

router = APIRouter()
log = get_logger(__name__)


def _total_goals_event_ticker(game_event_ticker: str) -> str | None:
    """Derive the total-goals event_ticker for a game event_ticker, or None when
    we don't have a total series for this league. Same {date}{matchup} suffix,
    different series prefix:
        KXINTLFRIENDLYGAME-26JUN01COLCRI → KXINTLFRIENDLYTOTAL-26JUN01COLCRI
    """
    game_series, _, suffix = game_event_ticker.partition("-")
    total_series = total_series_for_game(game_series)
    if total_series is None or not suffix:
        return None
    return f"{total_series}-{suffix}"


async def _fetch_total_goals(
    request: Request, game_event_ticker: str
) -> list[dict[str, Any]]:
    """The per-game Over/Under ladder for a game, as its own list (separate from
    the moneyline `markets`, so it never lands on the price chart). WS-subscribed
    for live prices like the moneyline children — but the subscription is tied to
    the game's lifecycle by the supervisor: when the game goes DONE, the tier
    dispatcher unsubscribes its totals too (supervisor._total_tickers_for /
    done-handling), so they don't leak. Kalshi's REST /markets returns null
    bid/ask on these thin markets even when a book exists, so the WS book is the
    real price source. Empty list when the league has no total series, the event
    isn't listed, or the fetch fails — never breaks the event read."""
    total_event = _total_goals_event_ticker(game_event_ticker)
    if total_event is None:
        return []

    supervisor = getattr(request.app.state, "supervisor", None)
    if supervisor is None:
        return []
    try:
        async with KalshiRestClient() as client:
            resp = await client.get_markets(event_ticker=total_event, limit=50)
    except Exception as e:  # noqa: BLE001 — totals are a bonus, never fail the game read
        log.warning("total_goals_fetch_failed", total_event=total_event, error=str(e)[:120])
        return []

    markets = [m for m in resp.markets if total_goals_threshold(m.ticker) is not None]
    if not markets:
        return []

    tickers = [m.ticker for m in markets]
    # Register these totals with the supervisor so its tier dispatcher keeps them
    # subscribed while the game is live and unsubscribes them when it's DONE —
    # the lifecycle hook that stops the subscription set from leaking.
    supervisor.track_total_goals(game_event_ticker, tickers)
    try:
        await supervisor.kalshi_ws.add_market_subscriptions(tickers)
    except Exception as e:  # noqa: BLE001
        log.warning("total_goals_subscribe_failed", total_event=total_event, error=str(e)[:120])
    unseeded = [
        t for t in tickers
        if not ((book := supervisor.live_state.books.get(t)) is not None and book.ws_owned)
    ]
    await asyncio.gather(
        *(supervisor.market_refresher.refresh_now_await(t) for t in unseeded),
        return_exceptions=True,
    )

    live_state = supervisor.live_state
    out: list[dict[str, Any]] = []
    for m in markets:
        book = live_state.books.get(m.ticker)
        out.append({
            "ticker": m.ticker,
            "threshold": total_goals_threshold(m.ticker),  # 1.5 / 2.5 / 3.5 / 4.5
            "label": m.yes_sub_title,  # "Over 2.5 goals scored"
            "status": m.status,
            "yes_bid_cents": book.yes_best_bid if book else None,
            "yes_ask_cents": book.yes_best_ask if book else None,
            "no_bid_cents": book.no_best_bid if book else None,
            "no_ask_cents": book.no_best_ask if book else None,
        })
    out.sort(key=lambda d: d["threshold"])
    return out


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
        log.warning("event_subscribe_failed", event_ticker=event_ticker, error=str(e)[:120])

    # Seed any child whose book WS doesn't own yet with one REST snapshot, so
    # the response carries a real top-of-book on first view (the WS snapshot
    # from the subscribe above is in flight and won't have landed yet). Once
    # WS owns the book, WS deltas keep it fresh — we do NOT resync locked books
    # here: WS is authoritative for subscribed markets, and a REST repoll would
    # race the delta stream. See the 2026-05-29 stale-book investigation.
    # Skip the seed once WS owns the book. Keying on ws_owned (not both-sides-
    # non-empty) avoids re-fetching every load for a legitimately one-sided book.
    unseeded = [
        t for t in child_tickers
        if not (
            (book := supervisor.live_state.books.get(t)) is not None
            and book.ws_owned
        )
    ]
    # Concurrent seeds — independent REST calls; gather so a 3-way moneyline's
    # children don't stack sequentially on first load.
    seed_results = await asyncio.gather(
        *(supervisor.market_refresher.refresh_now_await(t) for t in unseeded),
        return_exceptions=True,
    )
    for t, result in zip(unseeded, seed_results):
        if isinstance(result, Exception):
            log.warning("event_book_seed_failed", ticker=t, error=str(result)[:120])

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
                    # Fee-inclusive exact avg entry matching kalshi.com (e.g.
                    # 57.71): (cost_basis + fees) / quantity. Falls back to the
                    # floored whole-cent value pre-backfill.
                    "avg_entry_price": (
                        round((held.cost_basis_cents + (held.fees_paid_cents or 0)) / held.quantity, 2)
                        if held.cost_basis_cents is not None and held.quantity > 0
                        else held.avg_entry_price_cents
                    ),
                    "cost_basis_cents": held.cost_basis_cents,
                    "current_price_cents": held.current_price_cents,
                    "unrealized_pnl_cents": held.unrealized_pnl_cents,
                    "realized_pnl_cents": held.realized_pnl_cents,
                    "fees_paid_cents": held.fees_paid_cents,
                }
            ),
        }

    # Sort children by ticker suffix so the tab order is stable across
    # refreshes. For 3-way moneylines this puts NGR/TIE/ZIM in alphabetical
    # order — not perfect but stable.
    children.sort(key=lambda m: m.ticker)

    # Per-game Over/Under ladder — fetched on-demand, kept in its OWN array so it
    # never enters `markets` (and therefore never the price chart). Best-effort.
    total_goals = await _fetch_total_goals(request, event_ticker)

    return {
        "event_ticker": event_ticker,
        "event_title": head.event_title,
        "series": head.series,
        "league": league_display_name(head.series),
        "league_url": kalshi_category_url(head.series),
        "open_time": utc_iso(head.open_time),
        "close_time": utc_iso(head.close_time),
        "bucket": head.bucket,
        "espn_state": head.espn_state,
        "espn_period": head.espn_period,
        "espn_clock": head.espn_clock,
        "espn_status_detail": head.espn_status_detail,
        "live": live_payload(head.espn_event),
        "markets": [child_dict(m) for m in children],
        "total_goals": total_goals,
    }
