"""Tests for the canceled-order reconciliation sweep.

The sweep transitions an OPEN bet to CANCELLED only when its Kalshi order is
canceled AND the bet has zero fills. These tests pin the two safety guarantees:
  - a zero-fill OPEN bet whose order is canceled gets cancelled;
  - a bet with ANY fill is never a candidate (real exposure, never reinterpreted
    as a cancel).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from src.core.db import Base
from src.core.types import (
    BetSide,
    BetSource,
    BetStatus,
    Confidence,
    Sport,
    Strategy,
    Timing,
)
from src.models import Bet, BetFill, Market
from src.services.order_reconciler import (
    cancel_matching_bets,
    candidate_order_ids,
)


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _market(session: AsyncSession, ticker: str) -> int:
    m = Market(
        sport=Sport.SOCCER, game_id=None, kalshi_ticker=ticker,
        market_type="match_result", title=ticker,
        yes_price_cents=None, no_price_cents=None, volume=None,
        close_time=None, status="open",
    )
    session.add(m)
    await session.flush()
    return m.id


async def _bet(
    session: AsyncSession, *, market_id: int, order_id: str,
    status: BetStatus = BetStatus.OPEN,
) -> Bet:
    b = Bet(
        sport=Sport.SOCCER, market_id=market_id, suggestion_id=None,
        parent_bet_id=None, kalshi_order_id=order_id, client_order_id=order_id,
        side=BetSide.YES, entry_price_cents=40, exit_price_cents=None,
        quantity=10, remaining_quantity=10, remaining_quantity_centi=1000,
        stake_cents=400, pnl_cents=None, realized_pnl_cents=None,
        entry_fees_cents=0, exit_fees_cents=0, status=status, exit_type=None,
        source=BetSource.HUMAN, strategy=Strategy.MEAN_REVERSION,
        confidence=Confidence.MEDIUM, kelly_fraction_bps=None,
        ai_probability_pct=None, human_override_sizing=False,
        human_override_direction=False, human_reasoning=None, ai_reasoning=None,
        timing=Timing.LIVE, game_period=None, game_clock=None, tags=None,
        event_series=None, home_code=None, away_code=None, home_name=None,
        away_name=None, selection_code=None, verified=True, version=1,
        placed_at=datetime.now(timezone.utc), settled_at=None,
    )
    session.add(b)
    await session.flush()
    return b


async def _fill(session: AsyncSession, *, bet_id: int, order_id: str) -> None:
    session.add(BetFill(
        bet_id=bet_id, trade_id=f"t-{order_id}", order_id=order_id,
        ticker="X", side="yes", action="buy", price_cents=40,
        quantity_centi=1000, fee_cents=None, is_taker=True,
        created_time=datetime.now(timezone.utc),
    ))
    await session.flush()


@pytest.mark.asyncio
async def test_zero_fill_open_bet_is_a_candidate(session: AsyncSession) -> None:
    mid = await _market(session, "KXT-A")
    await _bet(session, market_id=mid, order_id="ord-open-nofill")
    candidates = await candidate_order_ids(session)
    assert candidates == {"ord-open-nofill"}


@pytest.mark.asyncio
async def test_filled_bet_is_not_a_candidate(session: AsyncSession) -> None:
    mid = await _market(session, "KXT-B")
    b = await _bet(session, market_id=mid, order_id="ord-filled")
    await _fill(session, bet_id=b.id, order_id="ord-filled")
    candidates = await candidate_order_ids(session)
    assert candidates == set()


@pytest.mark.asyncio
async def test_terminal_bet_is_not_a_candidate(session: AsyncSession) -> None:
    mid = await _market(session, "KXT-C")
    await _bet(
        session, market_id=mid, order_id="ord-won", status=BetStatus.WON,
    )
    candidates = await candidate_order_ids(session)
    assert candidates == set()


@pytest.mark.asyncio
async def test_cancel_matching_transitions_only_named(session: AsyncSession) -> None:
    mid = await _market(session, "KXT-D")
    a = await _bet(session, market_id=mid, order_id="ord-cancel-me")
    mid2 = await _market(session, "KXT-E")
    b = await _bet(session, market_id=mid2, order_id="ord-leave-me")

    n = await cancel_matching_bets(session, {"ord-cancel-me"})
    await session.commit()

    assert n == 1
    await session.refresh(a)
    await session.refresh(b)
    assert a.status == BetStatus.CANCELLED
    assert b.status == BetStatus.OPEN


@pytest.mark.asyncio
async def test_cancel_matching_is_idempotent(session: AsyncSession) -> None:
    mid = await _market(session, "KXT-F")
    a = await _bet(session, market_id=mid, order_id="ord-x")
    assert await cancel_matching_bets(session, {"ord-x"}) == 1
    await session.commit()
    # Second pass: already CANCELLED, so it's no longer a candidate and the
    # transition is a no-op (count 0), never a re-clobber.
    assert await candidate_order_ids(session) == set()
    assert await cancel_matching_bets(session, {"ord-x"}) == 0
