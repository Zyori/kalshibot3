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
from src.core.types import (
    BetSide,
    BetSource,
    BetStatus,
    Sport,
    Strategy,
)
from src.kalshi.ws_wire import Fill, FillPayload
from src.models import Bet, BetFill, ComboLeg, Market, PendingCombo
from src.services.bet_service import (
    ComboLegInput,
    recompute_bet_from_fills,
    record_external_combo,
    record_fill,
    settle_bets_for_market,
)

COMBO_TICKER = "KXMVESPORTSMULTIGAMEEXTENDED-S202662EADA40D40-43319250880"


def _combo_fill(
    *, order_id: str, price_cents: int, qty: int, ticker: str = COMBO_TICKER,
    trade_id: str | None = None,
) -> Fill:
    """A WS buy fill on a combo ticker (the async RFQ fill). `trade_id` defaults
    to t-{order_id}; pass it explicitly to simulate two partial fills on the
    same order (same order_id, distinct trade_id)."""
    yes_p = price_cents
    return Fill(
        type="fill", sid=1,
        msg=FillPayload(
            trade_id=trade_id or f"t-{order_id}", order_id=order_id, ticker=ticker,
            side="yes", action="buy", count_centi=qty * 100,
            yes_price_cents=yes_p, no_price_cents=100 - yes_p,
            is_taker=True, ts=datetime.now(timezone.utc),
        ),
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


def test_subtitle_titles_normal_and_comma_guard():
    """yes_sub_title → per-leg labels; a comma inside a label must NOT misalign
    the rest — it falls back to all-None (ticker shown) rather than wrong names."""
    from src.api.routes.combos import _subtitle_titles

    # Normal: 3 clean segments for 3 legs.
    assert _subtitle_titles("yes Canada,yes Georgia,no Brazil", 3) == [
        "Canada", "Georgia", "Brazil",
    ]
    # Count mismatch (a label with a comma would do this) → all None, never wrong.
    assert _subtitle_titles("yes Trinidad, Tobago,yes Georgia", 2) == [None, None]
    # Missing subtitle → all None of the right length.
    assert _subtitle_titles(None, 2) == [None, None]
    assert _subtitle_titles("", 3) == [None, None, None]


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


# === RFQ fill-driven recording: the combo bet is created when the async fill
# lands, using legs stashed in pending_combo at accept time. ===


async def _stash_pending(session: AsyncSession, *, count: int = 10) -> None:
    session.add(PendingCombo(
        combo_ticker=COMBO_TICKER, side="yes", count=count,
        legs_json=[
            {"leg_ticker": "KXINTLFRIENDLYGAME-26JUN05CANIRL-CAN",
             "leg_event_ticker": "KXINTLFRIENDLYGAME-26JUN05CANIRL",
             "leg_title": None, "side": "yes"},
            {"leg_ticker": "KXINTLFRIENDLYGAME-26JUN05GEOBHR-GEO",
             "leg_event_ticker": "KXINTLFRIENDLYGAME-26JUN05GEOBHR",
             "leg_title": None, "side": "yes"},
        ],
        strategy="lock_parlay", confidence="medium", timing="pre_match",
        tags_json=["rfq"], human_reasoning="test parlay",
    ))
    await session.flush()


@pytest.mark.asyncio
async def test_rfq_fill_creates_combo_bet_from_pending(session: AsyncSession):
    """A combo buy fill with a matching pending_combo creates the bet keyed to
    the fill's real order_id, source=HUMAN/verified, legs from the stash, and
    consumes (deletes) the pending row."""
    await _stash_pending(session, count=10)

    captures = await record_fill(session, _combo_fill(order_id="ord-rfq", price_cents=18, qty=10))
    await session.flush()

    bet = (await session.execute(
        select(Bet).where(Bet.kalshi_order_id == "ord-rfq")
    )).scalar_one()
    assert bet.sport == Sport.COMBO
    assert bet.source == BetSource.HUMAN  # placed through the app, not external
    assert bet.verified is True
    assert bet.side == BetSide.YES
    assert bet.entry_price_cents == 18  # from the fill
    assert bet.strategy == Strategy.LOCK_PARLAY
    assert bet.tags == ["rfq"]
    # The fill attached + entry recorded.
    from src.core.types import SnapshotPhase
    assert captures == [(bet.id, SnapshotPhase.ENTRY)]
    legs = (await session.execute(
        select(ComboLeg).where(ComboLeg.bet_id == bet.id)
    )).scalars().all()
    assert len(legs) == 2
    # Pending row consumed.
    assert await session.scalar(select(func.count(PendingCombo.id))) == 0


@pytest.mark.asyncio
async def test_combo_fill_without_pending_is_dropped(session: AsyncSession):
    """A combo buy fill with NO pending_combo (and no existing bet) is dropped —
    we never invent a bet for a combo we didn't accept (isolation invariant)."""
    captures = await record_fill(session, _combo_fill(order_id="ord-x", price_cents=18, qty=10))
    assert captures == []
    assert await session.scalar(select(func.count(Bet.id))) == 0


@pytest.mark.asyncio
async def test_pending_combo_idempotent_on_replayed_fill(session: AsyncSession):
    """The same fill replayed (WS reconnect) doesn't create a second bet."""
    await _stash_pending(session, count=10)
    fill = _combo_fill(order_id="ord-rfq", price_cents=18, qty=10)
    await record_fill(session, fill)
    await session.flush()
    # Replay the identical fill — dedup on trade_id should no-op.
    await record_fill(session, fill)
    await session.flush()
    assert await session.scalar(select(func.count(Bet.id))) == 1


@pytest.mark.asyncio
async def test_rfq_combo_records_at_ordered_size(session: AsyncSession):
    """An RFQ combo records at the ORDERED size (pending.count), like every
    other bet — we deliberately do NOT auto-derive filled size from the async
    fill (that reconciliation was a persistent money-path bug source). entry/
    stake refine from the fill; quantity is the order."""
    await _stash_pending(session, count=75)
    await record_fill(session, _combo_fill(order_id="ord-ord", price_cents=18, qty=75))
    await session.flush()

    bet = (await session.execute(
        select(Bet).where(Bet.kalshi_order_id == "ord-ord")
    )).scalar_one()
    assert bet.quantity == 75
    assert bet.remaining_quantity_centi == 7500
    assert bet.entry_price_cents == 18  # refined from the fill


@pytest.mark.asyncio
async def test_combo_partial_fill_flags_unreconciled_then_clears(session: AsyncSession):
    """A combo that fills short of its ordered count gets tagged for manual
    review (quantity isn't auto-reconciled). A later fill that completes the
    order clears the tag. The recorded quantity stays at the ordered size."""
    await _stash_pending(session, count=10)  # ordered 10

    # First partial: 6 of 10 fill.
    await record_fill(session, _combo_fill(order_id="ord-p", price_cents=18, qty=6, trade_id="t-1"))
    await session.flush()
    bet = (await session.execute(
        select(Bet).where(Bet.kalshi_order_id == "ord-p")
    )).scalar_one()
    assert bet.quantity == 10  # recorded at ordered size, not shrunk
    assert "size-unreconciled" in (bet.tags or [])

    # Remaining 4 fill on the same order (distinct trade_id) → order complete.
    await record_fill(session, _combo_fill(order_id="ord-p", price_cents=18, qty=4, trade_id="t-2"))
    await session.flush()
    await session.refresh(bet)
    assert "size-unreconciled" not in (bet.tags or [])


@pytest.mark.asyncio
async def test_logged_combo_quantity_survives_later_fill_recompute(session: AsyncSession):
    """Regression guard: a combo LOGGED via record_external_combo (with an
    order_id — the common /combos path) must keep its recorded quantity when a
    fill later back-links and triggers recompute. (A prior fix misclassified it
    as an RFQ combo and shrank its size to the fill centi.)"""
    bet = await _record(session, order_id="ord-logged")  # quantity=95
    await session.flush()
    # A fill of fewer contracts back-links and recompute runs (fills_sync).
    session.add(BetFill(
        bet_id=bet.id, trade_id="t-logged", order_id="ord-logged",
        ticker=COMBO_TICKER, side="yes", action="buy",
        price_cents=17, quantity_centi=6000, fee_cents=0,
    ))
    await session.flush()
    await recompute_bet_from_fills(session, bet=bet)
    await session.flush()
    # Logged quantity preserved — NOT shrunk to 60.
    assert bet.quantity == 95
    assert bet.remaining_quantity_centi == 9500


@pytest.mark.asyncio
async def test_bad_enum_in_pending_defaults_not_drops(session: AsyncSession):
    """An invalid stored strategy must NOT raise (which would roll back and lose
    the fill) — it defaults, and the bet/legs/fill all still record."""
    session.add(PendingCombo(
        combo_ticker=COMBO_TICKER, side="yes", count=10,
        legs_json=[{"leg_ticker": "L1", "leg_event_ticker": "E1",
                    "leg_title": None, "side": "yes"},
                   {"leg_ticker": "L2", "leg_event_ticker": "E2",
                    "leg_title": None, "side": "yes"}],
        strategy="not_a_real_strategy",  # invalid
        confidence="medium", timing="pre_match", tags_json=None, human_reasoning=None,
    ))
    await session.flush()

    captures = await record_fill(session, _combo_fill(order_id="ord-bad", price_cents=18, qty=10))
    await session.flush()
    bet = (await session.execute(
        select(Bet).where(Bet.kalshi_order_id == "ord-bad")
    )).scalar_one()
    assert bet.strategy == Strategy.MANUAL  # safe default, not a crash
    assert bet.quantity == 10
    assert captures  # the fill was recorded, not lost


def test_placeable_combo_allowlist():
    """Only the sports multi-game parlay series is placeable; every other MVE
    family (cross-category, hypothetical new series, or a name that merely
    starts with ours) is refused on the money path — allowlist, segment-exact."""
    from src.sports.combo import is_combo_ticker, is_placeable_sports_combo

    sports = "KXMVESPORTSMULTIGAMEEXTENDED-S2026X-Y"
    cross = "KXMVECROSSCATEGORY-S2026X-Y"
    future = "KXMVESOMETHINGNEW-S2026X-Y"
    prefix_collision = "KXMVESPORTSMULTIGAMEEXTENDEDPLUS-S2026X-Y"

    # All are recognized as combos (firewall/ledger).
    assert is_combo_ticker(sports)
    assert is_combo_ticker(cross)
    assert is_combo_ticker(prefix_collision)
    # But only the exact sports series is placeable.
    assert is_placeable_sports_combo(sports)
    assert not is_placeable_sports_combo(cross)
    assert not is_placeable_sports_combo(future)
    # A future series whose name starts with ours must NOT slip through.
    assert not is_placeable_sports_combo(prefix_collision)
