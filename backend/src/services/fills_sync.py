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

Cross-market isolation: soccer-only. Non-soccer fills are skipped at the
top of the loop — never persisted to bet_fill.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.db import get_session_factory
from src.core.logging import get_logger
from src.kalshi.rest import KalshiRestClient
from src.kalshi.schemas import Fill as RestFill
from src.models import Bet, BetFill
from src.sports.soccer import is_soccer_ticker

log = get_logger(__name__)

POLL_INTERVAL_S = 30


async def _recompute_bet_fees(session: AsyncSession, *, bet_id: int) -> None:
    """Recompute entry_fees_cents and exit_fees_cents from bet_fill rows.

    Always derived; never accumulated. If a fee value changes (rare —
    Kalshi could in theory waive after the fact), the bet reflects the
    latest truth.
    """
    bet = await session.get(Bet, bet_id)
    if bet is None:
        return
    fills = (await session.execute(
        select(BetFill).where(BetFill.bet_id == bet_id)
    )).scalars().all()
    bet.entry_fees_cents = sum(
        (f.fee_cents or 0) for f in fills if f.action == "buy"
    )
    bet.exit_fees_cents = sum(
        (f.fee_cents or 0) for f in fills if f.action == "sell"
    )


async def _ingest_rest_fill(
    session: AsyncSession,
    *,
    rest_fill: RestFill,
) -> int | None:
    """Upsert one REST fill. Returns the bet_id whose fees need recomputing
    (or None if nothing changed)."""
    if not is_soccer_ticker(rest_fill.ticker):
        return None

    existing = await session.scalar(
        select(BetFill).where(BetFill.trade_id == rest_fill.trade_id)
    )
    if existing is not None:
        # Already have the row (typical case: WS got it first). Update fee
        # if it's still pending. Don't touch bet_id — the WS path's FIFO
        # matching is authoritative for attribution.
        changed = False
        if existing.fee_cents is None or existing.fee_cents != rest_fill.fee_cents:
            existing.fee_cents = rest_fill.fee_cents
            existing.fee_synced_at = datetime.now(timezone.utc)
            changed = True
        return existing.bet_id if changed else None

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
    return None


async def sync_fills_once() -> dict[str, int]:
    """One full pass over /portfolio/fills.

    Strategy: paginate the whole history. Idempotent — every bet_fill is
    keyed by trade_id, so re-processing a fill we've already enriched is a
    no-op. We could optimize with a since-cursor later; for now correctness
    over latency. Soccer-only filter keeps the work bounded.
    """
    rest_fills: list[RestFill] = []
    async with KalshiRestClient() as client:
        cursor: str | None = None
        while True:
            resp = await client.get_fills(cursor=cursor)
            rest_fills.extend(resp.fills)
            cursor = resp.cursor
            if not cursor:
                break

    factory = get_session_factory()
    enriched = 0
    affected_bet_ids: set[int] = set()
    async with factory() as session:
        for rf in rest_fills:
            bet_id = await _ingest_rest_fill(session, rest_fill=rf)
            if bet_id is not None:
                affected_bet_ids.add(bet_id)
                enriched += 1
        await session.flush()
        for bet_id in affected_bet_ids:
            await _recompute_bet_fees(session, bet_id=bet_id)
        await session.commit()

    log.info(
        "fills_sync_complete",
        rest_fills=len(rest_fills),
        enriched=enriched,
        bets_recomputed=len(affected_bet_ids),
    )
    return {
        "rest_fills": len(rest_fills),
        "enriched": enriched,
        "bets_recomputed": len(affected_bet_ids),
    }


class FillsSyncer:
    """Long-running poller. Lives on the supervisor."""

    def __init__(self) -> None:
        self._stopped = False
        self._last_run_at: float | None = None

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
            await sync_fills_once()
            self._last_run_at = time.monotonic()
        except Exception:  # noqa: BLE001
            log.exception("fills_sync_failed")

    async def trigger(self) -> None:
        """Fire an extra sweep now — called after WS fill events to backfill
        fees ~immediately rather than waiting for the next interval."""
        await self._tick()

    async def stop(self) -> None:
        self._stopped = True
