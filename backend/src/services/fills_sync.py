"""Fills sync — pull /portfolio/fills, populate bet_fill.fee_cents.

WS fill events don't carry fees. Kalshi's REST /portfolio/fills does (via
`fee_cost`). This sweep is the only path that authoritatively populates
bet_fill.fee_cents — never a formula.

Two roles:
  1. Fee backfill: for every bet_fill with fee_cents IS NULL, find its
     matching REST row by trade_id and copy fee_cost in.
  2. External-fill audit: REST may show fills that never arrived via WS
     (placed directly on kalshi.com). Per feedback_no_external_fill_reconciliation
     we record those as bet_fill rows with bet_id=NULL — visible in audit,
     never auto-bound to a bet.

After updating fee_cents we recompute the affected bet's entry_fees_cents
and exit_fees_cents as plain sums over its bet_fill rows.

Cross-market isolation: tradeable tickers only (soccer + combos, via
is_tradeable_ticker). Out-of-scope fills (politics/crypto/other) are skipped at
the top of the loop — never persisted to bet_fill. Combo fills ARE fee-synced
here, same as soccer.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.db import get_session_factory
from src.core.locks import ledger_guard
from src.core.logging import get_logger
from src.kalshi.rest import KalshiRestClient
from src.kalshi.schemas import Fill as RestFill
from src.models import Bet, BetFill
from src.services.bet_service import recompute_bet_from_fills
from src.sports.tradeable import is_tradeable_ticker

log = get_logger(__name__)

POLL_INTERVAL_S = 30


async def _ingest_rest_fill(
    session: AsyncSession,
    *,
    rest_fill: RestFill,
) -> set[int]:
    """Upsert one REST fill. Returns the set of bet_ids whose fees need
    recomputing.

    A WS sell that crossed multiple openers creates one bet_fill per opener
    via the synthetic-trade_id convention `{trade_id}#{opener_id}`. The
    Kalshi REST row reports a single fee_cost for the whole original trade.
    We split that fee across the synthetic rows by quantity_centi so each
    bet's exit_fees_cents reflects its share of the actual cost.
    """
    if not is_tradeable_ticker(rest_fill.ticker):
        return set()

    # Match the canonical row plus any cross-opener splits.
    rows = (await session.execute(
        select(BetFill).where(
            (BetFill.trade_id == rest_fill.trade_id)
            | (BetFill.trade_id.like(f"{rest_fill.trade_id}#%"))
        )
    )).scalars().all()

    if rows:
        total_centi = sum(r.quantity_centi for r in rows)
        if total_centi <= 0:
            return set()
        # Back-link orphan rows whose bet didn't exist when they were first
        # ingested. A WS buy fill that arrived before the orders route
        # committed the Bet was dropped from the WS path; but if a row
        # somehow exists with bet_id=NULL and a Bet now matches order_id,
        # bind it so the bet's aggregates pick up the fill.
        newly_bound: set[int] = set()
        for row in rows:
            if row.bet_id is None and row.action == "buy":
                bet = await session.scalar(
                    select(Bet).where(Bet.kalshi_order_id == row.order_id)
                )
                if bet is not None:
                    row.bet_id = bet.id
                    # Force a fee recompute for this bet even if the fee value
                    # below is unchanged: binding the row to the bet is itself
                    # the change the bet's aggregate must pick up. Without this,
                    # an already-fee'd fill that gets back-linked leaves
                    # bet.entry_fees_cents stale at 0.
                    newly_bound.add(bet.id)

        # Pro-rate the fee. Use largest-remainder rounding so cents sum
        # back to the original fee exactly (no off-by-one drift).
        allocations: list[tuple[BetFill, int, int]] = []  # (row, base_cents, remainder)
        running = 0
        for r in rows:
            num = rest_fill.fee_cents * r.quantity_centi
            base = num // total_centi
            remainder = num - base * total_centi
            allocations.append((r, base, remainder))
            running += base
        # Distribute the leftover cents to rows with the highest remainder.
        leftover = rest_fill.fee_cents - running
        allocations.sort(key=lambda x: x[2], reverse=True)
        affected: set[int] = set(newly_bound)
        synced_at = datetime.now(timezone.utc)
        for idx, (row, base, _) in enumerate(allocations):
            new_fee = base + (1 if idx < leftover else 0)
            if row.fee_cents != new_fee:
                row.fee_cents = new_fee
                row.fee_synced_at = synced_at
                if row.bet_id is not None:
                    affected.add(row.bet_id)
        return affected

    # REST saw a fill WS didn't. External (kalshi.com) or a missed event.
    # Record for audit; leave bet_id NULL.
    price = (
        rest_fill.yes_price if rest_fill.side == "yes" else rest_fill.no_price
    )
    new_row = BetFill(
        bet_id=None,
        trade_id=rest_fill.trade_id,
        order_id=rest_fill.order_id,
        ticker=rest_fill.ticker,
        side=rest_fill.side,
        action=rest_fill.action,
        price_cents=price,
        quantity_centi=rest_fill.count_centi,
        fee_cents=rest_fill.fee_cents,
        is_taker=rest_fill.is_taker,
        fee_synced_at=datetime.now(timezone.utc),
        created_time=rest_fill.created_time,
    )
    session.add(new_row)
    log.info(
        "external_fill_recorded",
        trade_id=rest_fill.trade_id,
        ticker=rest_fill.ticker,
        action=rest_fill.action,
    )
    return set()


async def sync_fills_once(
    min_ts: int | None = None, lock: asyncio.Lock | None = None
) -> dict[str, int]:
    """One pass over /portfolio/fills.

    `min_ts` is Kalshi's "fills created at or after this epoch second"
    filter. The FillsSyncer passes the highest created_time it has seen
    minus a small overlap so any race-window fills are caught.
    Idempotent — every bet_fill is keyed by trade_id, so re-processing
    overlap is a no-op. The tradeable-ticker filter keeps the work bounded.

    `lock` is the supervisor's ledger-write lock. recompute_bet_from_fills
    mutates Bet rows the WS handlers also write, so the per-fill DB work
    serializes under it. The network pull stays outside the lock.

    Per-fill isolation: each fill is ingested + recomputed + committed on its
    own. A single malformed REST row rolls back only itself and is logged,
    rather than aborting the whole pass — which would otherwise wedge the
    watermark and re-poison every 30s sweep, silently starving fee backfill.
    """
    rest_fills: list[RestFill] = []
    async with KalshiRestClient() as client:
        cursor: str | None = None
        while True:
            resp = await client.get_fills(cursor=cursor, min_ts=min_ts)
            rest_fills.extend(resp.fills)
            cursor = resp.cursor
            if not cursor:
                break

    factory = get_session_factory()
    enriched = 0
    recomputed = 0
    failed = 0
    max_ts = 0
    earliest_failure_ts: int | None = None
    for rf in rest_fills:
        try:
            async with ledger_guard(lock):
                async with factory() as session:
                    affected = await _ingest_rest_fill(session, rest_fill=rf)
                    if affected:
                        enriched += 1
                    await session.flush()
                    for bet_id in affected:
                        bet = await session.get(Bet, bet_id)
                        if bet is not None:
                            # Full re-derive from bet_fill: catches orphan-buy
                            # back-links (entry_price + stake_cents reflect the
                            # now-bound fill) AND routine fee-only updates.
                            await recompute_bet_from_fills(session, bet=bet)
                            recomputed += 1
                    await session.commit()
        except Exception:  # noqa: BLE001
            # Per-fill isolation: a malformed row rolls back only itself and is
            # logged, rather than aborting the whole pass. The watermark is held
            # at/below the earliest failure so the next sweep re-fetches and
            # retries it (idempotent) instead of advancing past a fill that was
            # never processed.
            log.exception("fills_sync_row_failed", trade_id=rf.trade_id)
            failed += 1
            if rf.created_time is not None:
                ts = int(rf.created_time.timestamp())
                if earliest_failure_ts is None or ts < earliest_failure_ts:
                    earliest_failure_ts = ts
            continue
        if rf.created_time is not None:
            ts = int(rf.created_time.timestamp())
            if ts > max_ts:
                max_ts = ts

    # Don't let the watermark step past an unprocessed fill: cap it just below
    # the earliest failure so that row is re-fetched next pass.
    if earliest_failure_ts is not None:
        max_ts = min(max_ts, earliest_failure_ts - 1) if max_ts else 0

    log.info(
        "fills_sync_complete",
        rest_fills=len(rest_fills),
        enriched=enriched,
        bets_recomputed=recomputed,
        failed=failed,
        since_ts=min_ts,
        max_ts=max_ts,
    )
    return {
        "rest_fills": len(rest_fills),
        "enriched": enriched,
        "bets_recomputed": recomputed,
        "failed": failed,
        "max_ts": max_ts,
    }


class FillsSyncer:
    """Long-running poller. Lives on the supervisor.

    Maintains an in-memory watermark cursor (epoch seconds). First tick
    after process start does a full pull (min_ts=None); subsequent ticks
    only fetch fills newer than `watermark - OVERLAP_SECONDS`, which
    bounds steady-state work to ~O(new_fills) instead of O(history).
    Watermark loss on restart is fine — a full pull is correct, just more
    work. Idempotent via bet_fill.trade_id."""

    OVERLAP_SECONDS = 60
    """How far back to re-fetch on each incremental tick. Catches fills
    that landed during the previous sweep (created_time could be earlier
    than the watermark we observed) and any clock skew between client/
    Kalshi. 60s is enough to cover a slow sweep."""

    def __init__(self, lock: asyncio.Lock | None = None) -> None:
        self._stopped = False
        self._last_run_at: float | None = None
        self._watermark_ts: int | None = None
        self._lock = lock

    @property
    def last_run_age_s(self) -> float | None:
        if self._last_run_at is None:
            return None
        return time.monotonic() - self._last_run_at

    async def run(self) -> None:
        await self._tick()
        while not self._stopped:
            await asyncio.sleep(POLL_INTERVAL_S)
            await self._tick()

    async def _tick(self) -> None:
        try:
            since = (
                None if self._watermark_ts is None
                else max(0, self._watermark_ts - self.OVERLAP_SECONDS)
            )
            result = await sync_fills_once(min_ts=since, lock=self._lock)
            max_ts = result.get("max_ts", 0)
            if max_ts > 0 and (self._watermark_ts is None or max_ts > self._watermark_ts):
                self._watermark_ts = max_ts
            self._last_run_at = time.monotonic()
        except Exception:  # noqa: BLE001
            log.exception("fills_sync_failed")

    async def trigger(self) -> None:
        """Fire an extra sweep now — called after WS fill events to backfill
        fees ~immediately rather than waiting for the next interval."""
        await self._tick()

    async def stop(self) -> None:
        self._stopped = True
