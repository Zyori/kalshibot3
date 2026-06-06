"""Kalshi WebSocket client.

Ports V2's reconnect/sequence-tracking pattern (Kalshi-Mean-Reversion-Bot/
backend/src/ingestion/kalshi_ws.py) and adds V1's personal channels (fill,
user_orders).

Architecture:
  - Driven by one long-lived async task (Supervisor._ws_consumer_loop), which
    owns the connect/listen/backoff-reconnect loop around this client.
  - On connect: subscribe to orderbook_delta (per ticker list), fill (no
    market list — account-wide), user_orders (account-wide)
  - Each message → parse → update LiveState → put on an asyncio.Queue for
    the browser-WS broadcaster (chunk 9) to consume
  - On disconnect: exponential backoff reconnect, replay subscriptions
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Coroutine, Iterable
from typing import TYPE_CHECKING, Any

import websockets

from src.config import get_settings
from src.core.auth import KalshiAuth
from src.core.exceptions import KalshiError
from src.core.logging import get_logger
from src.kalshi.live_state import LiveState
from src.kalshi.ws_wire import (
    Fill,
    KalshiWsMessage,
    MarketLifecycle,
    Ok,
    OrderbookDelta,
    OrderbookSnapshot,
    Subscribed,
    Unsubscribed,
    UserOrder,
    parse_kalshi_ws_message,
)

if TYPE_CHECKING:
    from websockets.asyncio.client import ClientConnection

log = get_logger(__name__)

# Tunables
STALENESS_TIMEOUT_S = 90.0
"""Force a reconnect if NO WS message arrives for this long while connected.
Catches the half-stall the library ping/pong can't: TCP alive and pongs still
answered, but data messages stopped — the book silently freezes. Generous on
purpose: _last_message_at is connection-global across all subscribed tickers, so
genuine total silence for 90s means a stall, not a quiet market (during dead
hours some book still ticks). Library ping/pong (PING_INTERVAL_S below) handles
the fully-dead socket faster; this is the backstop for the alive-but-silent case."""
PING_INTERVAL_S = 20.0
PING_TIMEOUT_S = 20.0
WATCHDOG_INTERVAL_S = 15.0
BACKOFF_BASE_S = 1.0
BACKOFF_MAX_S = 30.0
SUBSCRIBE_BATCH_SIZE = 100
"""Kalshi caps tickers-per-subscribe in the docs at 100."""


class KalshiWsClient:
    """One connection, owns the subscription set, applies messages to LiveState.

    Reconnect / backoff logic lives in Supervisor._ws_consumer_loop — this
    class is just the connection lifecycle and message dispatch.
    """

    def __init__(
        self,
        live_state: LiveState,
        broadcast_queue: asyncio.Queue[KalshiWsMessage] | None = None,
    ) -> None:
        settings = get_settings()
        self.ws_url = settings.kalshi_ws_url
        self.auth = KalshiAuth(settings.kalshi_key_id, settings.kalshi_key_path)
        self.live_state = live_state
        self.broadcast_queue = broadcast_queue
        # Optional callback fired on every Fill — lets the supervisor wire
        # bet_service.record_fill without dragging DB imports into ws.py.
        self.on_fill: "asyncio.Future[None] | None" = None  # set externally
        self._fill_handler: "Any | None" = None
        self._lifecycle_handler: "Any | None" = None
        self._user_order_handler: "Any | None" = None

        self._ws: ClientConnection | None = None
        self._market_tickers: set[str] = set()
        """Markets we want orderbook_delta for. Persisted across reconnects so
        a fresh socket can replay subscriptions."""
        self._orderbook_sid: int | None = None
        """Server-assigned sid for the orderbook_delta subscription. Kalshi
        allocates exactly one sid per (channel, connection) — all market
        tickers we subscribe to share this sid. We mutate the ticker set on
        it with `update_subscription action=add_markets|delete_markets`.
        Verified against live Kalshi 2026-05-26 — see commit message for
        the wire-format probe results."""
        self._next_request_id: int = int(time.time() * 1000)
        """Monotonic counter for outbound message ids. Time-seeded so values
        don't collide across reconnects within the same second."""
        self._personal_channels_active = False
        self._sequence_numbers: dict[int, int] = {}
        """sid -> last seq we saw. Detects dropped messages."""
        self._last_message_at: float = 0.0
        self._handler_tasks: set[asyncio.Task[None]] = set()
        """Strong refs to in-flight handler tasks (fill/lifecycle/user_order).
        Without this the event loop holds only a weak ref and a handler — e.g.
        record_fill writing a real fill — could be GC'd mid-await. Each task
        self-discards on completion (see _spawn_handler)."""

    def is_subscribed(self, ticker: str) -> bool:
        """True if `ticker` is on our orderbook_delta subscription — i.e. WS is
        the authoritative source for its book. The REST market_refresher uses
        this to keep its hands off subscribed books (clearing + REST-repolling
        a live book races the delta stream and corrupts it)."""
        return ticker in self._market_tickers

    def subscribed_tickers(self) -> set[str]:
        """The current orderbook subscription set — shrinks on unsubscribe
        (delete_markets), unlike live_state.books which only ever grows. The
        price-history sampler prunes against this so its buffer can't accumulate
        markets we've stopped following."""
        return set(self._market_tickers)

    def _auth_headers(self) -> dict[str, str]:
        """Kalshi WS signs the path /trade-api/ws/v2 same way REST does the route."""
        timestamp_ms = str(int(time.time() * 1000))
        signature = self.auth._sign(timestamp_ms, "GET", "/trade-api/ws/v2")
        return {
            "KALSHI-ACCESS-KEY": self.auth.key_id,
            "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
            "KALSHI-ACCESS-SIGNATURE": signature,
        }

    async def connect(self) -> None:
        headers = self._auth_headers()
        # Pin ping/pong rather than relying on library defaults: this is what
        # surfaces a fully-dead socket (no pong → ConnectionClosed → the
        # supervisor reconnect loop catches it). The staleness watchdog is the
        # separate backstop for an alive-but-silent socket.
        self._ws = await websockets.connect(
            self.ws_url, additional_headers=headers,
            ping_interval=PING_INTERVAL_S, ping_timeout=PING_TIMEOUT_S,
        )
        self.live_state.connected = True
        self._last_message_at = time.monotonic()
        self._sequence_numbers.clear()
        # Sids are session-scoped: a new socket invalidates every prior sid.
        self._orderbook_sid = None
        log.info("kalshi_ws_connected", url=self.ws_url)
        await self._replay_subscriptions()

    async def add_market_subscriptions(self, tickers: Iterable[str]) -> None:
        """Add tickers to orderbook_delta. Idempotent.

        First call (no sid yet): sends `subscribe` to allocate the sid and
        register the initial ticker set. Subsequent calls: sends
        `update_subscription action=add_markets` to extend the existing sid.
        """
        new = set(tickers) - self._market_tickers
        if not new:
            return
        self._market_tickers |= new
        if self._ws is None:
            return
        if self._orderbook_sid is None:
            await self._send_initial_orderbook_subscribe(sorted(new))
        else:
            await self._send_update_subscription("add_markets", sorted(new))

    async def remove_market_subscriptions(self, tickers: Iterable[str]) -> None:
        """Drop tickers from the orderbook_delta sid. Idempotent.

        Sends `update_subscription action=delete_markets`. If we'd be removing
        every ticker, sends an `unsubscribe` instead so the sid is released
        cleanly (a sid with zero tickers is a hanging session-resource on
        Kalshi's side — better to drop and re-allocate on next add).
        """
        targets = set(tickers) & self._market_tickers
        if not targets:
            return
        self._market_tickers -= targets
        # WS no longer feeds these books — release ownership so a later REST
        # poll (FAR demotion) can re-establish the baseline.
        for t in targets:
            self.live_state.release_ws_ownership(t)
        if self._ws is None or self._orderbook_sid is None:
            return
        if not self._market_tickers:
            await self._send_unsubscribe_sid(self._orderbook_sid)
            self._orderbook_sid = None
        else:
            await self._send_update_subscription("delete_markets", sorted(targets))

    async def _ensure_personal_channels(self) -> None:
        """Subscribe to fill + user_orders. Both are account-wide (no tickers)."""
        if self._personal_channels_active or self._ws is None:
            return
        await self._send_personal_subscribe("fill")
        await self._send_personal_subscribe("user_orders")
        self._personal_channels_active = True

    async def _replay_subscriptions(self) -> None:
        self._personal_channels_active = False
        await self._ensure_personal_channels()
        if self._market_tickers:
            # A new socket invalidates every prior WS snapshot — the deltas that
            # will arrive are computed against the *fresh* snapshot, not the one
            # the old connection delivered. Release WS ownership so a book that
            # goes stale before its fresh snapshot lands can still be repaired by
            # REST (resync_locked / the sweep), instead of being locked out until
            # the resubscribe completes. The fresh snapshot re-asserts ownership.
            for t in self._market_tickers:
                self.live_state.release_ws_ownership(t)
            await self._send_initial_orderbook_subscribe(sorted(self._market_tickers))

    def _alloc_request_id(self) -> int:
        self._next_request_id += 1
        return self._next_request_id

    async def _send_initial_orderbook_subscribe(self, tickers: list[str]) -> None:
        if self._ws is None or not tickers:
            return
        rid = self._alloc_request_id()
        await self._ws.send(json.dumps({
            "id": rid, "cmd": "subscribe",
            "params": {"channels": ["orderbook_delta"], "market_tickers": tickers},
        }))
        log.info("kalshi_ws_subscribe_sent", tickers=len(tickers))

    async def _send_update_subscription(self, action: str, tickers: list[str]) -> None:
        if self._ws is None or self._orderbook_sid is None or not tickers:
            return
        rid = self._alloc_request_id()
        await self._ws.send(json.dumps({
            "id": rid, "cmd": "update_subscription",
            "params": {"sids": [self._orderbook_sid], "action": action, "market_tickers": tickers},
        }))
        log.info("kalshi_ws_update_subscription_sent", action=action, tickers=len(tickers), sid=self._orderbook_sid)

    async def _send_unsubscribe_sid(self, sid: int) -> None:
        if self._ws is None:
            return
        rid = self._alloc_request_id()
        await self._ws.send(json.dumps({
            "id": rid, "cmd": "unsubscribe", "params": {"sids": [sid]},
        }))
        log.info("kalshi_ws_unsubscribe_sent", sid=sid)

    async def _send_personal_subscribe(self, channel: str) -> None:
        if self._ws is None:
            return
        rid = self._alloc_request_id()
        await self._ws.send(json.dumps({
            "id": rid, "cmd": "subscribe", "params": {"channels": [channel]},
        }))
        log.info("kalshi_ws_subscribed_personal", channel=channel)

    def _check_sequence(self, sid: int, seq: int) -> None:
        """Log (don't recover) sequence gaps. A real gap means we missed an
        update; the snapshot-then-subscribe replay on reconnect will fix it."""
        last = self._sequence_numbers.get(sid)
        if last is not None and seq > last + 1:
            gap = seq - last - 1
            # Small gaps happen naturally because non-orderbook messages (acks,
            # lifecycle) also increment seq. Only call out big ones.
            if gap > 10:
                log.warning("kalshi_ws_sequence_gap", sid=sid, expected=last + 1, got=seq, gap=gap)
        self._sequence_numbers[sid] = seq

    async def listen(self) -> None:
        """Block forever, applying messages to LiveState."""
        if self._ws is None:
            raise KalshiError("WebSocket not connected")
        async for raw in self._ws:
            self._last_message_at = time.monotonic()
            self.live_state.last_ws_message_at = self._last_message_at

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("kalshi_ws_invalid_json", raw=str(raw)[:120])
                continue

            if data.get("type") == "error":
                log.error("kalshi_ws_server_error", body=data)
                continue

            try:
                msg = parse_kalshi_ws_message(data)
            except Exception as e:  # noqa: BLE001 — never crash the loop on parse
                log.warning("kalshi_ws_parse_failed", error=str(e), raw=str(data)[:120])
                continue

            if msg is None:
                continue  # unknown type, ignored by design

            if isinstance(msg, (OrderbookSnapshot, OrderbookDelta)):
                self._check_sequence(msg.sid, msg.seq)

            self._dispatch(msg)

            if self.broadcast_queue is not None:
                with contextlib.suppress(asyncio.QueueFull):
                    self.broadcast_queue.put_nowait(msg)

    def _spawn_handler(self, coro: Coroutine[Any, Any, None]) -> None:
        """Run a message handler as a tracked task, holding a strong reference
        until it finishes so it can't be GC'd mid-await (a dropped fill-persist
        is real money lost). Self-discards on completion."""
        task = asyncio.create_task(coro)
        self._handler_tasks.add(task)
        task.add_done_callback(self._handler_tasks.discard)

    def _dispatch(self, msg: KalshiWsMessage) -> None:
        """Apply a parsed message to LiveState. Side-effecting, no errors raised."""
        if isinstance(msg, OrderbookSnapshot):
            self.live_state.apply_orderbook_snapshot(msg)
        elif isinstance(msg, OrderbookDelta):
            self.live_state.apply_orderbook_delta(msg)
        elif isinstance(msg, MarketLifecycle):
            self.live_state.apply_market_lifecycle(msg)
            if self._lifecycle_handler is not None:
                self._spawn_handler(self._lifecycle_handler(msg))
        elif isinstance(msg, UserOrder):
            self.live_state.apply_user_order(msg)
            if self._user_order_handler is not None:
                self._spawn_handler(self._user_order_handler(msg))
        elif isinstance(msg, Fill):
            # LiveState only notes the liveness timestamp. The supervisor
            # wires a fill handler (bet_service.record_fill) via set_fill_handler
            # so DB persistence happens without dragging DB imports here.
            self.live_state.apply_fill(msg)
            if self._fill_handler is not None:
                self._spawn_handler(self._fill_handler(msg))
        elif isinstance(msg, Subscribed):
            # Capture the sid for the orderbook channel — this is THE sid we
            # mutate via update_subscription for the entire connection life.
            # Personal channels (fill, user_orders) also produce Subscribed
            # acks; we record their channel name for visibility but don't
            # otherwise track their sids (we never mutate them).
            if msg.msg.channel == "orderbook_delta" and self._orderbook_sid is None:
                self._orderbook_sid = msg.msg.sid
            log.info("kalshi_ws_subscribed_ack", channel=msg.msg.channel, sid=msg.msg.sid)
        elif isinstance(msg, Unsubscribed):
            if self._orderbook_sid is not None and msg.sid == self._orderbook_sid:
                self._orderbook_sid = None
            log.info("kalshi_ws_unsubscribed_ack", sid=msg.sid)
        elif isinstance(msg, Ok):
            # update_subscription ack. msg.market_tickers is the full set
            # remaining on the sid after the mutation — useful for sanity
            # checking that our local _market_tickers matches the server.
            log.info("kalshi_ws_update_ack", sid=msg.sid, server_tickers=len(msg.msg.market_tickers))

    def set_fill_handler(self, handler: Any) -> None:
        """Register an async callback fired on every Fill message.

        Signature: `async def handler(fill: Fill) -> None`. Exceptions inside
        the handler propagate via the spawned task and don't affect the WS
        loop (asyncio tasks log their own unhandled exceptions).
        """
        self._fill_handler = handler

    def set_lifecycle_handler(self, handler: Any) -> None:
        """Register an async callback fired on every MarketLifecycle event.

        Signature: `async def handler(msg: MarketLifecycle) -> None`. Used by
        the supervisor to drive BET settlement when a market hits its
        terminal state on Kalshi's side. Same exception isolation as
        set_fill_handler — spawned as its own task.
        """
        self._lifecycle_handler = handler

    def set_user_order_handler(self, handler: Any) -> None:
        """Register an async callback fired on every UserOrder event.

        Signature: `async def handler(msg: UserOrder) -> None`. Supervisor
        uses this to transition BET rows to CANCELLED when an order goes
        terminal — covers both cancels we issued and cancels the user
        made directly on kalshi.com (defense in depth alongside the
        cancel route's synchronous BET update).
        """
        self._user_order_handler = handler

    async def close(self) -> None:
        self.live_state.connected = False
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    def seconds_since_last_message(self) -> float | None:
        """Wall-clock seconds since the last WS message, or None if connected but
        no message has landed yet / not connected."""
        if self._ws is None or self._last_message_at == 0.0:
            return None
        return time.monotonic() - self._last_message_at

    def is_stale(self) -> bool:
        """True when connected but no message has arrived for STALENESS_TIMEOUT_S
        — an alive-but-silent socket whose book has frozen."""
        idle = self.seconds_since_last_message()
        return idle is not None and idle > STALENESS_TIMEOUT_S
