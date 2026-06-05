"""Tests for src/kalshi/schemas.py — wire-format validation and money conversion."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.kalshi.schemas import (
    Market,
    PlaceOrderRequest,
    Quote,
    Settlement,
    SettlementsResponse,
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


class TestSettlementResult:
    """market_result must tolerate 'scalar'. Kalshi emits it rarely for some
    exotic props (NOT for any market this app trades — every settled soccer and
    combo market resolves yes/no, verified across full history). A too-strict
    Literal used to fail validation for the whole settlements page, killing the
    settlement sweep for every ticker in the batch."""

    def test_scalar_result_accepted(self) -> None:
        s = Settlement(ticker="KXMVE-1", market_result="scalar")
        assert s.market_result == "scalar"
        # scalar has no clean YES payoff — the sweeper skips on None and retries.
        assert s.settlement_value_cents is None

    @pytest.mark.parametrize(
        "result,expected",
        [("yes", 100), ("no", 0), ("scalar", None), ("", None)],
    )
    def test_settlement_value_mapping(self, result: str, expected: int | None) -> None:
        assert Settlement(ticker="T", market_result=result).settlement_value_cents == expected

    def test_one_scalar_row_does_not_drop_the_page(self) -> None:
        """The exact failure mode found in production: a settlements page with
        a scalar row mixed among binary rows must validate as a whole."""
        resp = SettlementsResponse.model_validate(
            {"settlements": [
                {"ticker": "A", "market_result": "yes", "revenue": 100},
                {"ticker": "B", "market_result": "scalar", "revenue": 0},
                {"ticker": "C", "market_result": "no", "revenue": 0},
            ]}
        )
        assert len(resp.settlements) == 3
        assert [s.settlement_value_cents for s in resp.settlements] == [100, None, 0]


class TestQuoteWire:
    """RFQ quote wire shape — dollar strings → int cents, fractional contracts
    floored. Verified against live quotes on the account."""

    def test_quote_converts_dollars_and_contracts(self) -> None:
        q = Quote.model_validate({
            "id": "q1", "rfq_id": "r1",
            "market_ticker": "KXMVESPORTSMULTIGAMEEXTENDED-S...",
            "status": "open",
            "yes_bid_dollars": "0.6300", "no_bid_dollars": "0.2900",
            "yes_contracts_fp": "10.00", "no_contracts_fp": "10.00",
        })
        assert q.yes_bid_cents == 63
        assert q.no_bid_cents == 29
        assert q.yes_contracts == 10
        assert q.no_contracts == 10

    def test_quote_one_sided(self) -> None:
        # A maker may quote only one side; the other bid is "0.0000".
        q = Quote.model_validate({
            "id": "q2", "rfq_id": "r1", "market_ticker": "X", "status": "open",
            "yes_bid_dollars": "0.3900", "no_bid_dollars": "0.0000",
            "yes_contracts_fp": "10.00", "no_contracts_fp": "0.00",
        })
        assert q.yes_bid_cents == 39
        assert q.no_bid_cents == 0
        assert q.no_contracts == 0

    def test_quote_null_side_does_not_crash(self) -> None:
        # The real failure mode: the unquoted side arrives as null (not "0.00").
        # float(None) used to throw and 500 the whole quotes page.
        q = Quote.model_validate({
            "id": "q3", "rfq_id": "r1", "market_ticker": "X", "status": "open",
            "yes_bid_dollars": "0.4500", "no_bid_dollars": None,
            "yes_contracts_fp": "10.00", "no_contracts_fp": None,
        })
        assert q.yes_bid_cents == 45
        assert q.no_bid_cents == 0
        assert q.no_contracts == 0
