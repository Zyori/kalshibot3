"""Route tests for GET /api/partner/context (U2).

The context endpoint composes the dashboard's own handlers, so these focus on:
  - global scope (no ?event) returns positions + recent_trades + bankroll
  - parity: a position's unrealized PnL matches what /positions reports
  - empty book returns empty arrays, not nulls or errors
  - a non-soccer ?event ticker is refused (cross-market isolation, via get_event)

The happy-path ?event= branch needs a live supervisor (market discovery +
live state) and is covered by the U9 dry run, not here.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

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
from src.models import Bet, Market, Position

TICKER = "KXWCGAME-26JUN11MEXRSA-MEX"


async def _seed(factory: async_sessionmaker, *, with_rows: bool) -> None:
    if not with_rows:
        return
    async with factory() as s:
        market = Market(
            sport=Sport.SOCCER, game_id=None, kalshi_ticker=TICKER,
            market_type="match_result", title=TICKER,
            yes_price_cents=None, no_price_cents=None, volume=None,
            close_time=None, status=MarketStatus.OPEN,
        )
        s.add(market)
        await s.flush()
        s.add(Position(
            sport=Sport.SOCCER, kalshi_ticker=TICKER, market_id=market.id,
            side=BetSide.YES, quantity=10, avg_entry_price_cents=40,
            cost_basis_cents=400, current_price_cents=61,
            unrealized_pnl_cents=210, realized_pnl_cents=0, fees_paid_cents=0,
            last_synced=datetime.now(timezone.utc),
        ))
        s.add(Bet(
            sport=Sport.SOCCER, market_id=market.id, kalshi_order_id="ord-1",
            client_order_id="cli-1", side=BetSide.YES, entry_price_cents=40,
            quantity=10, remaining_quantity=10, remaining_quantity_centi=1000,
            stake_cents=400, status=BetStatus.OPEN, source=BetSource.HUMAN,
            strategy=Strategy.MEAN_REVERSION, confidence=Confidence.MEDIUM,
            timing=Timing.LIVE, verified=True,
            placed_at=datetime.now(timezone.utc),
        ))
        await s.commit()


async def _make_client(*, with_rows: bool) -> AsyncClient:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await _seed(factory, with_rows=with_rows)

    async def _override() -> AsyncSession:
        async with factory() as s:
            yield s

    app.dependency_overrides[get_session] = _override
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://t")
    client._engine = engine  # type: ignore[attr-defined]  # for teardown
    return client


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    c = await _make_client(with_rows=True)
    yield c
    app.dependency_overrides.pop(get_session, None)
    await c._engine.dispose()  # type: ignore[attr-defined]
    await c.aclose()


@pytest_asyncio.fixture
async def empty_client() -> AsyncClient:
    c = await _make_client(with_rows=False)
    yield c
    app.dependency_overrides.pop(get_session, None)
    await c._engine.dispose()  # type: ignore[attr-defined]
    await c.aclose()


async def test_global_scope_returns_positions_and_trades(client: AsyncClient) -> None:
    res = await client.get("/api/partner/context")
    assert res.status_code == 200
    body = res.json()
    assert body["scope"] == "book"
    assert "event" not in body
    assert len(body["positions"]) == 1
    assert len(body["recent_trades"]) == 1
    assert "bankroll_cents" in body  # present even if None (no lifespan in test)


async def test_position_pnl_parity_with_positions_route(client: AsyncClient) -> None:
    ctx = (await client.get("/api/partner/context")).json()
    pos = (await client.get("/api/positions")).json()
    # Same code path → identical numbers. Single source of truth.
    assert ctx["positions"] == pos["positions"]
    assert ctx["positions"][0]["unrealized_pnl_cents"] == 210


async def test_empty_book_returns_empty_arrays(empty_client: AsyncClient) -> None:
    res = await empty_client.get("/api/partner/context")
    assert res.status_code == 200
    body = res.json()
    assert body["positions"] == []
    assert body["recent_trades"] == []


async def test_non_soccer_event_is_refused(client: AsyncClient) -> None:
    # ?event= hits get_event, which rejects non-soccer tickers before any
    # supervisor access — cross-market isolation.
    res = await client.get("/api/partner/context", params={"event": "KXPRES-2028"})
    assert res.status_code == 400
