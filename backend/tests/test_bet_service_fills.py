"""Tests for the bet_fill + FIFO partial-close logic in bet_service.

These cover the core "1 bet = 1 buy decision, sells aggregate via FIFO"
contract. Fee enrichment runs separately via fills_sync; here we verify
the fill bookkeeping itself.
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
from src.core.types import (
    BetSide,
    BetSource,
    BetStatus,
    Confidence,
    MarketStatus,
    Sport,
    Strategy,
    Timing,
)
from src.kalshi.ws_wire import Fill, FillPayload
from src.models import Bet, BetFill, Market
from src.services.bet_service import (
    record_fill,
    record_placed_order,
    settle_bets_for_market,
)


SOCCER_TICKER = "KXWCGAME-26JUN11MEXRSA-MEX"


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


def _make_fill(
    *,
    trade_id: str,
    order_id: str,
    side: str,
    action: str,
    price_cents: int,
    qty: int,
    ticker: str = SOCCER_TICKER,
    is_taker: bool = False,
) -> Fill:
    yes_p = price_cents if side == "yes" else 100 - price_cents
    no_p = 100 - yes_p
    return Fill(
        type="fill",
        sid=1,
        msg=FillPayload(
            trade_id=trade_id,
            order_id=order_id,
            ticker=ticker,
            side=side,  # type: ignore[arg-type]
            action=action,  # type: ignore[arg-type]
            count_centi=qty * 100,
            yes_price_cents=yes_p,
            no_price_cents=no_p,
            is_taker=is_taker,
            ts=datetime.now(timezone.utc),
        ),
    )


async def _make_market(session: AsyncSession) -> Market:
    m = Market(
        sport=Sport.SOCCER,
        game_id=None,
        kalshi_ticker=SOCCER_TICKER,
        market_type="match_result",
        title=SOCCER_TICKER,
        yes_price_cents=None,
        no_price_cents=None,
        volume=None,
        close_time=None,
        status=MarketStatus.OPEN,
    )
    session.add(m)
    await session.flush()
    return m


async def _open_bet(
    session: AsyncSession,
    *,
    order_id: str,
    qty: int,
    price: int,
    side: BetSide = BetSide.YES,
) -> Bet:
    market = await session.scalar(
        select(Market).where(Market.kalshi_ticker == SOCCER_TICKER)
    )
    if market is None:
        market = await _make_market(session)
    bet = Bet(
        sport=Sport.SOCCER,
        market_id=market.id,
        kalshi_order_id=order_id,
        client_order_id=f"cli-{order_id}",
        side=side,
        entry_price_cents=price,
        quantity=qty,
        remaining_quantity=qty,
        stake_cents=qty * price,
        status=BetStatus.OPEN,
        source=BetSource.HUMAN,
        strategy=Strategy.MANUAL,
        confidence=Confidence.MEDIUM,
        timing=Timing.PRE_MATCH,
        verified=True,
        placed_at=datetime.now(timezone.utc),
    )
    session.add(bet)
    await session.flush()
    return bet


@pytest.mark.asyncio
async def test_buy_fill_creates_bet_fill_and_refines_entry(session: AsyncSession) -> None:
    bet = await _open_bet(session, order_id="ord-1", qty=100, price=30)

    # First fill at 28¢ for 40 contracts.
    await record_fill(session, _make_fill(
        trade_id="t1", order_id="ord-1", side="yes", action="buy",
        price_cents=28, qty=40,
    ))
    # Second fill at 31¢ for 60 contracts.
    await record_fill(session, _make_fill(
        trade_id="t2", order_id="ord-1", side="yes", action="buy",
        price_cents=31, qty=60,
    ))
    await session.flush()

    fills = (await session.execute(
        select(BetFill).where(BetFill.bet_id == bet.id)
    )).scalars().all()
    assert len(fills) == 2
    assert all(f.action == "buy" for f in fills)
    assert all(f.fee_cents is None for f in fills)

    # Weighted avg entry: (28*40 + 31*60) / 100 = 29.8 → 29 (floor).
    await session.refresh(bet)
    assert bet.entry_price_cents == 29


@pytest.mark.asyncio
async def test_sell_chunks_decrement_remaining_and_settle_when_zero(
    session: AsyncSession,
) -> None:
    bet = await _open_bet(session, order_id="ord-1", qty=100, price=30)

    # Buy filled at 30¢
    await record_fill(session, _make_fill(
        trade_id="b1", order_id="ord-1", side="yes", action="buy",
        price_cents=30, qty=100,
    ))

    # First sell: 40 @ 35¢
    await record_fill(session, _make_fill(
        trade_id="s1", order_id="sell-1", side="yes", action="sell",
        price_cents=35, qty=40,
    ))
    await session.refresh(bet)
    assert bet.remaining_quantity == 60
    assert bet.status == BetStatus.OPEN
    assert bet.realized_pnl_cents == (35 - 30) * 40  # 200

    # Second sell: 60 @ 38¢
    await record_fill(session, _make_fill(
        trade_id="s2", order_id="sell-2", side="yes", action="sell",
        price_cents=38, qty=60,
    ))
    await session.refresh(bet)
    assert bet.remaining_quantity == 0
    assert bet.status == BetStatus.WON
    # 40*5 + 60*8 = 200 + 480 = 680
    assert bet.realized_pnl_cents == 680
    assert bet.pnl_cents == 680
    # Weighted exit avg: (35*40 + 38*60) / 100 = 36.8 → 36 (floor)
    assert bet.exit_price_cents == 36


@pytest.mark.asyncio
async def test_sell_crosses_two_openers_fifo(session: AsyncSession) -> None:
    # Two opens on same (market, side); FIFO closes oldest first.
    bet_a = await _open_bet(session, order_id="ord-a", qty=50, price=20)
    bet_b = await _open_bet(session, order_id="ord-b", qty=50, price=25)

    await record_fill(session, _make_fill(
        trade_id="ba", order_id="ord-a", side="yes", action="buy",
        price_cents=20, qty=50,
    ))
    await record_fill(session, _make_fill(
        trade_id="bb", order_id="ord-b", side="yes", action="buy",
        price_cents=25, qty=50,
    ))

    # Sell 80 @ 30¢: should consume all 50 of bet_a, then 30 of bet_b.
    await record_fill(session, _make_fill(
        trade_id="s1", order_id="sell-1", side="yes", action="sell",
        price_cents=30, qty=80,
    ))

    await session.refresh(bet_a)
    await session.refresh(bet_b)
    assert bet_a.remaining_quantity == 0
    assert bet_a.status == BetStatus.WON
    assert bet_a.realized_pnl_cents == (30 - 20) * 50  # 500
    assert bet_b.remaining_quantity == 20
    assert bet_b.status == BetStatus.OPEN
    assert bet_b.realized_pnl_cents == (30 - 25) * 30  # 150


@pytest.mark.asyncio
async def test_record_fill_idempotent_on_trade_id(session: AsyncSession) -> None:
    await _open_bet(session, order_id="ord-1", qty=10, price=30)

    f = _make_fill(
        trade_id="dup", order_id="ord-1", side="yes", action="buy",
        price_cents=30, qty=10,
    )
    await record_fill(session, f)
    await record_fill(session, f)  # replay

    fills = (await session.execute(select(BetFill))).scalars().all()
    assert len(fills) == 1


@pytest.mark.asyncio
async def test_external_buy_fill_recorded_without_bet(session: AsyncSession) -> None:
    # No bet exists for this order_id — placed via kalshi.com.
    await _make_market(session)
    await record_fill(session, _make_fill(
        trade_id="ext", order_id="external-1", side="yes", action="buy",
        price_cents=30, qty=5,
    ))
    fill = await session.scalar(
        select(BetFill).where(BetFill.trade_id == "ext")
    )
    assert fill is not None
    assert fill.bet_id is None  # no auto-bet creation


@pytest.mark.asyncio
async def test_settlement_credits_remaining_only(session: AsyncSession) -> None:
    bet = await _open_bet(session, order_id="ord-1", qty=100, price=40)
    await record_fill(session, _make_fill(
        trade_id="b1", order_id="ord-1", side="yes", action="buy",
        price_cents=40, qty=100,
    ))
    # Sell 30 @ 60¢ → realized = 30 * 20 = 600, remaining 70.
    await record_fill(session, _make_fill(
        trade_id="s1", order_id="sell-1", side="yes", action="sell",
        price_cents=60, qty=30,
    ))
    await session.refresh(bet)
    assert bet.realized_pnl_cents == 600
    assert bet.remaining_quantity == 70

    # Market settles YES=100. Remaining 70 contracts pay 100 each → settle
    # pnl = (100 - 40) * 70 = 4200. Total realized = 600 + 4200 = 4800.
    await settle_bets_for_market(
        session, ticker=SOCCER_TICKER, settlement_value_cents=100,
    )
    await session.refresh(bet)
    assert bet.status == BetStatus.WON
    assert bet.realized_pnl_cents == 4800
    assert bet.pnl_cents == 4800
    assert bet.remaining_quantity == 0


@pytest.mark.asyncio
async def test_record_placed_order_sell_does_not_create_bet(
    session: AsyncSession,
) -> None:
    from src.kalshi.schemas import Order

    bet_a = await _open_bet(session, order_id="ord-a", qty=10, price=30)
    sell_order = Order(
        order_id="sell-x",
        client_order_id="cli-sell-x",
        ticker=SOCCER_TICKER,
        side="yes",
        action="sell",
        type="limit",
        status="resting",
        yes_price=40,
        no_price=60,
        count=10,
        remaining_count=10,
    )
    returned = await record_placed_order(
        session,
        order=sell_order,
        client_order_id="cli-sell-x",
        requested_count=10,
        requested_price_cents=40,
        action="sell",
    )
    # Should echo the opener, not create a new bet.
    assert returned is not None
    assert returned.id == bet_a.id
    bets = (await session.execute(select(Bet))).scalars().all()
    assert len(bets) == 1
