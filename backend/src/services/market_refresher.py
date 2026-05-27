"""Market refresher: REST orderbook poller for FAR-tier markets.

Responsibility: keep `LiveState.books` populated with current top-of-book
data for markets that are too far from kickoff to justify a WS subscription
(FAR tier). When a market crosses into SOON, this poller drops it from the
schedule and the WS subscription path (C3) takes over.

The polling cadence is adaptive — see `market_tier.far_poll_interval`:
  >72h to kickoff   6h cadence
  24h–72h           2h cadence
  unknown kickoff   6h cadence (treated as far-future)

State lives in `_next_refresh_at`: ticker → monotonic deadline. The tick
loop runs every TICK_INTERVAL_S; any ticker whose deadline has passed gets
refreshed and rescheduled. This avoids per-ticker asyncio tasks (which
would explode under load) and keeps the work concentrated in one place.

Force-refresh: callers (HTTP route in C5, or supervisor on Far→Soon
transition) call `refresh_now(ticker)` to bypass the schedule and fetch
immediately. This is also how the cold-start case is handled — the first
discovery refresh enqueues every FAR ticker for an immediate poll, so
the dashboard has prices on first render rather than waiting up to 6h.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable

from src.core.logging import get_logger
from src.kalshi.live_state import LiveState
from src.kalshi.rest import KalshiRestClient
from src.kalshi.ws_wire import BookLevel
from src.services.market_tier import far_poll_interval

log = get_logger(__name__)

TICK_INTERVAL_S = 15
RESYNC_COOLDOWN_S = 10
"""Per-ticker minimum interval between locked-book resyncs. Stops a
persistently broken book from hammering Kalshi but still recovers quickly
once the WS catches back up."""
"""How often the poll loop wakes up to check for due refreshes. Smaller =
more responsive but more CPU. 15s gives sub-minute reaction to force-
refresh requests without spinning the event loop."""

ORDERBOOK_DEPTH = 32
"""How many price levels to request per side. Wide enough to render the
depth ladder; Kalshi caps at 100."""


class MarketRefresher:
    """Tracks FAR-tier tickers and polls their orderbook on an adaptive cadence.

    Not a long-lived asyncio.Task per ticker — one tick loop checks all
    scheduled tickers each TICK_INTERVAL_S. Scales to hundreds of FAR
    tickers without thread/task explosion.
    """

    def __init__(self, live_state: LiveState) -> None:
        self.live_state = live_state
        self._next_refresh_at: dict[str, float] = {}
        """ticker -> monotonic deadline. Past deadlines are due now."""
        self._kickoff: dict[str, float | None] = {}
        """ticker -> seconds-to-kickoff at last classification. Used to pick
        the next interval from `far_poll_interval`."""
        self._stopped = False
        self._task: asyncio.Task | None = None
        self._tick_event = asyncio.Event()
        self._last_resync_at: dict[str, float] = {}
        """ticker -> monotonic timestamp of last locked-book resync."""
        """Set by `refresh_now` to wake the tick loop early."""

    # === Scheduling ===

    def set_far_tickers(self, tickers_with_seconds: Iterable[tuple[str, float | None]]) -> None:
        """Replace the FAR-tier schedule. Existing entries keep their next-
        refresh deadline (don't reset a ticker just because it was already
        scheduled); new entries are enqueued for immediate refresh."""
        now = time.monotonic()
        seen: set[str] = set()
        for ticker, sec_to_kickoff in tickers_with_seconds:
            seen.add(ticker)
            self._kickoff[ticker] = sec_to_kickoff
            if ticker not in self._next_refresh_at:
                self._next_refresh_at[ticker] = now  # due now
        # Drop tickers no longer in the FAR set (they've moved to SOON/LIVE/DONE
        # or aren't being tracked anymore).
        for ticker in list(self._next_refresh_at):
            if ticker not in seen:
                del self._next_refresh_at[ticker]
                self._kickoff.pop(ticker, None)
        # Wake the tick loop in case we just added overdue work.
        self._tick_event.set()

    def drop(self, ticker: str) -> None:
        """Remove a ticker from the schedule (e.g. transitioned to SOON, where
        the WS will handle it)."""
        self._next_refresh_at.pop(ticker, None)
        self._kickoff.pop(ticker, None)

    def refresh_now(self, ticker: str) -> None:
        """Mark a ticker due immediately. Used by force-refresh and by the
        Far→Soon hand-off so the WS subscribe is paired with one fresh REST
        snapshot (covers the gap before the first WS snapshot arrives)."""
        self._next_refresh_at[ticker] = time.monotonic()
        self._tick_event.set()

    async def resync_locked(self, ticker: str) -> bool:
        """If `ticker`'s book is locked (yes_bid + no_bid > 100, impossible),
        force an immediate REST resync. Returns True if a resync ran.

        Rate-limited: only resyncs the same ticker once per RESYNC_COOLDOWN_S
        so a persistently broken book doesn't hammer Kalshi. The cooldown is
        per-ticker, not global — different broken tickers can each resync
        once per window.

        Inline (not scheduled) because the caller is usually a route that
        wants the fixed book before responding to the user."""
        book = self.live_state.books.get(ticker)
        if book is None or not book.is_locked:
            return False
        now = time.monotonic()
        last = self._last_resync_at.get(ticker, 0.0)
        if now - last < RESYNC_COOLDOWN_S:
            return False
        self._last_resync_at[ticker] = now
        log.warning(
            "book_locked_resync",
            ticker=ticker,
            yes_bid=book.yes_best_bid, no_bid=book.no_best_bid,
        )
        # Clear first so a partial fetch doesn't leave a half-stale book.
        book.clear()
        try:
            async with KalshiRestClient() as client:
                await self._poll_one(client, ticker)
        except Exception as e:  # noqa: BLE001
            log.warning("book_locked_resync_failed", ticker=ticker, error=str(e)[:120])
            return False
        return True

    # === Polling ===

    async def run(self) -> None:
        """Tick loop. Wakes every TICK_INTERVAL_S or when `_tick_event` fires.
        On each wake, polls every ticker whose deadline has passed."""
        while not self._stopped:
            try:
                await self._tick_once()
            except Exception:  # noqa: BLE001 — never let a bad poll kill the loop
                log.exception("market_refresher_tick_failed")
            try:
                await asyncio.wait_for(self._tick_event.wait(), timeout=TICK_INTERVAL_S)
            except asyncio.TimeoutError:
                pass
            self._tick_event.clear()

    async def stop(self) -> None:
        self._stopped = True
        self._tick_event.set()

    async def _tick_once(self) -> None:
        now = time.monotonic()
        due = [t for t, deadline in self._next_refresh_at.items() if deadline <= now]
        if not due:
            return
        async with KalshiRestClient() as client:
            for ticker in due:
                try:
                    await self._poll_one(client, ticker)
                except Exception as e:  # noqa: BLE001
                    log.warning("market_refresher_poll_failed", ticker=ticker, error=str(e)[:160])
                    # Reschedule on failure with the normal cadence so we don't
                    # hammer a misbehaving market.
                    self._reschedule(ticker)

    async def _poll_one(self, client: KalshiRestClient, ticker: str) -> None:
        ob = await client.get_orderbook(ticker, depth=ORDERBOOK_DEPTH)
        book = self.live_state.get_or_create_book(ticker)
        # Translate from schemas.OrderbookLevel (REST shape) to ws_wire.BookLevel
        # (LiveState shape) — same fields, different module names. Once the
        # snapshot is applied, downstream readers can't tell which path filled
        # the book.
        yes = [BookLevel(price_cents=l.price_cents, quantity=l.quantity) for l in ob.yes]
        no = [BookLevel(price_cents=l.price_cents, quantity=l.quantity) for l in ob.no]
        book.yes.apply_snapshot(yes)
        book.no.apply_snapshot(no)
        book.last_update = time.monotonic()
        self._reschedule(ticker)

    def _reschedule(self, ticker: str) -> None:
        sec_to_kickoff = self._kickoff.get(ticker)
        interval = far_poll_interval(sec_to_kickoff)
        self._next_refresh_at[ticker] = time.monotonic() + interval.total_seconds()
