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
from src.kalshi.schemas import Order
from src.models import Bet, BetFill, ComboLeg, Market
from src.services.bet_service import (
    ComboLegInput,
    record_external_combo,
    record_placed_order,
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
    # No real Kalshi order id (logged, not placed). client_order_id is the
    # synthetic per-ticker idempotency key that DB-enforces one log per combo.
    assert bet.kalshi_order_id is None
    assert bet.client_order_id == f"external-combo:{COMBO_TICKER}"

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
async def test_relog_same_ticker_no_order_id_returns_same_bet(session: AsyncSession):
    """Logging the same combo ticker twice with no order_id is deduped by the
    synthetic per-ticker client_order_id (one EXTERNAL combo per ticker)."""
    first = await _record(session)
    await session.flush()
    second = await _record(session)  # no order_id → synthetic key path
    assert first.id == second.id
    assert await session.scalar(select(func.count(Bet.id))) == 1
    # The synthetic key is what enforces it.
    assert first.client_order_id == f"external-combo:{COMBO_TICKER}"


def test_sports_leg_recognition():
    """Per-leg isolation guard: sports legs pass, out-of-scope legs don't."""
    from src.sports.combo import is_sports_leg_ticker
    from src.sports.soccer import is_soccer_ticker

    # A soccer leg passes via is_soccer_ticker; other sports via is_sports_leg.
    assert is_sports_leg_ticker("KXNHLGAME-26JUN04VGKCAR-CAR")
    assert is_sports_leg_ticker("KXNBAGAME-26JUN05NYKSAS-NYK")
    assert is_sports_leg_ticker("KXMLBGAME-26JUN05-X")
    # Out of scope: politics / weather / crypto must be refused.
    assert not is_sports_leg_ticker("KXVPRESNOMR-28-EKIR")
    assert not is_sports_leg_ticker("KXHIGHCHI-25JAN16")
    # Soccer is handled by is_soccer_ticker, not the sports-leg list.
    assert not is_sports_leg_ticker("KXINTLFRIENDLYGAME-26JUN08FRANIR-FRA")
    assert is_soccer_ticker("KXINTLFRIENDLYGAME-26JUN08FRANIR-FRA")


@pytest.mark.asyncio
async def test_order_id_stamped_for_fee_backlink(session: AsyncSession):
    bet = await _record(session, order_id="ord-abc-123")
    await session.flush()
    # kalshi_order_id is set so fills_sync can back-link the external fill's fee.
    assert bet.kalshi_order_id == "ord-abc-123"


@pytest.mark.asyncio
async def test_backlinks_existing_orphan_fill_fee(session: AsyncSession):
    """An external bet_fill already recorded by fills_sync (bet_id=NULL, real
    fee) gets bound to the combo at record time, so the fee shows immediately —
    not dependent on a future sweep that would skip the historical fill."""
    session.add(BetFill(
        bet_id=None, trade_id="t-1", order_id="ord-fee",
        ticker=COMBO_TICKER, side="yes", action="buy",
        price_cents=17, quantity_centi=9500, fee_cents=93,
        is_taker=True, created_time=datetime.now(timezone.utc),
    ))
    await session.flush()

    bet = await _record(session, order_id="ord-fee")
    await session.flush()
    await session.refresh(bet)

    # The orphan fill is now bound and its fee is on the bet.
    fill = (await session.execute(
        select(BetFill).where(BetFill.trade_id == "t-1")
    )).scalar_one()
    assert fill.bet_id == bet.id
    assert bet.entry_fees_cents == 93


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
async def test_placed_combo_records_human_bet_with_legs(session: AsyncSession):
    """A combo placed THROUGH the builder records via record_placed_order with
    combo_legs — source=HUMAN, verified=True, sport inferred=COMBO, legs written."""
    order = Order(
        order_id="ord-combo", client_order_id="cli-combo", ticker=COMBO_TICKER,
        side="yes", action="buy", type="limit", status="resting",
        yes_price=20, no_price=80, count=10, remaining_count=10,
    )
    bet = await record_placed_order(
        session,
        order=order,
        client_order_id="cli-combo",
        requested_count=10,
        requested_price_cents=20,
        action="buy",
        source=BetSource.HUMAN,
        strategy=Strategy.LOCK_PARLAY,
        combo_legs=_legs(),
    )
    await session.flush()
    assert bet is not None
    assert bet.sport == Sport.COMBO          # inferred from the combo ticker
    assert bet.source == BetSource.HUMAN
    assert bet.verified is True
    assert bet.entry_price_cents == 20
    legs = (await session.execute(
        select(ComboLeg).where(ComboLeg.bet_id == bet.id).order_by(ComboLeg.leg_index)
    )).scalars().all()
    assert [leg.leg_title for leg in legs] == ["Canada", "Georgia"]


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


@pytest.mark.asyncio
async def test_combo_settles_from_a_real_settlement_row(session: AsyncSession):
    """Bridge the discovery→settle path the sweeper actually runs: feed the
    settlement_value_cents derived from a Kalshi Settlement WIRE ROW (not a
    hardcoded int) into settle_bets_for_market. Combos settle binary yes/no —
    verified across full account history that no KXMVE combo ever returns
    'scalar' — so this is the real production path, not a bypass."""
    from src.kalshi.schemas import Settlement

    bet = await _record(session)
    await session.flush()

    # A real settled-combo row as Kalshi returns it (market_result='yes').
    row = Settlement(ticker=COMBO_TICKER, market_result="yes", revenue=9500)
    assert row.settlement_value_cents == 100  # the value the sweeper feeds in

    n = await settle_bets_for_market(
        session, ticker=COMBO_TICKER,
        settlement_value_cents=row.settlement_value_cents,
    )
    await session.flush()
    assert n == 1
    await session.refresh(bet)
    assert bet.status == BetStatus.WON
    assert bet.pnl_cents == (100 - 17) * 95
