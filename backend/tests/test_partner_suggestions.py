"""Route tests for POST /api/partner/suggestions (U4).

Covers entry/exit happy paths, the exit bug-guard (must hold the position),
cross-market isolation, unknown-market rejection, Pydantic validation, and
the WS app-event broadcast.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from src.core.db import Base, get_session
from src.core.types import BetSide, MarketStatus, Sport, SuggestionStatus
from src.main import app
from src.models import Market, Position, Suggestion

TICKER = "KXWCGAME-26JUN11MEXRSA-MEX"


class _SpyBroadcaster:
    """Captures broadcast_app_event payloads so tests can assert the WS event."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def broadcast_app_event(self, event: dict[str, Any]) -> None:
        self.events.append(event)


@pytest_asyncio.fixture
async def ctx() -> tuple[AsyncClient, _SpyBroadcaster, async_sessionmaker]:
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
        # A held YES position so exit suggestions on YES are valid.
        s.add(Position(
            sport=Sport.SOCCER, kalshi_ticker=TICKER, market_id=market.id,
            side=BetSide.YES, quantity=10, avg_entry_price_cents=40,
            cost_basis_cents=400, current_price_cents=61,
            unrealized_pnl_cents=210, realized_pnl_cents=0, fees_paid_cents=0,
            last_synced=datetime.now(timezone.utc),
        ))
        await s.commit()

    async def _override() -> AsyncSession:
        async with factory() as s:
            yield s

    spy = _SpyBroadcaster()
    app.dependency_overrides[get_session] = _override
    app.state.broadcast = spy
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://t")
    try:
        yield client, spy, factory
    finally:
        app.dependency_overrides.pop(get_session, None)
        if hasattr(app.state, "broadcast"):
            delattr(app.state, "broadcast")
        await client.aclose()
        await engine.dispose()


def _entry_body(**over: Any) -> dict[str, Any]:
    body = {
        "kind": "entry",
        "ticker": TICKER,
        "side": "no",
        "suggested_price_cents": 24,
        "suggested_size_cents": 100,
        "strategy": "mean_reversion",
        "justification": "underdog scored early, draw is cheap",
        "confidence": "medium",
    }
    body.update(over)
    return body


async def test_entry_suggestion_persists_and_broadcasts(ctx) -> None:
    client, spy, factory = ctx
    res = await client.post("/api/partner/suggestions", json=_entry_body())
    assert res.status_code == 200
    body = res.json()
    assert body["kind"] == "entry"
    assert body["status"] == "pending"

    async with factory() as s:
        rows = (await s.execute(select(Suggestion))).scalars().all()
    assert len(rows) == 1
    # Columns are String, so the persisted value reads back as a plain str.
    # SuggestionKind is a StrEnum, so this equals SuggestionKind.ENTRY.
    assert rows[0].kind == "entry"
    assert rows[0].status == SuggestionStatus.PENDING

    assert len(spy.events) == 1
    assert spy.events[0]["type"] == "suggestion"
    assert spy.events[0]["suggestion_id"] == body["suggestion_id"]


async def test_exit_suggestion_on_held_position(ctx) -> None:
    client, spy, _ = ctx
    res = await client.post(
        "/api/partner/suggestions",
        json=_entry_body(kind="exit", side="yes", strategy="hedge",
                         justification="rich at 61, bank before 75'"),
    )
    assert res.status_code == 200
    assert res.json()["kind"] == "exit"
    assert len(spy.events) == 1


async def test_exit_suggestion_for_unheld_position_rejected(ctx) -> None:
    client, spy, _ = ctx
    # We hold YES, not NO — an exit on NO is a bug.
    res = await client.post(
        "/api/partner/suggestions",
        json=_entry_body(kind="exit", side="no", strategy="hedge"),
    )
    assert res.status_code == 400
    assert spy.events == []


async def test_non_soccer_ticker_rejected(ctx) -> None:
    client, spy, _ = ctx
    res = await client.post(
        "/api/partner/suggestions", json=_entry_body(ticker="KXPRES-2028")
    )
    assert res.status_code == 400
    assert spy.events == []


async def test_unknown_market_rejected(ctx) -> None:
    client, spy, _ = ctx
    res = await client.post(
        "/api/partner/suggestions",
        json=_entry_body(ticker="KXWCGAME-26JUN11XXXYYY-XXX"),
    )
    assert res.status_code == 404
    assert spy.events == []


@pytest.mark.parametrize(
    "over",
    [
        {"suggested_price_cents": 0},
        {"suggested_price_cents": 100},
        {"side": "maybe"},
        {"strategy": "not_a_strategy"},
        {"kind": "sideways"},
        {"justification": ""},
    ],
)
async def test_validation_rejects_bad_bodies(ctx, over) -> None:
    client, spy, _ = ctx
    res = await client.post("/api/partner/suggestions", json=_entry_body(**over))
    assert res.status_code == 422
    assert spy.events == []


async def test_list_returns_only_pending_newest_first(ctx) -> None:
    client, _, _ = ctx
    await client.post("/api/partner/suggestions", json=_entry_body())
    await client.post(
        "/api/partner/suggestions",
        json=_entry_body(side="yes", suggested_price_cents=55),
    )
    res = await client.get("/api/partner/suggestions")
    assert res.status_code == 200
    rows = res.json()["suggestions"]
    assert len(rows) == 2
    assert rows[0]["id"] > rows[1]["id"]  # newest first
    assert all(r["status"] == "pending" for r in rows)
    assert all(r["ticker"] == TICKER for r in rows)


async def test_dismiss_marks_rejected_and_drops_from_list(ctx) -> None:
    client, spy, _ = ctx
    created = (await client.post("/api/partner/suggestions", json=_entry_body())).json()
    sid = created["suggestion_id"]
    spy.events.clear()

    res = await client.post(f"/api/partner/suggestions/{sid}/dismiss")
    assert res.status_code == 200
    assert res.json()["status"] == "rejected"
    # broadcast carries the dismissed flag so other tabs drop the card
    assert spy.events and spy.events[-1].get("dismissed") is True

    remaining = (await client.get("/api/partner/suggestions")).json()["suggestions"]
    assert remaining == []


async def test_dismiss_unknown_id_404(ctx) -> None:
    client, _, _ = ctx
    res = await client.post("/api/partner/suggestions/9999/dismiss")
    assert res.status_code == 404
