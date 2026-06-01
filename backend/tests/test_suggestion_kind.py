"""Tests for the SUGGESTION.kind discriminator (entry vs exit).

U1 adds `kind` as a NOT NULL column with a CHECK constraint in
('entry', 'exit'). These verify both kinds round-trip, the column is
required, and an invalid kind is rejected at the DB boundary.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from src.core.db import Base
from src.core.types import (
    BetSide,
    Confidence,
    Sport,
    Strategy,
    SuggestionKind,
    SuggestionStatus,
    Urgency,
)
from src.models import Market, Suggestion

TICKER = "KXWCGAME-26JUN11MEXRSA-MEX"


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # CHECK constraints are only enforced by SQLite when foreign_keys-style
        # pragmas are on; CHECKs are always enforced, but be explicit that this
        # in-memory engine mirrors the app's enforcement.
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        market = Market(
            sport=Sport.SOCCER, game_id=None, kalshi_ticker=TICKER,
            market_type="match_result", title=TICKER,
            yes_price_cents=None, no_price_cents=None, volume=None,
            close_time=None, status="open",
        )
        s.add(market)
        await s.commit()
        yield s
    await engine.dispose()


def _suggestion(market_id: int, kind: SuggestionKind) -> Suggestion:
    return Suggestion(
        sport=Sport.SOCCER,
        market_id=market_id,
        kind=kind,
        side=BetSide.YES,
        suggested_price_cents=42,
        suggested_size_cents=100,
        strategy=Strategy.MEAN_REVERSION,
        justification="run of play says the draw is cheap",
        confidence=Confidence.MEDIUM,
        urgency=Urgency.MEDIUM,
        status=SuggestionStatus.PENDING,
    )


async def _market_id(session: AsyncSession) -> int:
    return (await session.execute(select(Market.id))).scalar_one()


async def test_entry_and_exit_round_trip(session: AsyncSession) -> None:
    mid = await _market_id(session)
    session.add(_suggestion(mid, SuggestionKind.ENTRY))
    session.add(_suggestion(mid, SuggestionKind.EXIT))
    await session.commit()

    rows = (await session.execute(select(Suggestion).order_by(Suggestion.id))).scalars().all()
    assert [r.kind for r in rows] == [SuggestionKind.ENTRY, SuggestionKind.EXIT]


async def test_kind_is_required(session: AsyncSession) -> None:
    mid = await _market_id(session)
    s = _suggestion(mid, SuggestionKind.ENTRY)
    s.kind = None  # type: ignore[assignment]
    session.add(s)
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_invalid_kind_rejected_by_check(session: AsyncSession) -> None:
    # Bypass the enum by writing raw SQL — the model type would coerce, but the
    # DB CHECK is the real guard we're verifying.
    mid = await _market_id(session)
    with pytest.raises(IntegrityError):
        await session.execute(
            text(
                "INSERT INTO suggestion "
                "(sport, market_id, kind, side, suggested_price_cents, "
                " suggested_size_cents, strategy, justification, confidence, "
                " urgency, status) "
                "VALUES ('soccer', :mid, 'foo', 'yes', 42, 100, "
                "'mean_reversion', 'x', 'medium', 'medium', 'pending')"
            ),
            {"mid": mid},
        )
        await session.commit()
