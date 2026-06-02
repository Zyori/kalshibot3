"""Cancel-route guard: soccer-on-the-ticker, resolved from Kalshi (authoritative).

Cancel reads the resting order from Kalshi's /portfolio/orders (not the WS
cache, which has no snapshot of pre-session orders), gates on soccer, then
cancels — the same authoritative pattern amend uses. These pin the three guard
outcomes without exercising the real cancel call.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes.orders import cancel_order

SOCCER = "KXWCGAME-26JUN11MEXRSA-MEX"
NON_SOCCER = "KXPRES-2028-DEM"


def _request() -> SimpleNamespace:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))


def _resting(order_id: str, ticker: str) -> dict:
    return {"order_id": order_id, "ticker": ticker, "side": "yes", "action": "buy",
            "status": "resting"}


def _mock_client(*, resting_order: dict | None) -> AsyncMock:
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get_orders = AsyncMock(
        return_value={"orders": [resting_order] if resting_order else []}
    )
    client.cancel_order = AsyncMock(return_value=SimpleNamespace(
        order=SimpleNamespace(order_id="o1", ticker=SOCCER, status="canceled"),
        reduced_by=10,
    ))
    return client


async def test_cancels_soccer_order_from_kalshi() -> None:
    """A soccer resting order Kalshi reports cancels — no local BET row or WS
    cache entry required (fixes kalshi.com / pre-restart orders)."""
    client = _mock_client(resting_order=_resting("o1", SOCCER))
    session = AsyncMock()
    with patch("src.api.routes.orders.KalshiRestClient", return_value=client), \
         patch("src.api.routes.orders.mark_bet_terminal_by_order_id", new=AsyncMock(return_value=None)):
        out = await cancel_order("o1", _request(), session)  # type: ignore[arg-type]
    assert out["status"] == "canceled"
    client.cancel_order.assert_awaited_once_with("o1")


async def test_refuses_non_soccer_order() -> None:
    """A non-soccer resting order is refused BEFORE the Kalshi cancel call."""
    client = _mock_client(resting_order=_resting("o2", NON_SOCCER))
    session = AsyncMock()
    with patch("src.api.routes.orders.KalshiRestClient", return_value=client):
        with pytest.raises(HTTPException) as ei:
            await cancel_order("o2", _request(), session)  # type: ignore[arg-type]
    assert ei.value.status_code == 400
    client.cancel_order.assert_not_awaited()


async def test_refuses_order_not_resting() -> None:
    """An order_id Kalshi doesn't list as resting → 404, no cancel call."""
    client = _mock_client(resting_order=None)
    session = AsyncMock()
    with patch("src.api.routes.orders.KalshiRestClient", return_value=client):
        with pytest.raises(HTTPException) as ei:
            await cancel_order("ghost", _request(), session)  # type: ignore[arg-type]
    assert ei.value.status_code == 404
    client.cancel_order.assert_not_awaited()
