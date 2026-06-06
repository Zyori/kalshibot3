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
import time
from collections.abc import Coroutine
from typing import Any

import websockets
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from datetime import datetime, timedelta, timezone

from src.core.db import get_session_factory
from src.core.logging import get_logger
from src.core.ws_manager import BroadcastManager
from src.ingestion.espn_news import EspnNews
from src.ingestion.espn_scoreboard import EspnEvent, EspnScoreboard
from src.ingestion.market_discovery import FeedMarket, MarketDiscovery, MarketFeed
from src.kalshi.live_state import LiveState
from src.kalshi.rest import KalshiRestClient
from src.kalshi.ws import (
    BACKOFF_BASE_S,
    BACKOFF_MAX_S,
    KalshiWsClient,
)
from src.core.types import BetSide, BetStatus, SnapshotPhase
from src.kalshi.ws_wire import Fill, KalshiWsMessage, MarketLifecycle, UserOrder
from src.services.bet_service import (
    backfill_open_bets_precision,
    mark_bet_terminal_by_order_id,
    record_fill,
    settle_bets_for_market,
)
from src.services.fills_sync import FillsSyncer
from src.services.market_refresher import MarketRefresher
from src.services.market_tier import MarketTier, classify
from src.services.nudge_evaluator import Nudge, NudgeEvaluator
from src.services.order_reconciler import OrderReconciler
from src.services.position_sync import PositionSyncer, _mark_price_cents
from src.services.price_history import PriceHistory
from src.services.settlement_sweeper import SettlementSweeper
from src.models import Bet, Market, Position, TradeSnapshot
from src.sports.run_of_play import live_payload
from sqlalchemy import select

log = get_logger(__name__)

# Event-burst spike detection (see _detect_market_spikes). A goal/red card moves a
# match market's mid in double digits within a poll or two; normal book churn is a
# cent or two. 10¢ is deliberately wide — this is a feed-latency assist, so it should
# fire only on the unambiguous "something big happened" jumps, not on noise.
BURST_SPIKE_THRESHOLD_CENTS = 10
# Don't compare against a sample older than this — a 10¢ drift accumulated slowly over
# minutes isn't an event, it's the game evolving. We want a sharp jump, so we look at
# the mid from roughly one-to-two observer ticks ago.
BURST_SPIKE_LOOKBACK_S = 45.0
# Per-event cooldown: after a burst fires for a game, ignore further spikes on it for
# this long. The burst window itself is ~75s; without a cooldown a market that stays
# volatile post-goal would re-arm the burst every tick and hold the poller at 10s.
BURST_COOLDOWN_S = 60.0


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
        self.espn_news = EspnNews(kickoff_soon=self._wc_kickoff_soon)
        self.market_discovery = MarketDiscovery(espn=self.espn_scoreboard)
        self.market_discovery.register_refresh_callback(self._on_discovery_refresh)
        # When ESPN flips a game in->post, freeze a final-phase snapshot on its
        # positioned bets. Wired here, after market_discovery exists (the handler
        # resolves tickers through the feed).
        self.espn_scoreboard.on_game_end = self._on_games_ended
        # MarketRefresher shares the browser broadcast queue so its REST
        # snapshots (FAR-tier polls + locked-book resyncs) reach connected
        # browsers, not just LiveState. Without this, browsers see the
        # corrected state only on the next page reload.
        self.market_refresher = MarketRefresher(
            self.live_state, broadcast_queue=self.kalshi_to_browser,
            is_ws_authoritative=self.kalshi_ws.is_subscribed,
        )
        self.position_syncer = PositionSyncer(live_state=self.live_state)
        self.fills_syncer = FillsSyncer()
        self.settlement_sweeper = SettlementSweeper()
        # position_syncer fires this when a Kalshi position drops to zero
        # while we still have an OPEN bet on that market — strong signal
        # that the position was paid out and we missed the WS lifecycle event.
        self.position_syncer.set_on_position_closed(self.settlement_sweeper.trigger)
        # After every sync commit: tell browsers to refetch positions AND run
        # the +50% profit nudge off the freshly-committed rows. Fired post-commit
        # so both read the row this sync just wrote.
        self.position_syncer.set_on_synced(self._on_position_synced)

        # Edge-triggered nudge state. In-memory by design (a nudge is a
        # reminder, not money — see nudge_evaluator). The +50% trigger rides the
        # position-sync hook above; clock-75'/red-card ride the ESPN observer
        # task started in start().
        self.nudge_evaluator = NudgeEvaluator()
        self._nudge_observer_interval_s = 20.0

        # Recent-mid trajectory per subscribed market, sampled on the observer
        # tick (NOT per WS delta — that's the firehose). In-memory/ephemeral,
        # same rationale as the nudge state. Read by /partner/context.
        self.price_history = PriceHistory()

        # Event-burst: when a live game's market mid jerks sharply (likely a goal
        # or red card), tell the ESPN poller to burst so the /summary detail lands
        # fast instead of up to a baseline-cadence later. Per-event cooldown stops
        # a thrashing book from re-triggering every tick. event_ticker -> monotonic
        # ts of last burst. In-memory/ephemeral, same rationale as the nudge state.
        self._last_burst_at: dict[str, float] = {}

        # Track each ticker's last-known tier so we can detect transitions
        # (FAR→SOON, LIVE→DONE, etc.) and run the right hand-off logic.
        self._last_tier: dict[str, MarketTier] = {}

        # game event_ticker → its total-goals market tickers, registered when an
        # event page fetches them (events._fetch_total_goals). The tier
        # dispatcher unsubscribes a game's totals when the game is no longer
        # active, so totals subscriptions don't leak (totals aren't in the
        # discovery feed, so they have no tier of their own).
        self._total_goals_tickers: dict[str, list[str]] = {}

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

        # Reconciles OPEN bets whose Kalshi order was canceled outside our live
        # paths (cancel on kalshi.com, or while the backend was down). Shares the
        # ledger write lock so its transitions serialize against the WS handlers.
        self.order_reconciler = OrderReconciler(self._ledger_write_lock)

        self._tasks: list[asyncio.Task[None]] = []
        # Fire-and-forget tasks (e.g. snapshot writes) kept in a set so the event
        # loop holds a strong reference until they finish — a bare create_task
        # whose handle is dropped can be GC'd mid-flight. Self-discarding via the
        # done-callback, so this never grows unbounded.
        self._transient_tasks: set[asyncio.Task[None]] = set()

    async def _on_fill(self, fill: Fill) -> None:
        """Persist a fill to the DB. Cross-market isolation lives inside
        record_fill — non-soccer fills are logged and dropped."""
        factory = get_session_factory()
        captures: list[tuple[int, SnapshotPhase]] = []
        fill_committed = False
        try:
            async with self._ledger_write_lock:
                async with factory() as session:
                    captures = await record_fill(session, fill)
                    await session.commit()
                    fill_committed = True
        except Exception:  # noqa: BLE001
            log.exception("fill_persist_failed", trade_id=fill.msg.trade_id)
        # Trade snapshots: freeze the live run-of-play for exit post-mortems.
        # The READ happens here, synchronously, at the fill moment — the same
        # in-memory run-of-play the events route serves, before any await lets
        # the discovery poll advance it (so the frozen state matches captured_at,
        # not a later minute). The WRITE is fired off the critical path so a slow
        # snapshot insert can't delay the position/fees syncs below. Gated on
        # fill_committed so a rolled-back fill never gets a snapshot. Pre-match
        # fills (no live game) freeze just the market mid — not an error.
        if fill_committed and captures:
            rows = self._freeze_snapshot_rows(fill, captures)
            self._spawn(self._write_trade_snapshots(fill, rows))
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

    def _spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        """Run a fire-and-forget coroutine, holding a strong reference until it
        finishes so it isn't GC'd mid-flight, then self-discarding."""
        task = asyncio.create_task(coro)
        self._transient_tasks.add(task)
        task.add_done_callback(self._transient_tasks.discard)

    def _feed_market(self, ticker: str) -> FeedMarket | None:
        """The FeedMarket carrying live game state for a market ticker, or None
        if the ticker isn't in the current discovery feed (e.g. a market whose
        game-state poller isn't running). Same O(n) scan the events route uses;
        the feed is ~240 rows."""
        feed = self.market_discovery.get_feed()
        for bucket in (feed.live, feed.upcoming, feed.recent):
            for m in bucket:
                if m.ticker == ticker:
                    return m
        return None

    def _freeze_snapshot_rows(
        self, fill: Fill, captures: list[tuple[int, SnapshotPhase]]
    ) -> list[dict[str, Any]]:
        """Freeze the live run-of-play at the fill moment into trade_snapshot row
        dicts — one per (bet_id, phase). Synchronous and side-effect-free: it
        reads the SAME in-memory state the events route serves, with no await in
        between, so the frozen blob matches captured_at rather than a later poll.
        A pre-match fill (no live game in the feed) yields null run-of-play and
        just the market mid — not an error. Called on the fill path; the write
        is deferred to _write_trade_snapshots off the critical path."""
        ticker = fill.msg.ticker
        fm = self._feed_market(ticker)
        espn = fm.espn_event if fm is not None else None
        rop = live_payload(espn)
        clock = fm.espn_clock if fm is not None else None
        score_home = espn.home_stats.score if espn is not None else None
        score_away = espn.away_stats.score if espn is not None else None

        book = self.live_state.books.get(ticker)
        mid = (
            _mark_price_cents(book, BetSide.YES)
            if book is not None and not book.is_locked
            else None
        )
        tape = [{"mid_cents": m} for _, m in self.price_history.series(ticker)]
        captured_at = fill.msg.ts if fill.msg.ts is not None else datetime.now(timezone.utc)

        return [
            {
                "bet_id": bet_id,
                "phase": phase.value,
                "captured_at": captured_at,
                "game_clock": clock,
                "score_home": score_home,
                "score_away": score_away,
                "run_of_play_json": rop,
                "market_mid_cents": mid,
                "price_history_json": tape or None,
            }
            for bet_id, phase in captures
        ]

    async def _write_trade_snapshots(
        self, fill: Fill, rows: list[dict[str, Any]]
    ) -> None:
        """Persist pre-frozen snapshot rows. Fired off the fill critical path so
        a slow insert can't delay the position/fees syncs. Own try/except — a
        logbook nicety must never surface as an unhandled task exception.

        Idempotent across replays via on_conflict_do_nothing on the unique
        (bet_id, phase) constraint: first fill wins, no read-modify."""
        stmt = sqlite_insert(TradeSnapshot).on_conflict_do_nothing(
            index_elements=["bet_id", "phase"]
        )
        try:
            factory = get_session_factory()
            async with factory() as session:
                await session.execute(stmt, rows)
                await session.commit()
            log.info(
                "trade_snapshots_captured",
                trade_id=fill.msg.trade_id,
                ticker=fill.msg.ticker,
                phases=[r["phase"] for r in rows],
                had_run_of_play=rows[0]["run_of_play_json"] is not None,
            )
        except Exception:  # noqa: BLE001 — a logbook nicety must never break anything
            log.exception(
                "trade_snapshot_capture_failed", trade_id=fill.msg.trade_id
            )

    async def _on_games_ended(self, ended: list[EspnEvent]) -> None:
        """Freeze a `final`-phase trade snapshot on every positioned bet whose
        game just flipped in->post. The ended EspnEvents carry the fresh final
        score / clock / status_detail (FT/AET/Penalties); the discovery feed is
        the only ticker<->espn_id bridge, so we resolve tickers through it while
        the just-ended markets are still in the feed (recent bucket).

        "Positioned" = the bet has an `entry` snapshot — i.e. it actually filled
        (cancelled orders never get one). on_conflict_do_nothing on (bet_id,
        phase) dedupes if a later poll re-sees the same game as post.

        Off the poll's critical path with its own try/except (the poller already
        wraps this call too): a final-snapshot write must never disturb polling."""
        feed = self.market_discovery.get_feed()
        by_espn_id: dict[str, list[str]] = {}
        for bucket in (feed.live, feed.upcoming, feed.recent):
            for m in bucket:
                if m.espn_event is not None:
                    by_espn_id.setdefault(m.espn_event.espn_id, []).append(m.ticker)

        rows: list[dict[str, Any]] = []
        factory = get_session_factory()
        async with factory() as session:
            for ev in ended:
                tickers = by_espn_id.get(ev.espn_id)
                if not tickers:
                    # Game ended but its markets already aged out of the feed —
                    # nothing to anchor a bet to. Rare; logged, not stamped.
                    log.info("game_end_no_feed_markets", espn_id=ev.espn_id)
                    continue
                # Bets that filled (have an `entry` snapshot) on these tickers.
                # One query per game; games are few. We don't filter out bets
                # that already have a `final` — the on_conflict_do_nothing on
                # (bet_id, phase) below is the idempotency guard, so a re-seen
                # post-state poll just no-ops rather than needing a pre-check.
                entry_q = (
                    select(Bet.id)
                    .join(Market, Market.id == Bet.market_id)
                    .join(TradeSnapshot, TradeSnapshot.bet_id == Bet.id)
                    .where(Market.kalshi_ticker.in_(tickers))
                    .where(TradeSnapshot.phase == SnapshotPhase.ENTRY.value)
                )
                bet_ids = set((await session.execute(entry_q)).scalars().all())
                rop = live_payload(ev)
                for bet_id in bet_ids:
                    rows.append({
                        "bet_id": bet_id,
                        "phase": SnapshotPhase.FINAL.value,
                        "captured_at": datetime.now(timezone.utc),
                        "game_clock": ev.clock_display,
                        "score_home": ev.home_stats.score,
                        "score_away": ev.away_stats.score,
                        "status_detail": ev.status_detail,
                        "run_of_play_json": rop,
                        "market_mid_cents": None,
                        "price_history_json": None,
                    })

            if not rows:
                return
            try:
                stmt = sqlite_insert(TradeSnapshot).on_conflict_do_nothing(
                    index_elements=["bet_id", "phase"]
                )
                await session.execute(stmt, rows)
                await session.commit()
                log.info(
                    "final_snapshots_captured",
                    games=len(ended),
                    bets=len(rows),
                    details=[ev.status_detail for ev in ended],
                )
            except Exception:  # noqa: BLE001 — a logbook nicety must never break polling
                log.exception("final_snapshot_capture_failed")

    async def _on_position_synced(self) -> None:
        """Post-commit hook after a position reconciliation: tell browsers to
        refetch, then run the +50% profit nudge off the just-written rows."""
        await self.broadcast.broadcast_app_event({"type": "position_synced"})
        try:
            await self._evaluate_profit_nudges()
        except Exception:  # noqa: BLE001 — a nudge failure must never break sync
            log.exception("profit_nudge_eval_failed")

    async def _broadcast_nudges(self, nudges: list[Nudge]) -> None:
        """Fan each nudge out as its own app event. Discrete + low-frequency →
        the browser invalidates ['nudges'] and re-reads. Keyed by subject+trigger
        so two nudges in one flush window don't collapse."""
        for n in nudges:
            await self.broadcast.broadcast_app_event(
                {
                    "type": "nudge",
                    "subject": n.subject,
                    "trigger": n.trigger,
                    "label": n.label,
                }
            )

    async def _evaluate_profit_nudges(self) -> None:
        """Read open positions + their unrealized return %, fire +50% nudges.
        Same %-of-cost-basis math the dashboard derives (unrealized / cost)."""
        factory = get_session_factory()
        async with factory() as session:
            rows = (await session.execute(select(Position))).scalars().all()
        pos_pct: list[tuple[str, float | None]] = []
        for p in rows:
            entry = p.cost_basis_cents
            pnl = p.unrealized_pnl_cents
            pct: float | None = None
            if entry and entry > 0 and pnl is not None:
                pct = (pnl / entry) * 100.0
            pos_pct.append((p.kalshi_ticker, pct))
        nudges = self.nudge_evaluator.evaluate_profit(pos_pct)
        if nudges:
            await self._broadcast_nudges(nudges)

    async def _nudge_observer_loop(self) -> None:
        """Watch the discovery feed for clock-75' and red-card crossings on live
        games. Reads the same ESPN-derived FeedMarket state the event API shows;
        no new external poll (ESPN already polls). Edge-triggered via the
        evaluator. Swallows per-cycle errors so a bad read never kills the loop."""
        while True:
            try:
                await asyncio.sleep(self._nudge_observer_interval_s)
                feed = self.market_discovery.get_feed()
                # One row per event is enough for clock/red-card — dedupe by
                # event_ticker (the two sides of a match share ESPN state).
                seen: dict[str, tuple[str, str | None, int]] = {}
                for m in feed.live:
                    if m.event_ticker in seen:
                        continue
                    reds = 0
                    if m.espn_event is not None:
                        reds = (
                            m.espn_event.home_stats.red_cards
                            + m.espn_event.away_stats.red_cards
                        )
                    seen[m.event_ticker] = (m.event_ticker, m.espn_clock, reds)
                nudges = self.nudge_evaluator.evaluate_live_games(list(seen.values()))
                if nudges:
                    await self._broadcast_nudges(nudges)
                self._sample_prices()
                # After sampling — needs this tick's mid in the series to detect a
                # jump against the prior tick.
                self._detect_market_spikes(feed.live)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("nudge_observer_cycle_failed")

    def track_total_goals(self, game_event_ticker: str, total_tickers: list[str]) -> None:
        """Register a game's total-goals tickers so the tier dispatcher keeps
        them subscribed while the game is active and unsubscribes them when it
        goes away. Called by the event endpoint when it fetches totals."""
        self._total_goals_tickers[game_event_ticker] = total_tickers

    def _wc_kickoff_soon(self) -> bool:
        """True when a World Cup game kicks off within the next hour — the window
        confirmed XIs and late injury news drop. Drives the news poller's fast
        cadence. Reads the discovery feed's upcoming WC games (open_time)."""
        now = datetime.now(timezone.utc)
        horizon = now + timedelta(hours=1)
        feed = self.market_discovery.get_feed()
        for m in feed.upcoming:
            if m.ticker.startswith("KXWCGAME") and m.open_time is not None:
                if now <= m.open_time <= horizon:
                    return True
        return False

    def _sample_prices(self) -> None:
        """Record one YES-mid sample per currently-subscribed book into the price
        buffer, and prune series for markets we no longer follow. Sampling on
        this tick (not per delta) bounds the write rate; the mid uses the same
        helper position marks use, so the tape and the marks can't diverge.

        Scoped to the WS-subscribed set, NOT live_state.books: books only ever
        grows (release_ws_ownership flips a flag, never deletes), so pruning
        against books would never drop anything and the buffer would leak one
        deque per market ever seen. The subscription set shrinks on unsubscribe,
        so it's the correct steady-state boundary. Locked/crossed zombie books
        are skipped — their derived mid is nonsense until the REST resync."""
        subscribed = self.kalshi_ws.subscribed_tickers()
        for ticker in subscribed:
            book = self.live_state.books.get(ticker)
            if book is None or book.is_locked:
                continue
            mid = _mark_price_cents(book, BetSide.YES)
            if mid is not None:
                self.price_history.record(ticker, mid)
        self.price_history.retain_only(subscribed)

    def _detect_market_spikes(self, live_markets: list[FeedMarket]) -> None:
        """Burst the ESPN poller when a live game's market mid jumps sharply — the
        book reprices on a goal/red card seconds before the ESPN feed catches up, so
        a sharp jump is our earliest signal that an event just fired. Bursting gets
        the /summary detail (which event, the shots) within ~10s instead of ~40s.

        Reads the same price_history the tape uses; no new sampling. Per-event: a
        spike on either side of a match bursts that game's poll once, then a cooldown
        suppresses re-triggering while the post-event book stays volatile. Must run
        AFTER _sample_prices so this tick's mid is already in the series."""
        now = time.monotonic()
        # One burst decision per event — the two sides of a match move together, and
        # the poller burst is global anyway, so dedupe by event_ticker.
        spiked: set[str] = set()
        for m in live_markets:
            if m.event_ticker in spiked:
                continue
            if now - self._last_burst_at.get(m.event_ticker, 0.0) < BURST_COOLDOWN_S:
                continue
            if self._ticker_spiked(m.ticker, now):
                spiked.add(m.event_ticker)

        for event_ticker in spiked:
            self._last_burst_at[event_ticker] = now
            self.espn_scoreboard.request_burst()
            log.info("espn_burst_triggered", event_ticker=event_ticker)
        # Bounded growth: drop cooldown entries for events no longer live.
        live_events = {m.event_ticker for m in live_markets}
        for dead in self._last_burst_at.keys() - live_events:
            self._last_burst_at.pop(dead, None)

    def _ticker_spiked(self, ticker: str, now: float) -> bool:
        """True if this market's mid moved >= the spike threshold between a recent
        sample (~one observer tick ago) and the latest. Compares the newest mid to
        the oldest sample still inside the lookback window — a sharp jump, not a slow
        drift. False if the series is too short to judge."""
        series = self.price_history.series(ticker)
        if len(series) < 2:
            return False
        latest_ts, latest_mid = series[-1]
        # Oldest sample within the lookback window (series is oldest-first).
        for ts, mid in series:
            if latest_ts - ts <= BURST_SPIKE_LOOKBACK_S:
                return abs(latest_mid - mid) >= BURST_SPIKE_THRESHOLD_CENTS
        return False

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

        # Total-goals subscriptions follow their game's lifecycle. A game's
        # event_ticker is the prefix of its market tickers (KX…GAME-DATE…-SIDE),
        # so derive the set of currently-active game events from subscribe_tickers
        # and keep those games' totals subscribed; unsubscribe + forget totals for
        # any game no longer active (its markets dropped out of SOON/LIVE).
        active_events = {t.rsplit("-", 1)[0] for t in subscribe_tickers}
        for game_event, total_tickers in list(self._total_goals_tickers.items()):
            if game_event in active_events:
                subscribe_tickers.update(total_tickers)
            else:
                done_tickers.update(total_tickers)
                del self._total_goals_tickers[game_event]

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

        # One-shot: re-derive non-terminal bets from their fills so any
        # recorded under the old floored-VWAP math pick up the exact
        # entry/stake/realized figures. Safe + idempotent (pure recompute from
        # bet_fill rows); runs before the loops so the first API read is fresh.
        try:
            factory = get_session_factory()
            async with factory() as session:
                n = await backfill_open_bets_precision(session)
                await session.commit()
            log.info("bet_precision_backfill", recomputed=n)
        except Exception:  # noqa: BLE001 — never block startup on the backfill
            log.exception("bet_precision_backfill_failed")

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
            self.espn_news.run(), name="espn_news",
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
        self._tasks.append(asyncio.create_task(
            self.order_reconciler.run(), name="order_reconcile",
        ))
        self._tasks.append(asyncio.create_task(
            self._nudge_observer_loop(), name="nudge_observer",
        ))
        log.info("supervisor_started", tasks=len(self._tasks))

    async def stop(self) -> None:
        """Cancel every task and await its cleanup."""
        await self.settlement_sweeper.stop()
        await self.order_reconciler.stop()
        await self.fills_syncer.stop()
        await self.position_syncer.stop()
        await self.market_discovery.stop()
        await self.market_refresher.stop()
        await self.espn_scoreboard.stop()
        await self.espn_news.stop()
        await self.broadcast.stop()
        # Let in-flight snapshot writes finish — they're short inserts, and
        # cancelling one mid-write just loses a logbook row for no benefit.
        if self._transient_tasks:
            with contextlib.suppress(Exception):
                await asyncio.wait(self._transient_tasks, timeout=2.0)
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await t
        self._tasks.clear()
        log.info("supervisor_stopped")
