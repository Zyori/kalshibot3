"""Route-level tests for the ledger API.

Covers the P0 regression where GET /api/ledger?market= built a second JOIN on
Market on top of the one list_bets already adds, producing
`sqlite3.OperationalError: ambiguous column name: market.kalshi_ticker` and
500ing every market-filtered request.
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


@pytest_asyncio.fixture
async def client_ordered() -> AsyncClient:
    """Three bets whose insert order (id) deliberately disagrees with their
    placed_at order, so a test can tell an id-sort from a placed_at-sort."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as s:
        market = Market(
            sport=Sport.SOCCER, game_id=None, kalshi_ticker=TICKER,
            market_type="match_result", title=TICKER, yes_price_cents=None,
            no_price_cents=None, volume=None, close_time=None,
            status=MarketStatus.OPEN,
        )
        s.add(market)
        await s.flush()
        # Insert order: A, B, C (ids 1,2,3). placed_at order: B newest, then A,
        # then C oldest — so chronological desc is B, A, C, NOT C, B, A (id desc).
        times = {
            "A": datetime(2026, 6, 5, 20, 0, tzinfo=timezone.utc),
            "B": datetime(2026, 6, 5, 22, 0, tzinfo=timezone.utc),
            "C": datetime(2026, 6, 5, 18, 0, tzinfo=timezone.utc),
        }
        for tag in ("A", "B", "C"):
            s.add(Bet(
                sport=Sport.SOCCER, market_id=market.id, kalshi_order_id=f"ord-{tag}",
                client_order_id=f"cli-{tag}", side=BetSide.YES, entry_price_cents=42,
                quantity=1, remaining_quantity=1, remaining_quantity_centi=100,
                stake_cents=42, status=BetStatus.OPEN, source=BetSource.HUMAN,
                strategy=Strategy.MANUAL, confidence=Confidence.MEDIUM,
                timing=Timing.PRE_MATCH, verified=True, placed_at=times[tag],
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
async def test_ledger_sorts_by_placed_at_not_insert_order(client_ordered: AsyncClient) -> None:
    """Newest placed_at first, regardless of insert (id) order."""
    res = await client_ordered.get("/api/ledger")
    assert res.status_code == 200
    order = [b["kalshi_order_id"] for b in res.json()["bets"]]
    assert order == ["ord-B", "ord-A", "ord-C"]  # 22:00, 20:00, 18:00


@pytest.mark.asyncio
async def test_ledger_cursor_pagination_no_drop_or_dupe(client_ordered: AsyncClient) -> None:
    """Keyset pagination by placed_at returns every bet exactly once, in order,
    across pages — the failure mode if the cursor still keyed on id alone."""
    seen: list[str] = []
    cursor = None
    for _ in range(5):  # generous bound; 3 bets paginate in 2 pages at limit=2
        params: dict[str, object] = {"limit": 2}
        if cursor is not None:
            params["cursor"] = cursor
        body = (await client_ordered.get("/api/ledger", params=params)).json()
        seen.extend(b["kalshi_order_id"] for b in body["bets"])
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert seen == ["ord-B", "ord-A", "ord-C"]  # in order, each once
