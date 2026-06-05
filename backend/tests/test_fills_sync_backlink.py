"""Regression: fills_sync back-linking an already-fee'd orphan fill must force
the bet's fee aggregate to recompute.

Bug: when _ingest_rest_fill bound an orphan bet_fill (bet_id NULL) to a bet but
the fill's fee was already correct, the bet_id was only added to `affected` if
the fee VALUE changed. A back-linked-but-unchanged fee left the bet's
entry_fees_cents stale at 0 (hit while logging combos: the fill carried its fee
from a prior sweep, so binding it never triggered a rollup).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from src.core.db import Base
from src.core.types import BetSide, BetSource, BetStatus, Sport, Strategy, Timing, Confidence
from src.kalshi.schemas import Fill as RestFill
from src.models import Bet, BetFill, Market
from src.services.fills_sync import _ingest_rest_fill

COMBO_TICKER = "KXMVESPORTSMULTIGAMEEXTENDED-S202662EADA40D40-43319250880"


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


@pytest.mark.asyncio
async def test_backlink_with_unchanged_fee_still_marks_bet_affected(session: AsyncSession):
    market = Market(
        sport=Sport.COMBO, game_id=None, kalshi_ticker=COMBO_TICKER,
        market_type="combo", title=COMBO_TICKER, yes_price_cents=None,
        no_price_cents=None, volume=None, close_time=None,
        status="open", settlement=None, settlement_detected_at=None,
    )
    session.add(market)
    await session.flush()

    bet = Bet(
        sport=Sport.COMBO, market_id=market.id, kalshi_order_id="ord-1",
        side=BetSide.YES, entry_price_cents=17, quantity=95,
        remaining_quantity=95, remaining_quantity_centi=9500, stake_cents=1615,
        entry_fees_cents=0, exit_fees_cents=0, status=BetStatus.OPEN,
        source=BetSource.EXTERNAL, strategy=Strategy.LOCK_PARLAY,
        confidence=Confidence.MEDIUM, timing=Timing.PRE_MATCH, verified=False,
        version=1, placed_at=datetime.now(timezone.utc),
    )
    session.add(bet)
    await session.flush()

    # Orphan fill: bound to no bet, fee ALREADY set to 93 (as a prior sweep left it).
    session.add(BetFill(
        bet_id=None, trade_id="t-1", order_id="ord-1", ticker=COMBO_TICKER,
        side="yes", action="buy", price_cents=17, quantity_centi=9500,
        fee_cents=93, is_taker=True, created_time=datetime.now(timezone.utc),
    ))
    await session.flush()

    rest = RestFill(
        trade_id="t-1", order_id="ord-1", ticker=COMBO_TICKER, side="yes",
        action="buy", yes_price=17, no_price=83, count_centi=9500,
        fee_cents=93, is_taker=True, created_time=datetime.now(timezone.utc),
    )
    affected = await _ingest_rest_fill(session, rest_fill=rest)

    # The bet is flagged for recompute even though the fee value didn't change.
    assert bet.id in affected
    # And the fill is now bound to the bet.
    fill = await session.scalar(
        select(BetFill).where(BetFill.trade_id == "t-1")
    )
    assert fill is not None
    assert fill.bet_id == bet.id
