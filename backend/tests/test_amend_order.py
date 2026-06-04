"""Amend route: server-authoritative order lookup, soccer guard, BET-row swap.

The route reads the order's ticker/side/action from Kalshi's /portfolio/orders
(not the client, not the WS cache), gates on soccer, amends, then re-points the
BET row from the old order_id to the new one. These cover the guard outcomes
and the reprice without hitting the real Kalshi endpoint.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes.orders import AmendBody, amend_order

SOCCER = "KXWCGAME-26JUN11MEXRSA-MEX"
NON_SOCCER = "KXPRES-2028-DEM"


def _request() -> SimpleNamespace:
    # No supervisor/book → _book_snapshot returns all-None (no liquidity context;
    # the HARD-REFUSE tier doesn't need it). app.state is otherwise empty.
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))


def _mock_client(*, resting_order: dict | None, new_order_id: str = "new123") -> AsyncMock:
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    orders = [resting_order] if resting_order is not None else []
    client.get_orders = AsyncMock(return_value={"orders": orders})
    client.amend_order = AsyncMock(return_value=SimpleNamespace(
        order=SimpleNamespace(order_id=new_order_id, status="resting"),
        old_order=SimpleNamespace(order_id="old", status="canceled"),
    ))
    return client


def _resting(order_id: str, ticker: str, side: str = "yes", action: str = "buy") -> dict:
    return {"order_id": order_id, "ticker": ticker, "side": side, "action": action,
            "status": "resting"}


def _session(*, bet_id: int | None = 1, fills: int = 0) -> AsyncMock:
    """A session whose scalar() answers the amend route's two pre-check queries
    in order: (1) the BET id for this order, (2) the fill count for that bet."""
    session = AsyncMock()
    session.scalar = AsyncMock(side_effect=[bet_id, fills])
    return session


async def test_amend_soccer_order_swaps_bet_id() -> None:
    client = _mock_client(resting_order=_resting("old", SOCCER), new_order_id="new123")
    session = _session()
    reprice = AsyncMock(return_value=None)
    with patch("src.api.routes.orders.KalshiRestClient", return_value=client), \
         patch("src.api.routes.orders.reprice_bet_for_amend", new=reprice):
        out = await amend_order("old", AmendBody(price_cents=45, count=8), _request(), session)  # type: ignore[arg-type]
    assert out["kalshi_order_id"] == "new123"
    assert out["old_order_id"] == "old"
    assert out["price_cents"] == 45 and out["count"] == 8
    client.amend_order.assert_awaited_once()
    # the BET row reprice was called old→new with the new shape
    _, kw = reprice.await_args
    assert kw["old_order_id"] == "old" and kw["new_order_id"] == "new123"
    assert kw["new_price_cents"] == 45 and kw["new_count"] == 8


async def test_amend_passes_authoritative_side_action_to_kalshi() -> None:
    """The amend request to Kalshi carries the side/action READ FROM KALSHI,
    not anything the client supplied (which is only price+count)."""
    client = _mock_client(resting_order=_resting("o", SOCCER, side="no", action="sell"))
    session = _session()
    with patch("src.api.routes.orders.KalshiRestClient", return_value=client), \
         patch("src.api.routes.orders.reprice_bet_for_amend", new=AsyncMock(return_value=None)):
        await amend_order("o", AmendBody(price_cents=30, count=5), _request(), session)  # type: ignore[arg-type]
    sent_req = client.amend_order.await_args.args[1]
    assert sent_req.side == "no" and sent_req.action == "sell"
    assert sent_req.no_price == 30 and sent_req.yes_price is None  # NO side → no_price set


async def test_amend_non_soccer_refused() -> None:
    client = _mock_client(resting_order=_resting("o2", NON_SOCCER))
    session = _session()
    with patch("src.api.routes.orders.KalshiRestClient", return_value=client):
        with pytest.raises(HTTPException) as ei:
            await amend_order("o2", AmendBody(price_cents=45, count=8), _request(), session)  # type: ignore[arg-type]
    assert ei.value.status_code == 400
    client.amend_order.assert_not_awaited()


async def test_amend_order_not_resting() -> None:
    client = _mock_client(resting_order=None)  # not in the resting list
    session = _session()
    with patch("src.api.routes.orders.KalshiRestClient", return_value=client):
        with pytest.raises(HTTPException) as ei:
            await amend_order("ghost", AmendBody(price_cents=45, count=8), _request(), session)  # type: ignore[arg-type]
    assert ei.value.status_code == 404
    client.amend_order.assert_not_awaited()


async def test_amend_refused_when_partially_filled() -> None:
    """A resting order that already has fills is refused (409) BEFORE the Kalshi
    amend call — amending would clobber the filled cost basis."""
    client = _mock_client(resting_order=_resting("o", SOCCER))
    session = _session(bet_id=7, fills=3)  # 3 fills on this order's bet
    with patch("src.api.routes.orders.KalshiRestClient", return_value=client):
        with pytest.raises(HTTPException) as ei:
            await amend_order("o", AmendBody(price_cents=45, count=8), _request(), session)  # type: ignore[arg-type]
    assert ei.value.status_code == 409
    assert "fill" in str(ei.value.detail).lower()
    client.amend_order.assert_not_awaited()  # never touched Kalshi


async def test_amend_refused_when_kalshi_shows_partial_fill() -> None:
    """The TOCTOU guard: Kalshi's own counts show the order has filled
    (remaining < initial) even if no local BetFill row exists yet. Refused (409)
    before touching Kalshi, so amend can't overwrite the filled cost basis.

    Counts seeded as the float STRINGS Kalshi actually sends ("10.00"), not
    ints — a raw `<` on the strings compares lexicographically and silently
    misses the fill, which is the bug this guard was failing to catch."""
    order = _resting("o", SOCCER)
    order.update({"initial_count_fp": "10.00", "remaining_count_fp": "4.00"})  # 60% filled
    client = _mock_client(resting_order=order)
    session = _session(bet_id=None, fills=0)  # nothing recorded locally yet
    with patch("src.api.routes.orders.KalshiRestClient", return_value=client):
        with pytest.raises(HTTPException) as ei:
            await amend_order("o", AmendBody(price_cents=45, count=8), _request(), session)  # type: ignore[arg-type]
    assert ei.value.status_code == 409
    assert "partially filled" in str(ei.value.detail).lower()
    client.amend_order.assert_not_awaited()


async def test_amend_partial_fill_guard_at_digit_width_boundary() -> None:
    """Regression: "4.00" < "10.00" is True numerically but False as strings
    (lexicographic, since '4' > '1'). The guard must fire across a digit-width
    boundary — the exact case a lexicographic compare let through."""
    order = _resting("o", SOCCER)
    order.update({"initial_count_fp": "10.00", "remaining_count_fp": "4.00"})
    client = _mock_client(resting_order=order)
    session = _session(bet_id=None, fills=0)
    with patch("src.api.routes.orders.KalshiRestClient", return_value=client):
        with pytest.raises(HTTPException) as ei:
            await amend_order("o", AmendBody(price_cents=45, count=8), _request(), session)  # type: ignore[arg-type]
    assert ei.value.status_code == 409
    client.amend_order.assert_not_awaited()


async def test_amend_holds_ledger_lock_when_supervisor_present() -> None:
    """When a supervisor exists, the amend serializes its Kalshi-amend + reprice
    under the ledger write lock (so a racing WS cancel can't interleave)."""
    import asyncio

    client = _mock_client(resting_order=_resting("old", SOCCER), new_order_id="new123")
    session = _session()
    lock = asyncio.Lock()
    # Supervisor stub needs live_state.books (the sanity-check _book_snapshot
    # reads it) plus the lock. Empty book → all-None snapshot, sanity passes.
    supervisor = SimpleNamespace(
        _ledger_write_lock=lock,
        live_state=SimpleNamespace(books={}),
    )
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(supervisor=supervisor)))
    with patch("src.api.routes.orders.KalshiRestClient", return_value=client), \
         patch("src.api.routes.orders.reprice_bet_for_amend", new=AsyncMock(return_value=None)):
        out = await amend_order("old", AmendBody(price_cents=45, count=8), req, session)  # type: ignore[arg-type]
    assert out["kalshi_order_id"] == "new123"
    assert not lock.locked()  # released in finally


def test_amend_body_rejects_bad_inputs() -> None:
    with pytest.raises(Exception):
        AmendBody(price_cents=45, count=0)   # ge=1
    with pytest.raises(Exception):
        AmendBody(price_cents=0, count=5)    # ge=1
    with pytest.raises(Exception):
        AmendBody(price_cents=100, count=5)  # le=99
