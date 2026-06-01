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


async def test_amend_soccer_order_swaps_bet_id() -> None:
    client = _mock_client(resting_order=_resting("old", SOCCER), new_order_id="new123")
    session = AsyncMock()
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
    session = AsyncMock()
    with patch("src.api.routes.orders.KalshiRestClient", return_value=client), \
         patch("src.api.routes.orders.reprice_bet_for_amend", new=AsyncMock(return_value=None)):
        await amend_order("o", AmendBody(price_cents=30, count=5), _request(), session)  # type: ignore[arg-type]
    sent_req = client.amend_order.await_args.args[1]
    assert sent_req.side == "no" and sent_req.action == "sell"
    assert sent_req.no_price == 30 and sent_req.yes_price is None  # NO side → no_price set


async def test_amend_non_soccer_refused() -> None:
    client = _mock_client(resting_order=_resting("o2", NON_SOCCER))
    session = AsyncMock()
    with patch("src.api.routes.orders.KalshiRestClient", return_value=client):
        with pytest.raises(HTTPException) as ei:
            await amend_order("o2", AmendBody(price_cents=45, count=8), _request(), session)  # type: ignore[arg-type]
    assert ei.value.status_code == 400
    client.amend_order.assert_not_awaited()


async def test_amend_order_not_resting() -> None:
    client = _mock_client(resting_order=None)  # not in the resting list
    session = AsyncMock()
    with patch("src.api.routes.orders.KalshiRestClient", return_value=client):
        with pytest.raises(HTTPException) as ei:
            await amend_order("ghost", AmendBody(price_cents=45, count=8), _request(), session)  # type: ignore[arg-type]
    assert ei.value.status_code == 404
    client.amend_order.assert_not_awaited()


def test_amend_body_rejects_bad_inputs() -> None:
    with pytest.raises(Exception):
        AmendBody(price_cents=45, count=0)   # ge=1
    with pytest.raises(Exception):
        AmendBody(price_cents=0, count=5)    # ge=1
    with pytest.raises(Exception):
        AmendBody(price_cents=100, count=5)  # le=99
