"""Tests for the bet_fill + FIFO partial-close logic in bet_service.

These cover the core "1 bet = 1 buy decision, sells aggregate via FIFO"
contract. Fee enrichment runs separately via fills_sync; here we verify
the fill bookkeeping itself.
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
from src.core.types import (
    BetSide,
    BetSource,
    BetStatus,
    Confidence,
    ExitType,
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
    reprice_bet_for_amend,
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
        remaining_quantity_centi=qty * 100,
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
async def test_orphan_buy_fill_dropped_not_persisted(session: AsyncSession) -> None:
    # WS fill for an order we don't have a Bet for — could be a race
    # (orders route hasn't committed yet) or a true external fill. We
    # drop it from the WS path so we don't pollute the external-fill
    # audit surface; fills_sync's REST sweep records true externals.
    await _make_market(session)
    await record_fill(session, _make_fill(
        trade_id="ext", order_id="external-1", side="yes", action="buy",
        price_cents=30, qty=5,
    ))
    fill = await session.scalar(
        select(BetFill).where(BetFill.trade_id == "ext")
    )
    assert fill is None  # nothing persisted from the WS orphan path


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


@pytest.mark.asyncio
async def test_no_side_buy_records_no_price_not_yes_complement(
    session: AsyncSession,
) -> None:
    """A NO buy must record the NO price as entry, not 100 - price. Kalshi's
    order response populates BOTH yes_price and no_price (complementary), so
    always taking yes_price stored the complement and corrupted NO-bet P&L."""
    from src.kalshi.schemas import Order

    # NO buy at 35¢ — Kalshi returns yes_price=65, no_price=35.
    no_order = Order(
        order_id="ord-no",
        client_order_id="cli-no",
        ticker=SOCCER_TICKER,
        side="no",
        action="buy",
        type="limit",
        status="resting",
        yes_price=65,
        no_price=35,
        count=10,
        remaining_count=10,
    )
    bet = await record_placed_order(
        session,
        order=no_order,
        client_order_id="cli-no",
        requested_count=10,
        requested_price_cents=35,
        action="buy",
    )
    assert bet is not None
    assert bet.entry_price_cents == 35  # NO price, not the 65 complement


@pytest.mark.asyncio
async def test_yes_side_buy_records_yes_price(session: AsyncSession) -> None:
    from src.kalshi.schemas import Order

    yes_order = Order(
        order_id="ord-yes",
        client_order_id="cli-yes",
        ticker=SOCCER_TICKER,
        side="yes",
        action="buy",
        type="limit",
        status="resting",
        yes_price=42,
        no_price=58,
        count=5,
        remaining_count=5,
    )
    bet = await record_placed_order(
        session,
        order=yes_order,
        client_order_id="cli-yes",
        requested_count=5,
        requested_price_cents=42,
        action="buy",
    )
    assert bet is not None
    assert bet.entry_price_cents == 42


@pytest.mark.asyncio
async def test_cross_opener_sell_pro_rates_kalshi_fee(session: AsyncSession) -> None:
    """When a WS sell spans two openers, fills_sync's pro-rata split must
    distribute the single Kalshi fee_cost across the synthetic bet_fill
    rows by quantity_centi. Each opener's exit_fees_cents should reflect
    its share — not the full fee landing on one opener."""
    from src.kalshi.schemas import Fill as RestFill
    from src.services.bet_service import recompute_bet_from_fills
    from src.services.fills_sync import _ingest_rest_fill

    bet_a = await _open_bet(session, order_id="ord-a", qty=30, price=20)
    bet_b = await _open_bet(session, order_id="ord-b", qty=70, price=25)
    await record_fill(session, _make_fill(
        trade_id="ba", order_id="ord-a", side="yes", action="buy",
        price_cents=20, qty=30,
    ))
    await record_fill(session, _make_fill(
        trade_id="bb", order_id="ord-b", side="yes", action="buy",
        price_cents=25, qty=70,
    ))

    # Sell 100 @ 40¢ spanning both openers (30 from A, 70 from B).
    await record_fill(session, _make_fill(
        trade_id="sx", order_id="sell-x", side="yes", action="sell",
        price_cents=40, qty=100,
    ))

    # Simulate the REST fill arriving with the authoritative fee_cost = 13¢.
    # Pro-rated 30:70 → A gets 4¢ (30*13/100 = 3.9), B gets 9¢. Largest-
    # remainder rounding distributes the leftover cent to B (remainder 10
    # vs A's 90; the larger remainder wins).
    rest = RestFill(
        trade_id="sx",
        order_id="sell-x",
        ticker=SOCCER_TICKER,
        side="yes",
        action="sell",
        count_centi=10000,
        yes_price=40,
        no_price=60,
        is_taker=True,
        fee_cents=13,
        created_time=datetime.now(timezone.utc),
    )
    affected = await _ingest_rest_fill(session, rest_fill=rest)
    assert {bet_a.id, bet_b.id} == affected
    for bid in affected:
        bet = await session.get(Bet, bid)
        await recompute_bet_from_fills(session, bet=bet)
    await session.flush()

    await session.refresh(bet_a)
    await session.refresh(bet_b)
    # Pro-rata math: A gets floor(13*30/100) = 3, remainder = 13*30 - 3*100 = 90
    # B gets floor(13*70/100) = 9, remainder = 13*70 - 9*100 = 10
    # Leftover cent = 13 - (3 + 9) = 1, goes to highest remainder (A).
    # So A = 4¢, B = 9¢. Total = 13¢ exactly.
    assert bet_a.exit_fees_cents + bet_b.exit_fees_cents == 13
    assert bet_a.exit_fees_cents == 4
    assert bet_b.exit_fees_cents == 9


@pytest.mark.asyncio
async def test_ws_after_external_fill_binds_bet_without_losing_fee(
    session: AsyncSession,
) -> None:
    """fills_sync may insert a bet_fill with bet_id=NULL before WS delivers
    the same trade. When WS arrives, the existing row gets attached to the
    bet — its already-populated fee_cents survives, and the bet's fee
    aggregate gets recomputed."""
    bet = await _open_bet(session, order_id="ord-1", qty=5, price=30)

    # Simulate fills_sync seeing a buy fill before WS delivered it.
    early = BetFill(
        bet_id=None,
        trade_id="early-1",
        order_id="ord-1",
        ticker=SOCCER_TICKER,
        side="yes",
        action="buy",
        price_cents=30,
        quantity_centi=500,
        fee_cents=2,
        is_taker=True,
        fee_synced_at=datetime.now(timezone.utc),
        created_time=datetime.now(timezone.utc),
    )
    session.add(early)
    await session.flush()

    # Now WS delivers the same trade.
    await record_fill(session, _make_fill(
        trade_id="early-1", order_id="ord-1", side="yes", action="buy",
        price_cents=30, qty=5,
    ))

    # The bet_fill is now attached to the bet, the fee is intact, the
    # bet-level entry_fees_cents reflects it.
    await session.refresh(bet)
    bound = await session.scalar(
        select(BetFill).where(BetFill.trade_id == "early-1")
    )
    assert bound is not None
    assert bound.bet_id == bet.id
    assert bound.fee_cents == 2
    assert bet.entry_fees_cents == 2

    # No duplicate row was created.
    count = await session.scalar(
        select(func.count(BetFill.id)).where(BetFill.trade_id == "early-1")
    )
    assert count == 1


@pytest.mark.asyncio
async def test_partial_close_net_pnl_uses_realized(session: AsyncSession) -> None:
    """A bet that has partially closed shows realized_pnl in the ledger
    even though it's still OPEN. The ledger's net_pnl_cents falls back to
    realized when pnl_cents is None."""
    from src.api.routes.ledger import _bet_to_dict

    bet = await _open_bet(session, order_id="ord-1", qty=100, price=40)
    await record_fill(session, _make_fill(
        trade_id="b1", order_id="ord-1", side="yes", action="buy",
        price_cents=40, qty=100,
    ))
    await record_fill(session, _make_fill(
        trade_id="s1", order_id="sell-1", side="yes", action="sell",
        price_cents=60, qty=30,
    ))
    # Simulate fee enrichment on the sell fill (Kalshi charged 3¢).
    sell_fill = await session.scalar(
        select(BetFill).where(BetFill.trade_id == "s1")
    )
    sell_fill.fee_cents = 3
    bet.exit_fees_cents = 3
    await session.flush()
    await session.refresh(bet)

    out = _bet_to_dict(bet, ticker=SOCCER_TICKER)
    assert bet.status == BetStatus.OPEN
    assert out["pnl_cents"] is None
    assert out["realized_pnl_cents"] == 600
    assert out["fees_cents"] == 3
    # Net falls back to realized - fees when pnl_cents is None.
    assert out["net_pnl_cents"] == 597


@pytest.mark.asyncio
async def test_cross_opener_sell_write_time_fee_split(session: AsyncSession) -> None:
    """When the canonical bet_fill already has fee_cents (fills_sync ran
    first), record_fill's cross-opener split should pro-rate the fee at
    write time so the transient misallocation window doesn't exist."""
    bet_a = await _open_bet(session, order_id="ord-a", qty=30, price=20)
    bet_b = await _open_bet(session, order_id="ord-b", qty=70, price=25)
    await record_fill(session, _make_fill(
        trade_id="ba", order_id="ord-a", side="yes", action="buy",
        price_cents=20, qty=30,
    ))
    await record_fill(session, _make_fill(
        trade_id="bb", order_id="ord-b", side="yes", action="buy",
        price_cents=25, qty=70,
    ))

    # Simulate fills_sync having seen the sell first (before WS).
    pre = BetFill(
        bet_id=None,
        trade_id="sx",
        order_id="sell-x",
        ticker=SOCCER_TICKER,
        side="yes",
        action="sell",
        price_cents=40,
        quantity_centi=10000,
        fee_cents=13,  # Kalshi's authoritative fee for the whole trade
        is_taker=True,
        fee_synced_at=datetime.now(timezone.utc),
        created_time=datetime.now(timezone.utc),
    )
    session.add(pre)
    await session.flush()

    # Now WS delivers the sell.
    await record_fill(session, _make_fill(
        trade_id="sx", order_id="sell-x", side="yes", action="sell",
        price_cents=40, qty=100,
    ))
    await session.refresh(bet_a)
    await session.refresh(bet_b)

    # Fee is split immediately, no fills_sync sweep needed.
    assert bet_a.exit_fees_cents + bet_b.exit_fees_cents == 13
    assert bet_a.exit_fees_cents == 4
    assert bet_b.exit_fees_cents == 9


@pytest.mark.asyncio
async def test_reprice_recovers_bet_cancelled_by_race(session: AsyncSession) -> None:
    """Amend cancel-race: Kalshi emits user_order(canceled) for the OLD order_id,
    which can flip the bet CANCELLED before reprice runs. Since the amend
    succeeded (order rests at the new id), reprice must re-point AND restore the
    bet to OPEN — not no-op and leave a live order with a dead bet."""
    bet = await _open_bet(session, order_id="old-id", qty=100, price=40)
    bet.status = BetStatus.CANCELLED  # the racing WS cancel landed first
    bet.exit_type = ExitType.CLOSED_EARLY
    await session.flush()

    out = await reprice_bet_for_amend(
        session, old_order_id="old-id", new_order_id="new-id",
        new_price_cents=42, new_count=100,
    )
    assert out is not None
    assert out.status == BetStatus.OPEN
    assert out.exit_type is None
    assert out.kalshi_order_id == "new-id"
    assert out.entry_price_cents == 42


@pytest.mark.asyncio
async def test_reprice_refuses_settled_bet(session: AsyncSession) -> None:
    """A WON/LOST bet is genuine settlement, never a cancel race — reprice must
    refuse to resurrect it even if an amend arrives for its (stale) order_id."""
    bet = await _open_bet(session, order_id="old-id", qty=100, price=40)
    bet.status = BetStatus.WON
    await session.flush()

    out = await reprice_bet_for_amend(
        session, old_order_id="old-id", new_order_id="new-id",
        new_price_cents=42, new_count=100,
    )
    assert out is None
    await session.refresh(bet)
    assert bet.status == BetStatus.WON
    assert bet.kalshi_order_id == "old-id"  # untouched


@pytest.mark.asyncio
async def test_reprice_skips_when_fills_exist(session: AsyncSession) -> None:
    """Belt-and-suspenders: reprice never clobbers a bet that has fills, even if
    it's somehow reached here (route guards first)."""
    bet = await _open_bet(session, order_id="old-id", qty=100, price=40)
    session.add(BetFill(
        bet_id=bet.id, trade_id="f1", order_id="old-id", ticker=SOCCER_TICKER,
        side="yes", action="buy", price_cents=40, quantity_centi=4000,
    ))
    await session.flush()

    out = await reprice_bet_for_amend(
        session, old_order_id="old-id", new_order_id="new-id",
        new_price_cents=42, new_count=100,
    )
    assert out is None
    await session.refresh(bet)
    assert bet.kalshi_order_id == "old-id"  # untouched
