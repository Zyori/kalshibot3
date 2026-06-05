"""Settlement sweep — resolve OPEN bets whose markets have settled on Kalshi.

The WS `market_lifecycle` event is the fast path for settlements (see
supervisor._on_market_lifecycle). It carries settlement_value directly,
but only fires while we're subscribed — and we drop subscriptions when a
market moves to DONE tier, which happens before Kalshi flips the market
to `settled` for many soccer matches (the post-final-whistle settlement
window can run 3+ hours).

This sweeper is the steady-state correctness loop. Every OPEN bet pins
its market into a poll set; once Kalshi reports a settlement for that
ticker on /portfolio/settlements, we call the existing
settle_bets_for_market path. Zero new settlement logic — only a new
way to discover that settlement happened.

Idle cost when nothing is open: one indexed SELECT returning zero rows.

Cross-market isolation: settle_bets_for_market refuses non-soccer
tickers, so a settlements row for a politics market is a no-op. We
filter at the top anyway to keep the log clean.
"""

from __future__ import annotations

import asyncio
import time

from sqlalchemy import select

from src.core.db import get_session_factory
from src.core.logging import get_logger
from src.core.types import BetStatus
from src.kalshi.rest import KalshiRestClient
from src.models import Bet, Market
from src.services.bet_service import settle_bets_for_market
from src.sports.tradeable import is_tradeable_ticker

log = get_logger(__name__)

POLL_INTERVAL_S = 60


async def sweep_settlements_once() -> dict[str, int]:
    """One pass: find tickers with OPEN bets, ask Kalshi if each has settled,
    drive settlement through bet_service when yes.

    Per-ticker query (not a bulk /settlements pull) because the bet set is
    small (single-user app, $4 bankroll, ~handful of OPEN bets at a time)
    and per-ticker is unambiguous about which markets we care about.
    """
    factory = get_session_factory()
    async with factory() as session:
        open_tickers = (
            await session.execute(
                select(Market.kalshi_ticker)
                .join(Bet, Bet.market_id == Market.id)
                .where(Bet.status == BetStatus.OPEN)
                .distinct()
            )
        ).scalars().all()

    open_tickers = [t for t in open_tickers if is_tradeable_ticker(t)]
    if not open_tickers:
        return {"checked": 0, "settled": 0}

    settled_count = 0
    async with KalshiRestClient() as client:
        for ticker in open_tickers:
            try:
                resp = await client.get_settlements(ticker=ticker, limit=10)
            except Exception as e:  # noqa: BLE001
                log.warning(
                    "settlement_sweep_fetch_failed",
                    ticker=ticker, error=str(e)[:160],
                )
                continue

            for row in resp.settlements:
                value_cents = row.settlement_value_cents
                if value_cents is None:
                    # Scalar settlement Kalshi hasn't normalized — skip and
                    # let the next sweep retry. Soccer 3-way moneylines
                    # resolve to yes/no on the winning leg.
                    continue
                async with factory() as session:
                    try:
                        n = await settle_bets_for_market(
                            session,
                            ticker=row.ticker,
                            settlement_value_cents=value_cents,
                        )
                        if n > 0:
                            settled_count += n
                        await session.commit()
                    except Exception:  # noqa: BLE001
                        log.exception(
                            "settlement_sweep_settle_failed",
                            ticker=row.ticker,
                        )

    if settled_count:
        log.info(
            "settlement_sweep_complete",
            checked=len(open_tickers), settled=settled_count,
        )
    return {"checked": len(open_tickers), "settled": settled_count}


class SettlementSweeper:
    """Long-running poller. Lives on the supervisor.

    Triggered ad-hoc by position_syncer when a Kalshi position drops to
    zero while an OPEN bet still exists on that market — that's the
    "Kalshi paid us, our row is stale" signal.
    """

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
            await sweep_settlements_once()
            self._last_run_at = time.monotonic()
        except Exception:  # noqa: BLE001
            log.exception("settlement_sweep_failed")

    async def trigger(self) -> None:
        await self._tick()

    async def stop(self) -> None:
        self._stopped = True
