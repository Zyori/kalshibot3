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
from src.ingestion.espn_scoreboard import EspnScoreboard
from src.ingestion.market_discovery import MarketDiscovery, MarketFeed
from src.kalshi.live_state import LiveState
from src.kalshi.rest import KalshiRestClient
from src.kalshi.ws import (
    BACKOFF_BASE_S,
    BACKOFF_MAX_S,
    KalshiWsClient,
)
from src.core.types import BetStatus
from src.kalshi.ws_wire import Fill, KalshiWsMessage, MarketLifecycle, UserOrder
from src.services.bet_service import (
    mark_bet_terminal_by_order_id,
    record_fill,
    settle_bets_for_market,
)
from src.services.fills_sync import FillsSyncer
from src.services.market_refresher import MarketRefresher
from src.services.market_tier import MarketTier, classify
from src.services.position_sync import PositionSyncer
from src.services.settlement_sweeper import SettlementSweeper

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
        # Lifecycle handler: settle OPEN bets when Kalshi reports the market
        # as terminal (status=settled with a settlement_value).
        self.kalshi_ws.set_lifecycle_handler(self._on_market_lifecycle)
        # User order handler: transition BET rows to CANCELLED on cancel.
        self.kalshi_ws.set_user_order_handler(self._on_user_order)

        # ESPN scoreboard is the source of truth for kickoff times — Kalshi's
        # `occurrence_datetime` is the settlement deadline, off by 3+ hours
        # on some series. The matcher inside market_discovery reads from
        # this shared snapshot.
        self.espn_scoreboard = EspnScoreboard()
        self.market_discovery = MarketDiscovery(espn=self.espn_scoreboard)
        self.market_discovery.register_refresh_callback(self._on_discovery_refresh)
        # MarketRefresher shares the browser broadcast queue so its REST
        # snapshots (FAR-tier polls + locked-book resyncs) reach connected
        # browsers, not just LiveState. Without this, browsers see the
        # corrected state only on the next page reload.
        self.market_refresher = MarketRefresher(
            self.live_state, broadcast_queue=self.kalshi_to_browser,
            is_ws_authoritative=self.kalshi_ws.is_subscribed,
        )
        self.position_syncer = PositionSyncer()
        self.fills_syncer = FillsSyncer()
        self.settlement_sweeper = SettlementSweeper()
        # position_syncer fires this when a Kalshi position drops to zero
        # while we still have an OPEN bet on that market — strong signal
        # that the position was paid out and we missed the WS lifecycle event.
        self.position_syncer.set_on_position_closed(self.settlement_sweeper.trigger)
        # After every sync commit, nudge browsers to refetch positions. Fired
        # post-commit so the refetch reads the row this sync just wrote — kills
        # the gap between a fill and the position showing up in the UI.
        self.position_syncer.set_on_synced(self._broadcast_position_synced)

        # Track each ticker's last-known tier so we can detect transitions
        # (FAR→SOON, LIVE→DONE, etc.) and run the right hand-off logic.
        self._last_tier: dict[str, MarketTier] = {}

        # Set externally (main.lifespan) so the fill handler can invalidate
        # the cached balance after a fill changes the account state.
        self.app_state: Any = None

        # Serializes the DB-mutating WS handlers (fill, settlement, cancel).
        # ws.py dispatches each as a detached create_task, so without this two
        # fills on the same bet — or a fill racing a settlement — would run
        # concurrent read-modify-write on the same bet_fill/Bet rows in separate
        # sessions, and the later commit would clobber the earlier. All three
        # handlers re-derive bet aggregates from rows visible in their own
        # transaction, which is only correct under serialized writes.
        self._ledger_write_lock = asyncio.Lock()

        self._tasks: list[asyncio.Task[None]] = []

    async def _on_fill(self, fill: Fill) -> None:
        """Persist a fill to the DB. Cross-market isolation lives inside
        record_fill — non-soccer fills are logged and dropped."""
        factory = get_session_factory()
        try:
            async with self._ledger_write_lock:
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
        # Fee enrichment: REST /portfolio/fills carries the authoritative
        # fee_cost per fill; WS doesn't. Trigger a sweep so the bet_fill
        # row we just inserted gets its fee populated within seconds.
        try:
            await self.fills_syncer.trigger()
        except Exception:  # noqa: BLE001
            log.exception("fills_sync_after_fill_failed")
        # Invalidate the cached balance so the next /health refreshes it.
        # Cheap signal; avoids holding a Kalshi REST call inside the fill
        # handler's critical path.
        if self.app_state is not None:
            self.app_state._balance_refreshed_at_mono = 0.0

    async def _broadcast_position_synced(self) -> None:
        """Tell browsers a position reconciliation just committed. They
        refetch the event/positions/ledger queries off this signal."""
        await self.broadcast.broadcast_app_event({"type": "position_synced"})

    async def _on_market_lifecycle(self, msg: MarketLifecycle) -> None:
        """Settle BETs when Kalshi reports a terminal market status.

        Kalshi sends market_lifecycle events on transitions. We only act on
        ones that carry a settlement_value — that's the signal the market
        has resolved and we know who paid out. Status alone (e.g.
        'closed' = trading halted but not settled) doesn't settle bets.
        """
        if msg.msg.settlement_value is None:
            return
        if msg.msg.status not in ("settled", "determined", "finalized"):
            # Status without settlement_value is a soft transition (paused,
            # closed-for-trading). Don't burn DB work on it.
            return
        factory = get_session_factory()
        try:
            async with self._ledger_write_lock:
                async with factory() as session:
                    await settle_bets_for_market(
                        session,
                        ticker=msg.msg.market_ticker,
                        settlement_value_cents=msg.msg.settlement_value,
                    )
                    await session.commit()
        except Exception:  # noqa: BLE001
            log.exception("settle_failed", ticker=msg.msg.market_ticker)
        # A settlement changes the balance (payout credited). Invalidate
        # the cached balance so the banner reflects the payout fast.
        if self.app_state is not None:
            self.app_state._balance_refreshed_at_mono = 0.0

    async def _on_user_order(self, msg: UserOrder) -> None:
        """Transition BET rows to CANCELLED when a user_order goes terminal.

        Kalshi sends user_order events on every state change of one of our
        orders. We only care about terminal states here (canceled, executed)
        since OPEN -> CANCELLED is the transition we need to record;
        executed is left as-is because the fill handler already covers the
        WON/LOST path via record_fill + settlement.

        Defense in depth alongside the cancel route's synchronous BET
        update: catches cancels done directly on kalshi.com, and the rare
        case where the cancel route completes the Kalshi call but the
        post-cancel DB write fails (then the WS event reconciles).
        """
        if msg.msg.status != "canceled":
            return
        factory = get_session_factory()
        try:
            async with self._ledger_write_lock:
                async with factory() as session:
                    await mark_bet_terminal_by_order_id(
                        session,
                        order_id=msg.msg.order_id,
                        status=BetStatus.CANCELLED,
                    )
                    await session.commit()
        except Exception:  # noqa: BLE001
            log.exception("user_order_cancel_persist_failed", order_id=msg.msg.order_id)

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
        # ESPN before market_discovery so the first discovery cycle has a
        # populated snapshot. EspnScoreboard.run() does its initial fetch
        # synchronously before entering the poll loop.
        self._tasks.append(asyncio.create_task(
            self.espn_scoreboard.run(), name="espn_scoreboard",
        ))
        self._tasks.append(asyncio.create_task(
            self.market_discovery.run(), name="market_discovery",
        ))
        self._tasks.append(asyncio.create_task(
            self.market_refresher.run(), name="market_refresher",
        ))
        self._tasks.append(asyncio.create_task(
            self.market_refresher.run_locked_sweep(), name="locked_book_sweep",
        ))
        self._tasks.append(asyncio.create_task(
            self.position_syncer.run(), name="position_sync",
        ))
        self._tasks.append(asyncio.create_task(
            self.fills_syncer.run(), name="fills_sync",
        ))
        self._tasks.append(asyncio.create_task(
            self.settlement_sweeper.run(), name="settlement_sweep",
        ))
        log.info("supervisor_started", tasks=len(self._tasks))

    async def stop(self) -> None:
        """Cancel every task and await its cleanup."""
        await self.settlement_sweeper.stop()
        await self.fills_syncer.stop()
        await self.position_syncer.stop()
        await self.market_discovery.stop()
        await self.market_refresher.stop()
        await self.espn_scoreboard.stop()
        await self.broadcast.stop()
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await t
        self._tasks.clear()
        log.info("supervisor_stopped")
