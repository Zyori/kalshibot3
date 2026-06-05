"""Tests for combo (multivariate / parlay) recording + settlement.

record_external_combo logs a combo placed on kalshi.com as an EXTERNAL bet with
its legs; the combo then settles binary through the same settle_bets_for_market
path as any market.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from src.core.db import Base
from src.core.types import BetSide, BetSource, BetStatus, Sport, Strategy
from src.models import Bet, ComboLeg, Market
from src.services.bet_service import (
    ComboLegInput,
    record_external_combo,
    settle_bets_for_market,
)

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


def _legs() -> list[ComboLegInput]:
    return [
        ComboLegInput(leg_ticker="KXINTLFRIENDLYGAME-26JUN05CANIRL-CAN",
                      leg_event_ticker="KXINTLFRIENDLYGAME-26JUN05CANIRL",
                      leg_title="Canada", side="yes"),
        ComboLegInput(leg_ticker="KXINTLFRIENDLYGAME-26JUN05GEOBHR-GEO",
                      leg_event_ticker="KXINTLFRIENDLYGAME-26JUN05GEOBHR",
                      leg_title="Georgia", side="yes"),
    ]


async def _record(session: AsyncSession, **kw) -> Bet:
    return await record_external_combo(
        session,
        ticker=COMBO_TICKER,
        side=BetSide.YES,
        entry_price_cents=17,
        quantity=95,
        legs=_legs(),
        placed_at=datetime.now(timezone.utc),
        **kw,
    )


@pytest.mark.asyncio
async def test_records_open_external_combo_with_legs(session: AsyncSession):
    bet = await _record(session)
    await session.flush()

    assert bet.sport == Sport.COMBO
    assert bet.source == BetSource.EXTERNAL
    assert bet.verified is False
    assert bet.status == BetStatus.OPEN
    assert bet.strategy == Strategy.LOCK_PARLAY  # default
    assert bet.side == BetSide.YES
    assert bet.entry_price_cents == 17
    assert bet.quantity == 95
    assert bet.stake_cents == 17 * 95  # exact: price * qty
    assert bet.kalshi_order_id is None and bet.client_order_id is None

    legs = (await session.execute(
        select(ComboLeg).where(ComboLeg.bet_id == bet.id).order_by(ComboLeg.leg_index)
    )).scalars().all()
    assert [leg.leg_title for leg in legs] == ["Canada", "Georgia"]
    assert [leg.leg_index for leg in legs] == [0, 1]

    market = await session.get(Market, bet.market_id)
    assert market.sport == Sport.COMBO
    assert market.market_type == "combo"


@pytest.mark.asyncio
async def test_idempotent_on_ticker(session: AsyncSession):
    first = await _record(session)
    await session.flush()
    second = await _record(session)
    assert first.id == second.id
    # Only one bet, and legs weren't duplicated.
    n_bets = await session.scalar(select(func.count(Bet.id)))
    n_legs = await session.scalar(select(func.count(ComboLeg.id)))
    assert n_bets == 1
    assert n_legs == 2


@pytest.mark.asyncio
async def test_order_id_stamped_for_fee_backlink(session: AsyncSession):
    bet = await _record(session, order_id="ord-abc-123")
    await session.flush()
    # kalshi_order_id is set so fills_sync can back-link the external fill's fee.
    assert bet.kalshi_order_id == "ord-abc-123"


@pytest.mark.asyncio
async def test_idempotent_on_order_id(session: AsyncSession):
    first = await _record(session, order_id="ord-xyz")
    await session.flush()
    second = await _record(session, order_id="ord-xyz")
    assert first.id == second.id
    assert await session.scalar(select(func.count(Bet.id))) == 1


@pytest.mark.asyncio
async def test_rejects_non_combo_ticker(session: AsyncSession):
    with pytest.raises(ValueError, match="not a combo ticker"):
        await record_external_combo(
            session, ticker="KXWCGAME-26JUN11MEXRSA-MEX", side=BetSide.YES,
            entry_price_cents=50, quantity=1, legs=[], placed_at=datetime.now(timezone.utc),
        )


@pytest.mark.asyncio
async def test_combo_settles_binary_win(session: AsyncSession):
    bet = await _record(session)
    await session.flush()
    # YES combo bought at 17¢, settles YES (100) → pnl = (100-17)*95 = 7885¢.
    n = await settle_bets_for_market(
        session, ticker=COMBO_TICKER, settlement_value_cents=100
    )
    await session.flush()
    assert n == 1
    await session.refresh(bet)
    assert bet.status == BetStatus.WON
    assert bet.pnl_cents == (100 - 17) * 95


@pytest.mark.asyncio
async def test_combo_settles_binary_loss(session: AsyncSession):
    bet = await _record(session)
    await session.flush()
    # Settles NO (0) → pnl = (0-17)*95 = -1615¢.
    await settle_bets_for_market(
        session, ticker=COMBO_TICKER, settlement_value_cents=0
    )
    await session.flush()
    await session.refresh(bet)
    assert bet.status == BetStatus.LOST
    assert bet.pnl_cents == (0 - 17) * 95
