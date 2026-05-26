"""Market discovery: pull active soccer matches from Kalshi, group by time-state.

Polls `/events?series_ticker=...` for every soccer series we track. Each event
typically has 2 markets (home YES / away YES) plus sometimes a draw market.

We classify each market into one of:
  - LIVE       Kalshi status="active" AND open_time has passed (game is on)
  - UPCOMING   Kalshi status="active" AND open_time is in the future (≤48h)
  - RECENT     Kalshi status="closed"/"settled" AND close_time was recent

Markets outside those windows are dropped from the feed (e.g. matches a week
out — not useful to surface yet).

The result is cached in-process and refreshed every POLL_INTERVAL_S. Read via
`get_feed()`; the supervisor subscribes the WS client to every LIVE ticker so
the orderbook is hot when the user opens one.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from src.core.logging import get_logger
from src.kalshi.rest import KalshiRestClient
from src.kalshi.schemas import Event, Market
from src.sports.soccer import SOCCER_GAME_SERIES

log = get_logger(__name__)

POLL_INTERVAL_S = 60
"""How often to re-fetch the soccer events from Kalshi."""

LIVE_WINDOW = timedelta(hours=3, minutes=30)
"""How long a match counts as LIVE after estimated kickoff. ~110 min match
plus stoppage plus buffer."""

UPCOMING_HORIZON = timedelta(days=30)
"""Matches kicking off within this window appear in UPCOMING. Wide enough
to cover the WC group stage from before tournament start."""

RECENT_HORIZON = timedelta(hours=12)
"""Settled markets within this window remain in RECENT."""

# Most Kalshi soccer match tickers encode the kickoff date as YYMONDD —
# e.g. KXWCGAME-26JUN27JORARG-ARG. We use this as the kickoff proxy because
# Kalshi's market close_time is sometimes set to the entire tournament's
# end rather than the per-match settlement window.
_TICKER_DATE_RE = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})[A-Z]", re.ASCII)
_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


def _kickoff_from_ticker(ticker: str) -> datetime | None:
    """Parse the date encoded in a soccer ticker. Returns None if absent.

    Kickoff time-of-day isn't in the ticker, so we pick noon UTC as a
    midday proxy — accurate to within a few hours, enough for live-vs-
    upcoming classification. The per-market detail endpoint carries the
    real open_time when we need it.
    """
    m = _TICKER_DATE_RE.search(ticker)
    if m is None:
        return None
    yy, mon, dd = m.group(1), m.group(2), m.group(3)
    month = _MONTHS.get(mon)
    if month is None:
        return None
    try:
        return datetime(2000 + int(yy), month, int(dd), 12, 0, tzinfo=timezone.utc)
    except ValueError:
        return None


@dataclass
class FeedMarket:
    """One row in the discovery feed. Sport-agnostic representation."""

    ticker: str
    """Kalshi market ticker. Stable, used everywhere as the key."""

    event_ticker: str
    event_title: str
    """Display title — usually "Team A vs Team B"."""

    market_title: str
    """Display title of the individual market within the event."""

    series: str
    """The series prefix this market comes from (e.g. KXWCGAME)."""

    status: str
    """Kalshi-side status: active, closed, settled, determined, initialized."""

    open_time: datetime | None
    close_time: datetime | None

    volume: int | None

    bucket: str = "unknown"
    """One of: live, upcoming, recent. Set by the classifier."""


@dataclass
class MarketFeed:
    """Three time-grouped lists. Sorted within each bucket."""

    live: list[FeedMarket] = field(default_factory=list)
    upcoming: list[FeedMarket] = field(default_factory=list)
    recent: list[FeedMarket] = field(default_factory=list)
    refreshed_at: datetime | None = None


def _classify(market: FeedMarket, now: datetime) -> str | None:
    """Bucket assignment. Returns None to drop the market from the feed.

    Heuristic: Kalshi event listings don't always carry a reliable open_time
    (and close_time can be set to the tournament's end rather than the
    match's settlement window), so we parse the kickoff date from the
    ticker itself when possible.
      LIVE       kickoff has passed but within LIVE_WINDOW
      UPCOMING   kickoff in the future, within UPCOMING_HORIZON
      RECENT     status terminal AND close_time within RECENT_HORIZON
    """
    close_t = market.close_time

    if market.status in ("closed", "settled", "determined"):
        if close_t is not None and now - close_t <= RECENT_HORIZON:
            return "recent"
        return None

    if market.status != "active":
        return None

    kickoff = _kickoff_from_ticker(market.ticker)
    if kickoff is not None:
        market.open_time = kickoff  # populate so the wire format carries it
        delta_from_kickoff = now - kickoff
        if timedelta(0) <= delta_from_kickoff <= LIVE_WINDOW:
            return "live"
        if kickoff > now and (kickoff - now) <= UPCOMING_HORIZON:
            return "upcoming"
        return None

    # Fallback: no date in the ticker (rare — usually futures/derivatives).
    # Use close_time as a weak proxy, with a tight live window.
    if close_t is not None and close_t > now and (close_t - now) <= UPCOMING_HORIZON:
        return "upcoming"
    return None


def _event_to_feed_markets(event: Event, series: str) -> list[FeedMarket]:
    """Flatten one event into FeedMarket rows for each market it contains."""
    rows: list[FeedMarket] = []
    for m in event.markets or []:
        rows.append(FeedMarket(
            ticker=m.ticker,
            event_ticker=event.event_ticker,
            event_title=event.title,
            market_title=m.title,
            series=series,
            status=m.status,
            open_time=None,  # Event.markets doesn't carry open_time directly;
                             # close_time is the relevant signal for filtering.
            close_time=m.close_time,
            volume=m.volume,
        ))
    return rows


class MarketDiscovery:
    """In-process cache of the soccer market feed.

    Polls Kalshi every POLL_INTERVAL_S, classifies each market into a bucket,
    exposes the result via get_feed().
    """

    def __init__(self, series: Iterable[str] = SOCCER_GAME_SERIES) -> None:
        self._series = tuple(series)
        self._feed = MarketFeed()
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._stopped = False
        self._on_refresh: list = []
        """Callbacks invoked with the new set of LIVE tickers after each refresh.
        Supervisor uses this to keep the WS subscriptions in sync."""

    def get_feed(self) -> MarketFeed:
        return self._feed

    def register_refresh_callback(self, cb) -> None:
        """Subscribers receive the set of live tickers after every refresh."""
        self._on_refresh.append(cb)

    async def refresh_once(self) -> None:
        """One full pass: hit /events for every series, classify, replace cache."""
        async with KalshiRestClient() as client:
            rows: list[FeedMarket] = []
            for series in self._series:
                try:
                    rows.extend(await self._fetch_series(client, series))
                except Exception as e:  # noqa: BLE001 — bad series shouldn't kill the loop
                    log.warning("market_discovery_series_failed", series=series, error=str(e)[:160])

        now = datetime.now(timezone.utc)
        live, upcoming, recent = [], [], []
        for r in rows:
            bucket = _classify(r, now)
            if bucket is None:
                continue
            r.bucket = bucket
            if bucket == "live":
                live.append(r)
            elif bucket == "upcoming":
                upcoming.append(r)
            elif bucket == "recent":
                recent.append(r)

        # Sort:
        #   LIVE     by event_ticker (groups two sides of one match together)
        #   UPCOMING by kickoff ascending (soonest first), then event_ticker
        #   RECENT   by close_time descending (most recently settled first)
        far = now + timedelta(days=999)
        live.sort(key=lambda r: r.event_ticker)
        upcoming.sort(key=lambda r: (r.open_time or r.close_time or far, r.event_ticker))
        recent.sort(key=lambda r: r.close_time or now, reverse=True)

        async with self._lock:
            self._feed = MarketFeed(
                live=live,
                upcoming=upcoming,
                recent=recent,
                refreshed_at=now,
            )

        log.info(
            "market_discovery_refreshed",
            live=len(live), upcoming=len(upcoming), recent=len(recent),
        )

        # Fire callbacks with the full feed — supervisor's tier classifier
        # needs every ticker plus its kickoff estimate (open_time), not just
        # the LIVE bucket. Discovery doesn't make tier policy decisions; it
        # just hands over the bucketed list.
        feed_snapshot = self._feed
        for cb in self._on_refresh:
            try:
                await cb(feed_snapshot)
            except Exception:  # noqa: BLE001
                log.exception("market_discovery_callback_failed")

    async def _fetch_series(self, client: KalshiRestClient, series: str) -> list[FeedMarket]:
        """Paginate through `/events` for one series, collect FeedMarket rows."""
        rows: list[FeedMarket] = []
        cursor: str | None = None
        while True:
            resp = await client.get_events(
                series_ticker=series, limit=200, cursor=cursor, with_nested_markets=True,
            )
            for event in resp.events:
                rows.extend(_event_to_feed_markets(event, series))
            cursor = resp.cursor
            if not cursor or not resp.events:
                break
        return rows

    async def run(self) -> None:
        """Long-running poll loop. Called from the supervisor."""
        # First refresh on startup, then on the interval.
        try:
            await self.refresh_once()
        except Exception:  # noqa: BLE001
            log.exception("market_discovery_initial_refresh_failed")

        while not self._stopped:
            await asyncio.sleep(POLL_INTERVAL_S)
            try:
                await self.refresh_once()
            except Exception:  # noqa: BLE001
                log.exception("market_discovery_refresh_failed")

    async def stop(self) -> None:
        self._stopped = True
