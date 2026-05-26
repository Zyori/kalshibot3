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

import websockets

from src.core.logging import get_logger
from src.core.ws_manager import BroadcastManager
from src.ingestion.market_discovery import MarketDiscovery
from src.kalshi.live_state import LiveState
from src.kalshi.ws import (
    BACKOFF_BASE_S,
    BACKOFF_MAX_S,
    KalshiWsClient,
)
from src.kalshi.ws_wire import KalshiWsMessage

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
        self.market_discovery = MarketDiscovery()
        self.market_discovery.register_refresh_callback(self._on_discovery_refresh)

        self._tasks: list[asyncio.Task] = []

    async def _on_discovery_refresh(self, live_tickers: set[str]) -> None:
        """Keep WS orderbook subscriptions in sync with the live-feed.

        We only add — never unsubscribe — because Kalshi WS doesn't expose
        a per-ticker unsubscribe and removed tickers naturally stop emitting
        deltas once their markets settle. The LiveState book entries for
        stale tickers eventually age out via the next snapshot or never
        receive updates again, which is harmless.
        """
        await self.kalshi_ws.add_market_subscriptions(live_tickers)

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
        log.info("supervisor_started", tasks=len(self._tasks))

    async def stop(self) -> None:
        """Cancel every task and await its cleanup."""
        await self.market_discovery.stop()
        await self.broadcast.stop()
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await t
        self._tasks.clear()
        log.info("supervisor_stopped")
