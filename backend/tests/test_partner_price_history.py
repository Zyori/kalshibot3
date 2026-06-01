"""/partner/context surfaces the price-history trajectory.

Reuses the in-memory app harness from test_partner_context (real app, no
lifespan → app.state.price_history is absent unless we set it). Covers the
best-effort no-buffer path and the buffer-present path.
"""
from __future__ import annotations

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.core.db import get_session
from src.main import app
from src.services.price_history import PriceHistory

from tests.test_partner_context import _make_client


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    c = await _make_client(with_rows=True)
    yield c
    app.dependency_overrides.pop(get_session, None)
    # Clean any price_history we attached so tests don't leak into each other.
    if hasattr(app.state, "price_history"):
        delattr(app.state, "price_history")
    await c._engine.dispose()  # type: ignore[attr-defined]
    await c.aclose()


async def test_no_buffer_yields_empty_series(client: AsyncClient) -> None:
    """No price_history on app.state (the test/just-restarted case) → endpoint
    still 200, every position carries an empty series (best-effort, never 500)."""
    res = await client.get("/api/partner/context")
    assert res.status_code == 200
    body = res.json()
    assert body["positions"][0]["price_history"] == []


async def test_position_carries_recorded_series(client: AsyncClient) -> None:
    """With samples in the buffer for a held market, the series surfaces on that
    position as integer-cent mids, oldest first."""
    ph = PriceHistory()
    ticker = "KXWCGAME-26JUN11MEXRSA-MEX"  # the seeded position's market
    for mid in (40, 48, 55, 61):
        ph.record(ticker, mid)
    app.state.price_history = ph

    body = (await client.get("/api/partner/context")).json()
    series = body["positions"][0]["price_history"]
    assert [s["mid_cents"] for s in series] == [40, 48, 55, 61]
    assert all(isinstance(s["mid_cents"], int) for s in series)


async def test_untracked_market_empty_even_with_buffer(client: AsyncClient) -> None:
    """A buffer that has other tickers but not this position's → empty series,
    not someone else's data."""
    ph = PriceHistory()
    ph.record("SOME-OTHER-TICKER", 50)
    app.state.price_history = ph

    body = (await client.get("/api/partner/context")).json()
    assert body["positions"][0]["price_history"] == []
