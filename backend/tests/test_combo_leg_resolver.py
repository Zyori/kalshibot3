"""Tests for resolve_combo_legs — which legs of a settled combo hit/missed.

A WON combo marks all legs hit with no network. A LOST combo looks up each
leg's own market result via the Kalshi client to find the miss(es).
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
from src.models import Bet, ComboLeg, Market
from src.services.combo_leg_resolver import resolve_combo_legs

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


class FakeClient:
    """Stand-in KalshiRestClient: returns canned market results per leg ticker,
    and counts calls so we can assert the WON path makes zero of them."""

    def __init__(self, results: dict[str, str]):
        self.results = results
        self.calls = 0

    async def get_market(self, ticker: str) -> dict:
        self.calls += 1
        return {"market": {"result": self.results.get(ticker, "")}}


async def _combo_bet(session: AsyncSession, *, status: BetStatus) -> Bet:
    market = Market(
        sport=Sport.COMBO, game_id=None, kalshi_ticker=COMBO_TICKER,
        market_type="combo", title=COMBO_TICKER, yes_price_cents=None,
        no_price_cents=None, volume=None, close_time=None,
        status="settled", settlement=None, settlement_detected_at=None,
    )
    session.add(market)
    await session.flush()
    bet = Bet(
        sport=Sport.COMBO, market_id=market.id, side=BetSide.YES,
        entry_price_cents=20, quantity=10, remaining_quantity=0,
        remaining_quantity_centi=0, stake_cents=200, status=status,
        source=BetSource.HUMAN, strategy=Strategy.LOCK_PARLAY,
        confidence=Confidence.MEDIUM, timing=Timing.PRE_MATCH, verified=True,
        version=1, placed_at=datetime.now(timezone.utc),
    )
    session.add(bet)
    await session.flush()
    for i, (tk, side) in enumerate([
        ("LEG-A", "yes"), ("LEG-B", "yes"), ("LEG-C", "yes"),
    ]):
        session.add(ComboLeg(
            bet_id=bet.id, leg_index=i, leg_ticker=tk, side=side, result=None,
        ))
    await session.flush()
    return bet


@pytest.mark.asyncio
async def test_won_combo_marks_all_legs_hit_without_network(session: AsyncSession):
    bet = await _combo_bet(session, status=BetStatus.WON)
    client = FakeClient({})  # would error if used meaningfully
    n = await resolve_combo_legs(session, client, bet=bet)  # type: ignore[arg-type]
    assert n == 3
    assert client.calls == 0  # WON is logical certainty — no per-leg lookups
    legs = (await session.execute(
        select(ComboLeg).where(ComboLeg.bet_id == bet.id)
    )).scalars().all()
    assert all(leg.result == "yes" for leg in legs)


@pytest.mark.asyncio
async def test_lost_combo_resolves_each_leg_from_kalshi(session: AsyncSession):
    bet = await _combo_bet(session, status=BetStatus.LOST)
    # Two legs hit, one missed (the reason the parlay lost).
    client = FakeClient({"LEG-A": "yes", "LEG-B": "no", "LEG-C": "yes"})
    n = await resolve_combo_legs(session, client, bet=bet)  # type: ignore[arg-type]
    assert n == 3
    assert client.calls == 3
    legs = {
        leg.leg_ticker: leg.result
        for leg in (await session.execute(
            select(ComboLeg).where(ComboLeg.bet_id == bet.id)
        )).scalars().all()
    }
    assert legs == {"LEG-A": "yes", "LEG-B": "no", "LEG-C": "yes"}
    # The missed leg is the one whose result != side (LEG-B).
    missed = [tk for tk, r in legs.items() if r != "yes"]
    assert missed == ["LEG-B"]


@pytest.mark.asyncio
async def test_idempotent_skips_resolved_legs(session: AsyncSession):
    bet = await _combo_bet(session, status=BetStatus.LOST)
    client = FakeClient({"LEG-A": "yes", "LEG-B": "no", "LEG-C": "yes"})
    await resolve_combo_legs(session, client, bet=bet)  # type: ignore[arg-type]
    assert client.calls == 3
    # Re-run: all resolved → no further lookups, returns 0.
    n2 = await resolve_combo_legs(session, client, bet=bet)  # type: ignore[arg-type]
    assert n2 == 0
    assert client.calls == 3  # unchanged


@pytest.mark.asyncio
async def test_won_leg_with_null_side_is_skipped_not_looped(session: AsyncSession):
    """A WON-combo leg with no recorded side must be skipped, not set to
    result=side=None (which would leave it pending and re-trigger every sweep)."""
    bet = await _combo_bet(session, status=BetStatus.WON)
    # Null out one leg's side to simulate the defensive edge.
    legs = (await session.execute(
        select(ComboLeg).where(ComboLeg.bet_id == bet.id).order_by(ComboLeg.leg_index)
    )).scalars().all()
    legs[1].side = None
    await session.flush()

    client = FakeClient({})
    n = await resolve_combo_legs(session, client, bet=bet)  # type: ignore[arg-type]
    assert n == 2  # the two sided legs marked; the null-side one skipped
    assert client.calls == 0
    refreshed = (await session.execute(
        select(ComboLeg).where(ComboLeg.bet_id == bet.id).order_by(ComboLeg.leg_index)
    )).scalars().all()
    assert refreshed[0].result == "yes"
    assert refreshed[1].result is None  # skipped, not forced to None-via-side
    assert refreshed[2].result == "yes"


@pytest.mark.asyncio
async def test_open_combo_is_skipped(session: AsyncSession):
    bet = await _combo_bet(session, status=BetStatus.OPEN)
    client = FakeClient({})
    n = await resolve_combo_legs(session, client, bet=bet)  # type: ignore[arg-type]
    assert n == 0
    assert client.calls == 0


@pytest.mark.asyncio
async def test_lost_leg_with_unknown_result_stays_pending(session: AsyncSession):
    bet = await _combo_bet(session, status=BetStatus.LOST)
    # LEG-C comes back scalar/empty → stays pending, not forced.
    client = FakeClient({"LEG-A": "yes", "LEG-B": "no", "LEG-C": ""})
    n = await resolve_combo_legs(session, client, bet=bet)  # type: ignore[arg-type]
    assert n == 2  # only the two clean results recorded
    pending = (await session.execute(
        select(ComboLeg).where(ComboLeg.bet_id == bet.id).where(ComboLeg.result.is_(None))
    )).scalars().all()
    assert len(pending) == 1
    assert pending[0].leg_ticker == "LEG-C"
