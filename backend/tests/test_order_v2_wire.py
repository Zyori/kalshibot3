"""V2 order-wire mapping — the money-critical translation from our internal
yes/no + buy/sell + integer-cent model to Kalshi's single YES-book bid/ask +
fixed-point dollar price.

The exhaustive four-row table (buy/sell × yes/no) is the safety net against the
single most dangerous bug in the order path: inverting the side or price so an
order places the OPPOSITE of what the user intended. A NO order is the trap —
both the side and the price flip relative to its held frame.
"""

from __future__ import annotations

import pytest

from src.core.types import cents_to_dollars_str
from src.kalshi.schemas import (
    AmendOrderRequest,
    PlaceOrderRequest,
    V2OrderAck,
    synthesize_order_from_ack,
)


class TestCentsToDollarsStr:
    @pytest.mark.parametrize(
        "cents,expected",
        [(1, "0.0100"), (42, "0.4200"), (56, "0.5600"), (99, "0.9900"), (100, "1.0000")],
    )
    def test_grid(self, cents: int, expected: str) -> None:
        assert cents_to_dollars_str(cents) == expected

    def test_inverse_of_dollars_str_to_cents(self) -> None:
        from src.core.types import dollars_str_to_cents
        for c in range(1, 100):
            assert dollars_str_to_cents(cents_to_dollars_str(c)) == c


class TestPlaceOrderV2Mapping:
    """The four-row truth table. side ∈ {bid,ask}, price is always the YES-leg
    price; a NO held price becomes its 100-complement."""

    @pytest.mark.parametrize(
        "side,action,held_price,want_side,want_price",
        [
            ("yes", "buy",  56, "bid", "0.5600"),  # buy YES  → bid, yes price
            ("yes", "sell", 56, "ask", "0.5600"),  # sell YES → ask, yes price
            ("no",  "buy",  44, "ask", "0.5600"),  # buy NO   → ask, 100−44 = 56
            ("no",  "sell", 44, "bid", "0.5600"),  # sell NO  → bid, 100−44 = 56
            # Boundaries: the NO complement is the error-prone arithmetic.
            ("no",  "buy",  99, "ask", "0.0100"),  # buy NO @99 → sell YES @1
            ("no",  "sell",  1, "bid", "0.9900"),  # sell NO @1 → buy YES @99
            ("yes", "buy",   1, "bid", "0.0100"),
            ("yes", "buy",  99, "bid", "0.9900"),
        ],
    )
    def test_four_rows(
        self, side: str, action: str, held_price: int,
        want_side: str, want_price: str,
    ) -> None:
        req = PlaceOrderRequest(
            ticker="KXWCGAME-26JUN19USAAUS-USA",
            side=side, action=action, count=3,
            yes_price=held_price if side == "yes" else None,
            no_price=held_price if side == "no" else None,
            client_order_id="cid-1",
        )
        wire = req.to_v2_wire()
        assert wire["side"] == want_side
        assert wire["price"] == want_price
        assert wire["count"] == "3.00"
        assert wire["time_in_force"] == "good_till_canceled"
        assert wire["self_trade_prevention_type"] == "taker_at_cross"
        assert wire["client_order_id"] == "cid-1"

    def test_expiration_time_passed_through(self) -> None:
        req = PlaceOrderRequest(
            ticker="X", side="yes", action="buy", count=1,
            yes_price=50, client_order_id="c", expiration_ts=1781000000,
        )
        assert req.to_v2_wire()["expiration_time"] == 1781000000

    def test_no_expiration_omits_field(self) -> None:
        req = PlaceOrderRequest(
            ticker="X", side="yes", action="buy", count=1,
            yes_price=50, client_order_id="c",
        )
        assert "expiration_time" not in req.to_v2_wire()


class TestAmendOrderV2Mapping:
    @pytest.mark.parametrize(
        "side,action,held_price,want_side,want_price",
        [
            ("yes", "buy",  56, "bid", "0.5600"),
            ("yes", "sell", 56, "ask", "0.5600"),
            ("no",  "buy",  44, "ask", "0.5600"),
            ("no",  "sell", 44, "bid", "0.5600"),
        ],
    )
    def test_four_rows(
        self, side: str, action: str, held_price: int,
        want_side: str, want_price: str,
    ) -> None:
        req = AmendOrderRequest(
            ticker="X", side=side, action=action, count=7,
            yes_price=held_price if side == "yes" else None,
            no_price=held_price if side == "no" else None,
            updated_client_order_id="ucid",
        )
        wire = req.to_v2_wire()
        assert wire["side"] == want_side
        assert wire["price"] == want_price
        assert wire["count"] == "7.00"
        assert wire["updated_client_order_id"] == "ucid"


class TestSynthesizeOrderFromAck:
    """The V2 ack drops side/price/ticker/status — synthesize_order_from_ack
    reconstructs our canonical Order from the request fields we already hold, so
    downstream consumers (record_placed_order, the amend route) see the same
    Order shape as before V2."""

    def test_resting_order_round_trips_request_fields(self) -> None:
        ack = V2OrderAck(order_id="ord-1", fill_count="0.00", remaining_count="3.00")
        order = synthesize_order_from_ack(
            ack=ack, ticker="KX-USA", side="no", action="buy",
            held_price_cents=44, count=3, client_order_id="cid",
        )
        assert order.order_id == "ord-1"
        assert order.side == "no"
        assert order.action == "buy"
        assert order.no_price == 44       # held frame preserved (NOT the YES 56)
        assert order.yes_price is None
        assert order.count == 3
        assert order.remaining_count == 3
        assert order.status == "resting"

    def test_fully_filled_is_executed(self) -> None:
        ack = V2OrderAck(order_id="o", fill_count="5.00", remaining_count="0.00")
        order = synthesize_order_from_ack(
            ack=ack, ticker="X", side="yes", action="buy",
            held_price_cents=60, count=5, client_order_id="c",
        )
        assert order.status == "executed"
        assert order.yes_price == 60

    def test_missing_remaining_count_falls_back_to_total_minus_filled(self) -> None:
        # V2 may omit remaining_count when the order rests untouched.
        ack = V2OrderAck(order_id="o", fill_count="2.00")
        order = synthesize_order_from_ack(
            ack=ack, ticker="X", side="yes", action="buy",
            held_price_cents=60, count=10, client_order_id="c",
        )
        assert order.remaining_count == 8
        assert order.status == "resting"

    def test_stp_cancel_is_canceled_not_resting(self) -> None:
        # self_trade_prevention (taker_at_cross) kills an order that would cross
        # our own resting order: the ack 201s with fill=0 AND remaining=0. This
        # MUST be 'canceled' — labeling it 'resting' makes record_placed_order
        # book a phantom OPEN bet for a position that doesn't exist.
        ack = V2OrderAck(order_id="o", fill_count="0.00", remaining_count="0.00")
        order = synthesize_order_from_ack(
            ack=ack, ticker="X", side="no", action="buy",
            held_price_cents=44, count=5, client_order_id="c",
        )
        assert order.status == "canceled"
        assert order.remaining_count == 0

    def test_both_counts_absent_is_resting(self) -> None:
        # Both fields omitted (order rests untouched, nothing filled): fall back
        # to remaining = count, status resting. Distinct from the STP-cancel case
        # where remaining is explicitly 0.
        ack = V2OrderAck(order_id="o")
        order = synthesize_order_from_ack(
            ack=ack, ticker="X", side="yes", action="buy",
            held_price_cents=60, count=4, client_order_id="c",
        )
        assert order.remaining_count == 4
        assert order.status == "resting"
