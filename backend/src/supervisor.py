"""Background task orchestration.

Owns every long-running asyncio task the app needs:
  - kalshi_ws_consumer: Kalshi WS → LiveState + broadcast queue
  - broadcast.consume_queue: queue → coalesced fan-out to browser clients
  - broadcast.start: the 500ms flush loop

Lifecycle is driven from main.lifespan: start() at app startup, stop() on
shutdown. A clean stop cancels each task and awaits its CancelledError so
no orphans linger.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterable

from src.core.logging import get_logger
from src.core.ws_manager import BroadcastManager
from src.kalshi.live_state import LiveState
from src.kalshi.ws import kalshi_ws_consumer
from src.kalshi.ws_wire import KalshiWsMessage

log = get_logger(__name__)


class Supervisor:
    """Runs background tasks. Single instance per process."""

    def __init__(self) -> None:
        self.live_state = LiveState()
        self.broadcast = BroadcastManager()
        # The queue from Kalshi WS consumer to the broadcaster. Bounded so
        # a slow broadcaster can't grow memory forever; oldest dropped on
        # overflow (handled in kalshi_ws_consumer via put_nowait + suppress).
        self.kalshi_to_browser: asyncio.Queue[KalshiWsMessage] = asyncio.Queue(maxsize=5000)

        self._tasks: list[asyncio.Task] = []

    async def start(self, initial_tickers: Iterable[str] = ()) -> None:
        """Spawn every background task. Idempotent — calling twice is a no-op."""
        if self._tasks:
            return

        await self.broadcast.start()

        self._tasks.append(asyncio.create_task(
            kalshi_ws_consumer(
                self.live_state,
                broadcast_queue=self.kalshi_to_browser,
                initial_tickers=initial_tickers,
            ),
            name="kalshi_ws_consumer",
        ))
        self._tasks.append(asyncio.create_task(
            self.broadcast.consume_queue(self.kalshi_to_browser),
            name="broadcast_consume_queue",
        ))
        log.info("supervisor_started", tasks=len(self._tasks))

    async def stop(self) -> None:
        """Cancel every task and await its cleanup."""
        await self.broadcast.stop()
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await t
        self._tasks.clear()
        log.info("supervisor_stopped")
