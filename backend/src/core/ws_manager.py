"""Browser WebSocket connection manager.

Owns the set of currently-connected browser clients and broadcasts events
to all of them. Events are coalesced in 500ms windows — during a live game
the Kalshi WS pushes orderbook deltas many times per second; flushing once
per tick wastes browser CPU on identical book renders.

Design:
  - One BroadcastManager per process. Lives on app.state.broadcast.
  - Each connected browser is a WebSocket in self._clients.
  - Inbound events go into _pending; the flush loop drains it every 500ms.
  - Pending events for the same (type, ticker) key collapse: a later update
    supersedes an earlier one, because the snapshot/delta semantics already
    encode "this is the latest." For fills and user_orders we keep every
    message (no collapse) — those are durable events, not snapshots.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterable
from typing import Any

from fastapi import WebSocket

from src.core.logging import get_logger
from src.kalshi.ws_wire import (
    Fill,
    KalshiWsMessage,
    MarketLifecycle,
    OrderbookDelta,
    OrderbookSnapshot,
    UserOrder,
)

log = get_logger(__name__)

FLUSH_INTERVAL_S = 0.5


def _collapse_key(msg: KalshiWsMessage) -> str | None:
    """Key for coalescing. Returns None if the message must be kept verbatim.

    Orderbook updates collapse per (type, ticker) — only the latest matters
    because each carries full or partial state. Fills and user_orders never
    collapse — every one is a discrete event the UI cares about.
    """
    if isinstance(msg, OrderbookSnapshot):
        return f"snapshot:{msg.msg.market_ticker}"
    if isinstance(msg, OrderbookDelta):
        # Deltas are per-price-level; collapsing them would drop liquidity
        # movements between two browser flushes. Keep all of them.
        return None
    if isinstance(msg, MarketLifecycle):
        return f"lifecycle:{msg.msg.market_ticker}"
    return None


def _serialize(msg: KalshiWsMessage) -> dict[str, Any] | None:
    """Browser-side payload. Single canonical schema, regardless of channel.

    Returns None for message types the browser doesn't care about (subscribe
    acks, unsubscribe acks, update_subscription oks) — those are wire-level
    plumbing for the upstream Kalshi WS, not data the dashboard renders.
    Returning None instead of raising keeps a future new ack type from
    silently killing the entire broadcast flush loop, which is what happened
    when Subscribed/Unsubscribed/Ok were added to KalshiWsMessage.
    """
    if isinstance(msg, OrderbookSnapshot):
        return {
            "type": "orderbook_snapshot",
            "ticker": msg.msg.market_ticker,
            "yes": [{"price": l.price_cents, "qty": l.quantity} for l in msg.msg.yes],
            "no":  [{"price": l.price_cents, "qty": l.quantity} for l in msg.msg.no],
        }
    if isinstance(msg, OrderbookDelta):
        return {
            "type": "orderbook_delta",
            "ticker": msg.msg.market_ticker,
            "price": msg.msg.price_cents,
            "delta": msg.msg.delta,
            "side": msg.msg.side,
        }
    if isinstance(msg, Fill):
        return {
            "type": "fill",
            "trade_id": msg.msg.trade_id,
            "order_id": msg.msg.order_id,
            "ticker": msg.msg.ticker,
            "side": msg.msg.side,
            "action": msg.msg.action,
            "count": msg.msg.count,
            "yes_price": msg.msg.yes_price_cents,
            "no_price": msg.msg.no_price_cents,
        }
    if isinstance(msg, UserOrder):
        return {
            "type": "user_order",
            "order_id": msg.msg.order_id,
            "client_order_id": msg.msg.client_order_id,
            "ticker": msg.msg.ticker,
            "side": msg.msg.side,
            "status": msg.msg.status,
            "yes_price": msg.msg.yes_price_cents,
            "remaining_count": msg.msg.remaining_count,
        }
    if isinstance(msg, MarketLifecycle):
        return {
            "type": "market_lifecycle",
            "ticker": msg.msg.market_ticker,
            "status": msg.msg.status,
            "settlement_value": msg.msg.settlement_value,
        }
    # Subscribed / Unsubscribed / Ok: wire-level acks. Browser doesn't need them.
    return None


class BroadcastManager:
    """Fan-out hub for browser clients. Single instance per process."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        # Coalesced messages, keyed for replacement; un-coalesced messages
        # accumulate in _pending_list.
        self._pending_collapsed: dict[str, KalshiWsMessage] = {}
        self._pending_list: list[KalshiWsMessage] = []
        # App-level events (not from Kalshi WS): already-serialized dicts that
        # bypass _serialize. Keyed by `type` so repeats within one flush window
        # collapse to a single notification — e.g. two fills firing two
        # position_synced signals only wakes the browser once.
        self._pending_app: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._flush_task: asyncio.Task | None = None
        self._stopped = False

    # === Client lifecycle ===

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)
        log.info("browser_ws_connected", total=len(self._clients))

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)
        log.info("browser_ws_disconnected", total=len(self._clients))

    # === Inbound from Kalshi WS consumer ===

    async def enqueue(self, msg: KalshiWsMessage) -> None:
        async with self._lock:
            key = _collapse_key(msg)
            if key is None:
                self._pending_list.append(msg)
            else:
                self._pending_collapsed[key] = msg

    async def consume_queue(self, queue: asyncio.Queue[KalshiWsMessage]) -> None:
        """Long-running task: pull from the Kalshi consumer's queue forever."""
        while not self._stopped:
            msg = await queue.get()
            await self.enqueue(msg)

    async def broadcast_app_event(self, event: dict[str, Any]) -> None:
        """Push an app-level event (not a Kalshi WS message) to browsers.

        The payload is the final wire dict — it must carry its own `type` and
        is sent verbatim, skipping _serialize. Used for server-derived signals
        like `position_synced`, which fires only after a position reconciliation
        commits so the refetch it triggers reads fresh DB state.
        """
        async with self._lock:
            self._pending_app[event["type"]] = event

    # === Flush loop ===

    async def start(self) -> None:
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = asyncio.create_task(self._flush_loop())

    async def stop(self) -> None:
        self._stopped = True
        if self._flush_task is not None:
            self._flush_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._flush_task
        # Close any still-connected clients politely.
        for ws in list(self._clients):
            with contextlib.suppress(Exception):
                await ws.close()
        self._clients.clear()

    async def _flush_loop(self) -> None:
        while not self._stopped:
            await asyncio.sleep(FLUSH_INTERVAL_S)
            try:
                await self._flush_once()
            except Exception:  # noqa: BLE001
                # Surface the failure but keep the loop alive — a single bad
                # batch must never silently stop broadcasts to the browser
                # (that was the failure mode that hid this bug for hours).
                log.exception("broadcast_flush_failed")

    async def _flush_once(self) -> None:
        async with self._lock:
            if (
                not self._pending_collapsed
                and not self._pending_list
                and not self._pending_app
            ):
                return
            batch = list(self._pending_collapsed.values()) + self._pending_list
            app_events = list(self._pending_app.values())
            self._pending_collapsed.clear()
            self._pending_list.clear()
            self._pending_app.clear()

        if not self._clients:
            return  # nobody listening; drop the batch

        events = [s for s in (_serialize(m) for m in batch) if s is not None]
        events.extend(app_events)
        if not events:
            return
        payload = {"events": events}
        dead: list[WebSocket] = []
        for ws in self._clients:
            try:
                await ws.send_json(payload)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)
        if dead:
            log.info("browser_ws_pruned_dead", count=len(dead))
