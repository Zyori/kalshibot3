"""Post-only rejection maps to an actionable 422, not a scary 502.

post_only means maker-only: if the limit price would cross the spread, Kalshi
rejects it. That's the guard working, not a server error — so place_order
returns a 422 with a human reason, distinct from the generic KalshiError 502.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes.orders import OrderRequestBody, place_order
from src.core.exceptions import PostOnlyRejected, MarketHalted

SOCCER = "KXWCGAME-26JUN11MEXRSA-MEX"


def _request_with_book() -> SimpleNamespace:
    # No book in state → sanity check treats it as soft-warn, order proceeds to
    # the Kalshi call (which we mock to raise). app.state has no broadcast/etc.
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))


def _body(post_only: bool = True) -> OrderRequestBody:
    return OrderRequestBody(
        ticker=SOCCER, side="yes", action="buy", count=10,
        price_cents=40, post_only=post_only,
    )


def _mock_client_raising(exc: Exception) -> AsyncMock:
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.place_order = AsyncMock(side_effect=exc)
    return client


async def test_post_only_rejection_is_422_with_reason() -> None:
    client = _mock_client_raising(PostOnlyRejected("post-only rejected: would cross", status=400))
    session = AsyncMock()
    with patch("src.api.routes.orders.KalshiRestClient", return_value=client):
        with pytest.raises(HTTPException) as ei:
            await place_order(_body(post_only=True), _request_with_book(), session)  # type: ignore[arg-type]
    assert ei.value.status_code == 422
    reasons = ei.value.detail["reasons"]
    assert any("post-only" in r.lower() and "cross" in r.lower() for r in reasons)


async def test_other_kalshi_error_still_502() -> None:
    """A genuine Kalshi failure (e.g. market halted) stays a 502 — only
    post-only gets the special 422 treatment."""
    client = _mock_client_raising(MarketHalted("market halted", status=400))
    session = AsyncMock()
    with patch("src.api.routes.orders.KalshiRestClient", return_value=client):
        with pytest.raises(HTTPException) as ei:
            await place_order(_body(post_only=False), _request_with_book(), session)  # type: ignore[arg-type]
    assert ei.value.status_code == 502
