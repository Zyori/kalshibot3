"""Tests for src/kalshi/schemas.py — wire-format validation and money conversion."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.kalshi.schemas import (
    Market,
    PlaceOrderRequest,
    cents_to_dollars,
    dollars_to_cents,
)


class TestCentsConversion:
    """Round-trip and edge cases. This is the only place dollars touch cents."""

    @pytest.mark.parametrize(
        "dollars,cents",
        [
            (0, 0),
            (1, 100),
            (1.23, 123),
            (0.01, 1),
            (99.99, 9999),
            (1234.56, 123456),
        ],
    )
    def test_dollars_to_cents(self, dollars: float, cents: int) -> None:
        assert dollars_to_cents(dollars) == cents

    def test_cents_to_dollars(self) -> None:
        assert cents_to_dollars(123) == 1.23
        assert cents_to_dollars(0) == 0.0
        assert cents_to_dollars(9999) == 99.99

    def test_round_trip_integer_dollars(self) -> None:
        for d in range(0, 1000, 17):
            assert cents_to_dollars(dollars_to_cents(d)) == float(d)


class TestPriceRangeEnforcement:
    """Kalshi binary contracts are 1–99 cents. Schemas must reject out-of-range
    values — that's our defense-in-depth against bad data corrupting the DB
    where the same range is enforced by CHECK constraints."""

    def test_market_yes_bid_out_of_range_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Market(
                ticker="X", title="t", status="active",
                yes_bid=101,  # invalid
            )

    def test_market_yes_bid_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Market(
                ticker="X", title="t", status="active",
                yes_bid=-1,  # invalid
            )

    def test_market_accepts_none_for_illiquid(self) -> None:
        m = Market(ticker="X", title="t", status="active")
        assert m.yes_bid is None
        assert m.no_ask is None

    def test_place_order_zero_price_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PlaceOrderRequest(
                ticker="X", side="yes", action="buy",
                count=1, yes_price=0, client_order_id="cid",  # invalid
            )

    def test_place_order_count_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            PlaceOrderRequest(
                ticker="X", side="yes", action="buy",
                count=0, yes_price=42, client_order_id="cid",  # invalid count
            )

    def test_place_order_happy_path(self) -> None:
        req = PlaceOrderRequest(
            ticker="KX-WC-1", side="yes", action="buy",
            count=5, yes_price=42, client_order_id="cid-1",
        )
        assert req.type == "limit"  # default
        assert req.post_only is False  # default
