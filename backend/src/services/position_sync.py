"""Position reconciliation against Kalshi.

The user's Kalshi account holds positions across many domains — soccer
(this app), politics, weather, etc. This app must NEVER touch the
non-soccer positions: not read them into our DB, not cancel their orders,
not factor them into any displayed P&L. See memory:
feedback_cross_market_isolation.

The sync loop:
  1. Fetch every page of /portfolio/positions from Kalshi.
  2. Filter to soccer tickers (is_soccer_ticker) BEFORE any further work.
  3. UPSERT each soccer position into our POSITION table.
  4. Mark any soccer positions we have that Kalshi doesn't as zeroed.
       (User closed it on kalshi.com — bet_service still has the BET row,
        but the position is gone.)
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from collections.abc import Awaitable, Callable

from src.core.db import get_session_factory
from src.core.logging import get_logger
from src.core.types import BetSide, BetStatus, MarketStatus, Sport
from src.kalshi.rest import KalshiRestClient
from src.kalshi.schemas import PortfolioPosition
from src.models import Bet, Market, Position
from src.sports.soccer import is_soccer_ticker

log = get_logger(__name__)

POLL_INTERVAL_S = 60


async def _get_or_create_market_id(session: AsyncSession, *, ticker: str) -> int:
    """Same helper as bet_service — kept duplicated for clarity; both need it
    and importing across services would pull in extra concerns."""
    existing = await session.scalar(select(Market).where(Market.kalshi_ticker == ticker))
    if existing is not None:
        return existing.id

    m = Market(
        sport=Sport.SOCCER,
        game_id=None,
        kalshi_ticker=ticker,
        market_type="match_result",
        title=ticker,
        yes_price_cents=None,
        no_price_cents=None,
        volume=None,
        close_time=None,
        status=MarketStatus.OPEN,
    )
    session.add(m)
    await session.flush()
    return m.id


def _signed_position_to_side_and_qty(signed: int) -> tuple[BetSide, int]:
    """Kalshi's `position` is signed: positive = YES exposure, negative = NO."""
    if signed >= 0:
        return BetSide.YES, signed
    return BetSide.NO, abs(signed)


async def _upsert_position(
    session: AsyncSession,
    *,
    p: PortfolioPosition,
    avg_entry_price_cents: int,
) -> None:
    """UPSERT one POSITION row. avg_entry_price_cents comes from market
    exposure / quantity rounded to cents.

    Side-flip cleanup: a market can't hold both YES and NO exposure
    simultaneously (Kalshi nets them — buying YES against a NO position
    just closes contracts). If we previously cached the opposite side
    and the current position is on this side, the cached row is stale
    and must be deleted. Skipping this leaves zombie positions in the
    UI (the bug that surfaced 2026-05-26 when the user closed a NO
    position and opened a YES one on the same ticker).
    """
    side, qty = _signed_position_to_side_and_qty(p.position)
    market_id = await _get_or_create_market_id(session, ticker=p.ticker)
    opposite_side = BetSide.NO if side is BetSide.YES else BetSide.YES

    # Drop any cached row on the opposite side — the current Kalshi snapshot
    # says it's not held.
    opposite = await session.scalar(
        select(Position).where(
            Position.market_id == market_id,
            Position.side == opposite_side,
        )
    )
    if opposite is not None:
        await session.delete(opposite)

    existing = await session.scalar(
        select(Position).where(
            Position.market_id == market_id,
            Position.side == side,
        )
    )

    if qty == 0:
        # Zero quantity = closed position. Delete the row so the UI doesn't
        # show stale exposure.
        if existing is not None:
            await session.delete(existing)
        return

    if existing is None:
        new_row = Position(
            sport=Sport.SOCCER,
            kalshi_ticker=p.ticker,
            market_id=market_id,
            side=side,
            quantity=qty,
            avg_entry_price_cents=avg_entry_price_cents,
            current_price_cents=None,
            unrealized_pnl_cents=None,
            last_synced=datetime.now(timezone.utc),
        )
        session.add(new_row)
    else:
        existing.quantity = qty
        existing.avg_entry_price_cents = avg_entry_price_cents
        existing.last_synced = datetime.now(timezone.utc)


def _estimate_avg_entry(p: PortfolioPosition) -> int:
    """market_exposure / |position|, clamped to 1-99¢. Falls back to 50 when
    the math doesn't make sense (closed-out positions etc.)."""
    if p.position == 0:
        return 50
    raw = abs(p.market_exposure) // abs(p.position)
    return max(1, min(99, raw))


async def sync_positions_once() -> dict[str, object]:
    """One full reconciliation pass. Returns counts and the set of tickers
    that transitioned from held to closed this pass — used to trigger
    settlement sweeps on the markets where Kalshi just paid us out.
    """
    soccer_positions: list[PortfolioPosition] = []
    other_count = 0

    async with KalshiRestClient() as client:
        cursor: str | None = None
        while True:
            resp = await client.get_positions(cursor=cursor)
            for p in resp.market_positions:
                # Cross-market isolation: filter at the top, before any work.
                if is_soccer_ticker(p.ticker):
                    soccer_positions.append(p)
                else:
                    other_count += 1
            cursor = resp.cursor
            if not cursor:
                break

    factory = get_session_factory()
    closed_with_open_bet: set[str] = set()
    async with factory() as session:
        # Snapshot which tickers we currently have in our DB so we can null
        # out the ones Kalshi no longer reports.
        existing_tickers = {
            row[0] for row in (await session.execute(
                select(Position.kalshi_ticker)
            )).all()
        }

        seen_tickers: set[str] = set()
        for p in soccer_positions:
            await _upsert_position(
                session,
                p=p,
                avg_entry_price_cents=_estimate_avg_entry(p),
            )
            seen_tickers.add(p.ticker)

        # Any soccer position we had that Kalshi didn't return — closed.
        for ticker in existing_tickers - seen_tickers:
            for row in (await session.execute(
                select(Position).where(Position.kalshi_ticker == ticker)
            )).scalars():
                await session.delete(row)
            # If there's still an OPEN bet on this market, the position
            # vanishing is likely a settlement payout we missed via WS.
            still_open = await session.scalar(
                select(Bet.id)
                .join(Market, Market.id == Bet.market_id)
                .where(Market.kalshi_ticker == ticker)
                .where(Bet.status == BetStatus.OPEN)
                .limit(1)
            )
            if still_open is not None:
                closed_with_open_bet.add(ticker)

        await session.commit()

    log.info(
        "position_sync_complete",
        soccer=len(soccer_positions),
        non_soccer_skipped=other_count,
        closed_with_open_bet=len(closed_with_open_bet),
    )
    return {
        "soccer": len(soccer_positions),
        "non_soccer_skipped": other_count,
        "closed_with_open_bet": closed_with_open_bet,
    }


class PositionSyncer:
    """Long-running poller. Lives on the supervisor."""

    def __init__(self) -> None:
        self._stopped = False
        self._last_run_at: float | None = None
        self._on_position_closed: Callable[[], Awaitable[None]] | None = None

    def set_on_position_closed(
        self, cb: Callable[[], Awaitable[None]]
    ) -> None:
        """Called after a sync that detected a Kalshi position dropping to
        zero while we still have an OPEN bet on that market — the supervisor
        wires this to settlement_sweeper.trigger()."""
        self._on_position_closed = cb

    @property
    def last_run_age_s(self) -> float | None:
        if self._last_run_at is None:
            return None
        return time.monotonic() - self._last_run_at

    async def run(self) -> None:
        # First sync ASAP at startup; then on the interval.
        await self._tick()
        while not self._stopped:
            await asyncio.sleep(POLL_INTERVAL_S)
            await self._tick()

    async def _tick(self) -> None:
        try:
            result = await sync_positions_once()
            self._last_run_at = time.monotonic()
        except Exception:  # noqa: BLE001
            log.exception("position_sync_failed")
            return
        closed = result.get("closed_with_open_bet") or set()
        if closed and self._on_position_closed is not None:
            try:
                await self._on_position_closed()
            except Exception:  # noqa: BLE001
                log.exception("on_position_closed_callback_failed")

    async def trigger(self) -> None:
        """Fire an extra sync now — called after order placement."""
        await self._tick()

    async def stop(self) -> None:
        self._stopped = True
