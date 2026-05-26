"""Background task orchestration.

Owns every long-running asyncio task the app needs:
  - market discovery: poll Kalshi events → MarketFeed
  - kalshi WS consumer: Kalshi → LiveState + broadcast queue
  - broadcast consume_queue: queue → coalesced fan-out to browser clients
  - broadcast flush loop

Lifecycle is driven from main.lifespan: start() at app startup, stop() on
shutdown. A clean stop cancels each task and awaits its CancelledError so
no orphans linger.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import websockets

from datetime import datetime, timezone

from src.core.db import get_session_factory
from src.core.logging import get_logger
from src.core.ws_manager import BroadcastManager
from src.ingestion.market_discovery import MarketDiscovery, MarketFeed
from src.kalshi.live_state import LiveState
from src.kalshi.rest import KalshiRestClient
from src.kalshi.ws import (
    BACKOFF_BASE_S,
    BACKOFF_MAX_S,
    KalshiWsClient,
)
from src.kalshi.ws_wire import Fill, KalshiWsMessage
from src.services.bet_service import record_fill
from src.services.market_refresher import MarketRefresher
from src.services.market_tier import MarketTier, classify
from src.services.position_sync import PositionSyncer

log = get_logger(__name__)


class Supervisor:
    """Runs background tasks. Single instance per process."""

    def __init__(self) -> None:
        self.live_state = LiveState()
        self.broadcast = BroadcastManager()
        # The queue from Kalshi WS consumer to the broadcaster. Bounded so a
        # slow broadcaster can't grow memory forever; oldest dropped on
        # overflow (handled in the consumer via put_nowait + suppress).
        self.kalshi_to_browser: asyncio.Queue[KalshiWsMessage] = asyncio.Queue(maxsize=5000)

        # Single long-lived WS client so we can mutate its subscription set
        # in response to market-discovery refreshes.
        self.kalshi_ws = KalshiWsClient(
            self.live_state, broadcast_queue=self.kalshi_to_browser
        )
        # Fill handler: persist each fill as a BET row via bet_service.
        self.kalshi_ws.set_fill_handler(self._on_fill)

        self.market_discovery = MarketDiscovery()
        self.market_discovery.register_refresh_callback(self._on_discovery_refresh)
        self.market_refresher = MarketRefresher(self.live_state)
        self.position_syncer = PositionSyncer()

        # Track each ticker's last-known tier so we can detect transitions
        # (FAR→SOON, LIVE→DONE, etc.) and run the right hand-off logic.
        self._last_tier: dict[str, MarketTier] = {}

        # Set externally (main.lifespan) so the fill handler can invalidate
        # the cached balance after a fill changes the account state.
        self.app_state: Any = None

        self._tasks: list[asyncio.Task] = []

    async def _on_fill(self, fill: Fill) -> None:
        """Persist a fill to the DB. Cross-market isolation lives inside
        record_fill — non-soccer fills are logged and dropped."""
        factory = get_session_factory()
        try:
            async with factory() as session:
                await record_fill(session, fill)
                await session.commit()
        except Exception:  # noqa: BLE001
            log.exception("fill_persist_failed", trade_id=fill.msg.trade_id)
        # Trigger a position sync — a fill almost certainly changed our position.
        try:
            await self.position_syncer.trigger()
        except Exception:  # noqa: BLE001
            log.exception("position_sync_after_fill_failed")
        # Invalidate the cached balance so the next /health refreshes it.
        # Cheap signal; avoids holding a Kalshi REST call inside the fill
        # handler's critical path.
        if self.app_state is not None:
            self.app_state._balance_refreshed_at_mono = 0.0

    async def _on_discovery_refresh(self, feed: MarketFeed) -> None:
        """Classify every known ticker by tier and dispatch to the right path.

        Tier policy (see services/market_tier.py):
          FAR    REST poll on adaptive cadence (MarketRefresher)
          SOON   WS subscribed (KalshiWS)
          LIVE   WS subscribed (KalshiWS)
          DONE   unsubscribe + drop from polling

        Transitions matter:
          FAR→SOON   force one REST snapshot then WS subscribe, so the user
                     never sees a stale book during the hand-off window.
          LIVE→DONE  unsubscribe to keep the WS subscription set bounded.

        Avoiding the previous bug: never grows the WS subscription set
        unbounded — DONE tickers get explicit unsubscribes via per-ticker
        sid tracking in the WS client.
        """
        now = datetime.now(timezone.utc)

        # All buckets feed the classifier. Discovery already classified by
        # Kalshi status (live/upcoming/recent); the tier classifier looks at
        # the kickoff estimate, which lives in `open_time` after discovery
        # populates it from the ticker date.
        all_markets = list(feed.live) + list(feed.upcoming) + list(feed.recent)

        far_tickers: list[tuple[str, float | None]] = []
        subscribe_tickers: set[str] = set()
        done_tickers: set[str] = set()
        far_to_soon_transitions: list[str] = []

        for m in all_markets:
            tier_result = classify(kickoff=m.open_time, now=now)
            tier = tier_result.tier
            prev = self._last_tier.get(m.ticker)

            if tier is MarketTier.FAR:
                far_tickers.append((m.ticker, tier_result.seconds_to_kickoff))
            elif tier in (MarketTier.SOON, MarketTier.LIVE):
                subscribe_tickers.add(m.ticker)
                if prev is MarketTier.FAR:
                    far_to_soon_transitions.append(m.ticker)
            elif tier is MarketTier.DONE:
                done_tickers.add(m.ticker)

            self._last_tier[m.ticker] = tier

        # Hand-off order matters: drop transitioning tickers from the FAR
        # poller before letting WS take over, then issue the WS subscribes,
        # then unsubscribe DONE tickers.
        for ticker in far_to_soon_transitions:
            self.market_refresher.drop(ticker)

        self.market_refresher.set_far_tickers(far_tickers)

        await self.kalshi_ws.add_market_subscriptions(subscribe_tickers)

        # Force one REST snapshot per FAR→SOON transition so the user has a
        # current book the moment the WS subscribe lands, before the first
        # WS snapshot arrives. Bypasses the FAR scheduler (we already dropped
        # these tickers from it) and writes straight into LiveState.
        if far_to_soon_transitions:
            async with KalshiRestClient() as client:
                for ticker in far_to_soon_transitions:
                    try:
                        await self.market_refresher._poll_one(client, ticker)
                        # _poll_one re-adds to the FAR schedule on success.
                        # Drop again — SOON owns this ticker now.
                        self.market_refresher.drop(ticker)
                    except Exception:  # noqa: BLE001
                        log.warning("far_to_soon_snapshot_failed", ticker=ticker)

        if done_tickers:
            await self.kalshi_ws.remove_market_subscriptions(done_tickers)

        log.info(
            "tier_dispatch",
            far=len(far_tickers),
            subscribed=len(subscribe_tickers),
            done=len(done_tickers),
            transitions=len(far_to_soon_transitions),
        )

    async def _ws_consumer_loop(self) -> None:
        """Reconnect-with-backoff loop using the supervisor-owned client."""
        attempt = 0
        while True:
            try:
                await self.kalshi_ws.connect()
                attempt = 0
                await self.kalshi_ws.listen()
            except websockets.ConnectionClosed as e:
                log.warning("kalshi_ws_closed", code=e.code, reason=str(e.reason))
            except asyncio.CancelledError:
                await self.kalshi_ws.close()
                raise
            except Exception:  # noqa: BLE001
                log.exception("kalshi_ws_error")
            finally:
                self.live_state.connected = False

            delay = min(BACKOFF_BASE_S * (2 ** attempt), BACKOFF_MAX_S)
            attempt += 1
            log.info("kalshi_ws_reconnecting", attempt=attempt, delay_s=round(delay, 1))
            await asyncio.sleep(delay)

    async def start(self) -> None:
        """Spawn every background task. Idempotent — calling twice is a no-op."""
        if self._tasks:
            return

        await self.broadcast.start()

        self._tasks.append(asyncio.create_task(
            self._ws_consumer_loop(), name="kalshi_ws_consumer",
        ))
        self._tasks.append(asyncio.create_task(
            self.broadcast.consume_queue(self.kalshi_to_browser),
            name="broadcast_consume_queue",
        ))
        self._tasks.append(asyncio.create_task(
            self.market_discovery.run(), name="market_discovery",
        ))
        self._tasks.append(asyncio.create_task(
            self.market_refresher.run(), name="market_refresher",
        ))
        self._tasks.append(asyncio.create_task(
            self.position_syncer.run(), name="position_sync",
        ))
        log.info("supervisor_started", tasks=len(self._tasks))

    async def stop(self) -> None:
        """Cancel every task and await its cleanup."""
        await self.position_syncer.stop()
        await self.market_discovery.stop()
        await self.market_refresher.stop()
        await self.broadcast.stop()
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await t
        self._tasks.clear()
        log.info("supervisor_stopped")
