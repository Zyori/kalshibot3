"""Route-level tests for the ledger API.

Covers the P0 regression where GET /api/ledger?market= built a second JOIN on
Market on top of the one list_bets already adds, producing
`sqlite3.OperationalError: ambiguous column name: market.kalshi_ticker` and
500ing every market-filtered request.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from datetime import datetime, timezone

from src.core.db import Base, get_session
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
from src.main import app
from src.models import Bet, Market

TICKER = "KXWCGAME-26JUN11MEXRSA-MEX"


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as s:
        market = Market(
            sport=Sport.SOCCER, game_id=None, kalshi_ticker=TICKER,
            market_type="match_result", title=TICKER,
            yes_price_cents=None, no_price_cents=None, volume=None,
            close_time=None, status=MarketStatus.OPEN,
        )
        s.add(market)
        await s.flush()
        s.add(Bet(
            sport=Sport.SOCCER, market_id=market.id, kalshi_order_id="ord-1",
            client_order_id="cli-1", side=BetSide.YES, entry_price_cents=42,
            quantity=1, remaining_quantity=1, remaining_quantity_centi=100,
            stake_cents=42, status=BetStatus.OPEN, source=BetSource.HUMAN,
            strategy=Strategy.MANUAL, confidence=Confidence.MEDIUM,
            timing=Timing.PRE_MATCH, verified=True,
            placed_at=datetime.now(timezone.utc),
        ))
        await s.commit()

    async def _override() -> AsyncSession:
        async with factory() as s:
            yield s

    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        yield c
    app.dependency_overrides.pop(get_session, None)
    await engine.dispose()


@pytest.mark.asyncio
async def test_ledger_market_filter_does_not_500(client: AsyncClient) -> None:
    """The regression: a second JOIN on Market made this raise ambiguous-column."""
    res = await client.get("/api/ledger", params={"market": TICKER})
    assert res.status_code == 200
    body = res.json()
    assert any(b["ticker"] == TICKER for b in body["bets"])


@pytest.mark.asyncio
async def test_ledger_market_filter_excludes_other_markets(client: AsyncClient) -> None:
    res = await client.get("/api/ledger", params={"market": "KXOTHER-X"})
    assert res.status_code == 200
    assert res.json()["bets"] == []


@pytest.mark.asyncio
async def test_ledger_no_filter_returns_all(client: AsyncClient) -> None:
    res = await client.get("/api/ledger")
    assert res.status_code == 200
    assert len(res.json()["bets"]) == 1
