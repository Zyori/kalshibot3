"""Tests for record_external_position — importing a single-market position
placed directly on kalshi.com into the ledger.

The single counterpart to record_external_combo. Folds a position's buys AND
its closing sells (priced in the held-side frame — a YES position closed via
Kalshi's `sell no @ 7¢` is the `sell yes @ 93¢` it actually was) into one bet,
then derives P&L through recompute_bet_from_fills exactly as for an app-placed
bet. Idempotent + self-healing on (ticker, side).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

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
from src.core.types import (
    BetSide,
    BetSource,
    BetStatus,
    ExitType,
    Sport,
)
from src.models import Bet, BetFill, Market
from src.services.bet_service import (
    ExternalFillInput,
    record_external_position,
    settle_bets_for_market,
)

TICKER = "KXINTLFRIENDLYGAME-26JUN05CANIRL-CAN"

_T0 = datetime(2026, 6, 5, 20, 0, tzinfo=timezone.utc)


def _fill(
    action: str, held_price: int, qty: int, *, n: int = 0, fee: int = 0
) -> ExternalFillInput:
    """One fill, already priced in the held side's frame. `n` orders the fills
    in time so the buy-min is deterministic."""
    return ExternalFillInput(
        trade_id=f"t-{action}-{held_price}-{qty}-{n}",
        order_id=f"ord-{n}",
        action=action,
        held_price_cents=held_price,
        quantity_centi=qty * 100,
        fee_cents=fee,
        created_time=_T0 + timedelta(minutes=n),
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


async def _record(session: AsyncSession, fills, **kw) -> Bet:
    return await record_external_position(
        session, ticker=TICKER, side=BetSide.YES, fills=fills, **kw
    )


@pytest.mark.asyncio
async def test_records_open_position_buys_only(session: AsyncSession):
    bet = await _record(session, [_fill("buy", 40, 10, n=0)])
    await session.flush()

    assert bet.sport == Sport.SOCCER
    assert bet.source == BetSource.EXTERNAL
    assert bet.verified is False
    assert bet.status == BetStatus.OPEN
    assert bet.side == BetSide.YES
    assert bet.entry_price_cents == 40
    assert bet.quantity == 10
    assert bet.remaining_quantity == 10
    assert bet.stake_cents == 40 * 10
    # Idempotency key is the synthetic per-(ticker,side) client_order_id.
    assert bet.client_order_id == f"external-single:{TICKER}:yes"
    assert bet.kalshi_order_id is None
    assert bet.home_code == "CAN"
    assert bet.selection_code == "CAN"

    market = await session.get(Market, bet.market_id)
    assert market.kalshi_ticker == TICKER


@pytest.mark.asyncio
async def test_idempotent_on_ticker_side(session: AsyncSession):
    first = await _record(session, [_fill("buy", 40, 10, n=0)])
    await session.flush()
    second = await _record(session, [_fill("buy", 40, 10, n=0)])
    assert first.id == second.id
    assert await session.scalar(select(func.count(Bet.id))) == 1


@pytest.mark.asyncio
async def test_rejects_combo_ticker(session: AsyncSession):
    with pytest.raises(ValueError, match="record_external_combo"):
        await record_external_position(
            session,
            ticker="KXMVESPORTSMULTIGAMEEXTENDED-S202662EADA40D40-43319250880",
            side=BetSide.YES,
            fills=[_fill("buy", 17, 5, n=0)],
        )


@pytest.mark.asyncio
async def test_full_close_reconstructs_pnl(session: AsyncSession):
    """The real bug: a YES position bought @ 65¢ and closed via a sell that
    Kalshi reports on the opposite side, priced in the held frame at 93¢. P&L
    must be (93-65)*45 = +1260¢, and the bet lands closed (CLOSED_EARLY)."""
    bet = await _record(session, [
        _fill("buy", 65, 45, n=0, fee=72),
        _fill("sell", 93, 45, n=1),
    ])
    await session.flush()

    assert bet.entry_price_cents == 65
    assert bet.exit_price_cents == 93
    assert bet.remaining_quantity == 0
    assert bet.status == BetStatus.WON
    assert bet.exit_type == ExitType.CLOSED_EARLY
    assert bet.realized_pnl_cents == (93 - 65) * 45  # +1260¢
    assert bet.pnl_cents == 1260


@pytest.mark.asyncio
async def test_partial_close_then_settle(session: AsyncSession):
    """Bought 10 @ 30¢, sold 4 @ 50¢, held 6 to a YES settlement. Realized P&L
    accumulates the close (+80¢) plus the settlement on the held 6
    ((100-30)*6 = +420¢) = +500¢."""
    bet = await _record(session, [
        _fill("buy", 30, 10, n=0),
        _fill("sell", 50, 4, n=1),
    ])
    await session.flush()
    assert bet.status == BetStatus.OPEN
    assert bet.remaining_quantity == 6
    assert bet.realized_pnl_cents == (50 - 30) * 4  # +80¢ banked on the close

    await settle_bets_for_market(session, ticker=TICKER, settlement_value_cents=100)
    await session.refresh(bet)
    assert bet.status == BetStatus.WON
    assert bet.remaining_quantity == 0
    # 80 (close) + (100-30)*6 (settle held) = 80 + 420 = 500.
    assert bet.realized_pnl_cents == 500


@pytest.mark.asyncio
async def test_multiple_buys_blended_entry(session: AsyncSession):
    """Two buy orders on the same (ticker, side) fold into one bet with a
    centi-weighted blended entry — the per-order model can't represent this."""
    bet = await _record(session, [
        _fill("buy", 26, 20, n=0),
        _fill("buy", 47, 22, n=1),
    ])
    await session.flush()
    assert bet.quantity == 42
    # VWAP = (26*20 + 47*22) / 42 = (520 + 1034)/42 = 37.0 → 37.
    assert bet.entry_price_cents == 37


@pytest.mark.asyncio
async def test_reimport_heals_orphan_sells(session: AsyncSession):
    """A position imported buys-only (the old behavior left orphan sells) is
    healed by re-importing with the sells: same bet, now closed with P&L. Also
    proves we OVERWRITE a fills_sync orphan sell stored at the raw opposite-side
    price rather than colliding on its unique trade_id."""
    # First import: buys only.
    bet1 = await _record(session, [_fill("buy", 65, 45, n=0)])
    await session.flush()
    assert bet1.status == BetStatus.OPEN

    # fills_sync had recorded the sell at the RAW opposite side (sell no @ 7¢),
    # orphaned. Its trade_id is what the re-import will carry for the sell.
    sell_tid = "t-sell-93-45-1"
    session.add(BetFill(
        bet_id=None, trade_id=sell_tid, order_id="ord-1", ticker=TICKER,
        side="no", action="sell", price_cents=7, quantity_centi=4500,
        fee_cents=0, is_taker=False, created_time=_T0 + timedelta(minutes=1),
    ))
    await session.flush()

    # Re-import with the full fill set (sell priced in held frame @ 93¢).
    bet2 = await _record(session, [
        _fill("buy", 65, 45, n=0),
        _fill("sell", 93, 45, n=1),
    ])
    await session.flush()

    assert bet2.id == bet1.id  # same bet, healed
    assert await session.scalar(select(func.count(Bet.id))) == 1
    # The orphan sell row was bound + repriced to the held frame, not duplicated.
    sell_rows = (await session.execute(
        select(BetFill).where(BetFill.trade_id == sell_tid)
    )).scalars().all()
    assert len(sell_rows) == 1
    assert sell_rows[0].bet_id == bet2.id
    assert sell_rows[0].price_cents == 93
    assert sell_rows[0].side == "yes"
    assert bet2.status == BetStatus.WON
    assert bet2.realized_pnl_cents == (93 - 65) * 45
