"""Cancel-route guard: soccer-on-the-ticker, sourced from the live WS book.

The cancel policy changed from "must have a local BET row" (which 404'd the
user's own kalshi.com orders and the frontend swallowed it) to "any resting
order whose ticker is soccer, resolved from live_state.open_orders." These
tests pin the three guard outcomes without exercising the real Kalshi call.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes.orders import cancel_order
from src.kalshi.live_state import LiveState, OpenOrder

SOCCER = "KXWCGAME-26JUN11MEXRSA-MEX"
NON_SOCCER = "KXPRES-2028-DEM"


def _request_with_orders(*orders: OpenOrder) -> SimpleNamespace:
    ls = LiveState()
    for o in orders:
        ls.open_orders[o.order_id] = o
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(live_state=ls)))


def _order(order_id: str, ticker: str) -> OpenOrder:
    return OpenOrder(
        order_id=order_id, client_order_id=None, ticker=ticker,
        side="yes", status="resting", yes_price_cents=40, remaining_count=10,
    )


def _mock_kalshi_cancel(ticker: str) -> AsyncMock:
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.cancel_order = AsyncMock(return_value=SimpleNamespace(
        order=SimpleNamespace(order_id="o1", ticker=ticker, status="canceled"),
        reduced_by=10,
    ))
    return client


async def test_cancels_soccer_order_in_book(monkeypatch) -> None:
    """A soccer resting order in the live book cancels — no BET row required."""
    req = _request_with_orders(_order("o1", SOCCER))
    session = AsyncMock()
    client = _mock_kalshi_cancel(SOCCER)
    with patch("src.api.routes.orders.KalshiRestClient", return_value=client), \
         patch("src.api.routes.orders.mark_bet_terminal_by_order_id", new=AsyncMock(return_value=None)):
        out = await cancel_order("o1", req, session)  # type: ignore[arg-type]
    assert out["status"] == "canceled"
    client.cancel_order.assert_awaited_once_with("o1")


async def test_refuses_non_soccer_order(monkeypatch) -> None:
    """A non-soccer resting order is refused BEFORE any Kalshi call
    (cross-market isolation)."""
    req = _request_with_orders(_order("o2", NON_SOCCER))
    session = AsyncMock()
    client = _mock_kalshi_cancel(NON_SOCCER)
    with patch("src.api.routes.orders.KalshiRestClient", return_value=client):
        with pytest.raises(HTTPException) as ei:
            await cancel_order("o2", req, session)  # type: ignore[arg-type]
    assert ei.value.status_code == 400
    client.cancel_order.assert_not_awaited()  # never reached Kalshi


async def test_refuses_order_not_in_book() -> None:
    """An order_id not in the live book → 404, no Kalshi call (already filled
    or cancelled)."""
    req = _request_with_orders()  # empty book
    session = AsyncMock()
    with pytest.raises(HTTPException) as ei:
        await cancel_order("ghost", req, session)  # type: ignore[arg-type]
    assert ei.value.status_code == 404
