"""Order reconciliation — clear OPEN bets whose Kalshi order was canceled.

A bet transitions OPEN -> CANCELLED through two live paths today: the app's own
DELETE /orders route, and the WS user_order(canceled) handler. Both miss cases:

  - You cancel a resting order directly on kalshi.com.
  - The backend was down (or the WS dropped) when the cancel happened — the
    user_order stream carries no snapshot of orders that were already resting,
    so the canceled event is never seen.

In those cases the bet stays OPEN forever and shows on the Ledger as if it were
still live. This sweep is the reconciliation backstop: it asks Kalshi which of
our OPEN bets' orders are actually canceled, and transitions exactly those.

Safety (money path):
  - Only OPEN bets are touched (mark_bet_terminal_by_order_id is idempotent and
    refuses to clobber a terminal bet).
  - Only ZERO-FILL bets are touched. A bet with any fill represents real
    exposure / cost basis — never reinterpret that as a cancel, even if the
    order id later shows canceled (a partially-filled order can be canceled for
    its remainder while the filled part stands).
  - The order must be canceled per Kalshi's own /portfolio/orders, not inferred.

Runs on an interval and once at startup; also exposed via trigger() for the
post-cancel/-amend paths to call opportunistically.
"""

from __future__ import annotations

import asyncio
import time

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.db import get_session_factory
from src.core.logging import get_logger
from src.core.types import BetStatus
from src.kalshi.rest import KalshiRestClient
from src.models import Bet, BetFill
from src.services.bet_service import mark_bet_terminal_by_order_id

log = get_logger(__name__)

POLL_INTERVAL_S = 60


async def _canceled_order_ids() -> set[str]:
    """Every canceled order id on the account, paginated. Account-wide is fine:
    we only ever match these against our own OPEN bets' order ids, so a canceled
    order on a market we don't track simply never matches anything."""
    ids: set[str] = set()
    async with KalshiRestClient() as client:
        cursor: str | None = None
        while True:
            raw = await client.get_orders(status="canceled", limit=200, cursor=cursor)
            for o in raw.get("orders", []) or []:
                oid = o.get("order_id")
                if oid:
                    ids.add(oid)
            cursor = raw.get("cursor") or None
            if not cursor:
                break
    return ids


async def candidate_order_ids(session: AsyncSession) -> set[str]:
    """Order ids of OPEN bets with an order id and ZERO fills — the only bets a
    cancel reconciliation may touch. The LEFT JOIN + COUNT == 0 is the zero-fill
    guard; a bet with any BetFill row (real exposure) is excluded."""
    rows = (
        await session.execute(
            select(Bet.kalshi_order_id)
            .outerjoin(BetFill, BetFill.bet_id == Bet.id)
            .where(Bet.status == BetStatus.OPEN)
            .where(Bet.kalshi_order_id.is_not(None))
            .group_by(Bet.id)
            .having(func.count(BetFill.id) == 0)
        )
    ).scalars().all()
    return {oid for oid in rows if oid}


async def cancel_matching_bets(
    session: AsyncSession, order_ids: set[str]
) -> int:
    """Transition each given order's OPEN bet to CANCELLED. Caller has already
    confirmed these orders are canceled on Kalshi AND are zero-fill OPEN bets.
    Returns the count actually transitioned this call (an already-terminal bet
    counts as 0, so a re-run is a clean no-op). Does not commit."""
    transitioned = 0
    for order_id in order_ids:
        bet = await session.scalar(
            select(Bet).where(Bet.kalshi_order_id == order_id)
        )
        if bet is None or bet.status != BetStatus.OPEN:
            continue
        await mark_bet_terminal_by_order_id(
            session, order_id=order_id, status=BetStatus.CANCELLED,
        )
        transitioned += 1
    return transitioned


async def reconcile_canceled_orders_once() -> int:
    """One reconciliation pass. Returns the count of bets transitioned.

    Cheap by construction: one DB query for candidate OPEN zero-fill bets, one
    batched Kalshi read for the canceled set, then a set membership test — no
    per-bet round-trips. Skips the Kalshi read entirely when there are no
    candidates.
    """
    factory = get_session_factory()
    async with factory() as session:
        candidates = await candidate_order_ids(session)
    if not candidates:
        return 0

    canceled = await _canceled_order_ids()
    to_cancel = candidates & canceled
    if not to_cancel:
        return 0

    async with factory() as session:
        transitioned = await cancel_matching_bets(session, to_cancel)
        await session.commit()

    if transitioned:
        log.info("order_reconcile_cancelled", count=transitioned)
    return transitioned


class OrderReconciler:
    """Long-running poller. Lives on the supervisor. Mirrors PositionSyncer.

    Holds the supervisor's ledger write lock across each pass so its bet
    transitions serialize against the WS fill/settlement/cancel handlers (which
    also re-derive bet state under that lock); without it a fill landing
    concurrently could be lost."""

    def __init__(self, write_lock: asyncio.Lock) -> None:
        self._stopped = False
        self._last_run_at: float | None = None
        self._write_lock = write_lock

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
            async with self._write_lock:
                await reconcile_canceled_orders_once()
            self._last_run_at = time.monotonic()
        except Exception:  # noqa: BLE001 — a transient Kalshi/DB error retries next tick
            log.exception("order_reconcile_failed")

    async def trigger(self) -> None:
        """Run an extra pass now (e.g. after a cancel/amend)."""
        await self._tick()

    async def stop(self) -> None:
        self._stopped = True
