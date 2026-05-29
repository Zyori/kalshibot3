"""Tests for src/kalshi/ws_wire.py.

The wire-format parser is where bugs hurt most — a price misread by 1 decimal
place would corrupt the entire LiveState view of a market. These tests prove
that the dollar→cents conversion behaves correctly for every channel.
"""

from __future__ import annotations

import pytest

from src.kalshi.ws_wire import (
    Fill,
    MarketLifecycle,
    OrderbookDelta,
    OrderbookSnapshot,
    Subscribed,
    UserOrder,
    parse_kalshi_ws_message,
)


class TestOrderbookSnapshot:
    """V1 sends snapshots with `yes_dollars_fp` / `no_dollars_fp` keys whose
    values are arrays of [price_dollars_string, count_int] pairs."""

    def test_basic_snapshot(self) -> None:
        msg = parse_kalshi_ws_message({
            "type": "orderbook_snapshot",
            "sid": 7,
            "seq": 1,
            "msg": {
                "market_ticker": "KX-WC-1",
                "market_id": "1",
                "yes_dollars_fp": [["0.42", 100], ["0.41", 50]],
                "no_dollars_fp": [["0.58", 120]],
            },
        })
        assert isinstance(msg, OrderbookSnapshot)
        assert msg.msg.market_ticker == "KX-WC-1"
        assert msg.sid == 7 and msg.seq == 1
        assert len(msg.msg.yes) == 2
        assert msg.msg.yes[0].price_cents == 42
        assert msg.msg.yes[0].quantity == 100
        assert msg.msg.yes[1].price_cents == 41
        assert msg.msg.no[0].price_cents == 58

    def test_empty_side(self) -> None:
        msg = parse_kalshi_ws_message({
            "type": "orderbook_snapshot",
            "sid": 1, "seq": 1,
            "msg": {"market_ticker": "X", "market_id": "1", "yes_dollars_fp": [], "no_dollars_fp": []},
        })
        assert isinstance(msg, OrderbookSnapshot)
        assert msg.msg.yes == []
        assert msg.msg.no == []

    def test_alternative_key_names(self) -> None:
        """Some Kalshi responses use `yes`/`no` instead of `yes_dollars_fp`."""
        msg = parse_kalshi_ws_message({
            "type": "orderbook_snapshot",
            "sid": 1, "seq": 1,
            "msg": {"market_ticker": "X", "market_id": "1", "yes": [["0.5", 10]], "no": []},
        })
        assert isinstance(msg, OrderbookSnapshot)
        assert msg.msg.yes[0].price_cents == 50


class TestOrderbookDelta:
    def test_delta_with_dollar_strings(self) -> None:
        msg = parse_kalshi_ws_message({
            "type": "orderbook_delta",
            "sid": 3, "seq": 5,
            "msg": {
                "market_ticker": "KX-WC-1",
                "market_id": "1",
                "price_dollars": "0.43",
                "delta_fp": "25",
                "side": "yes",
            },
        })
        assert isinstance(msg, OrderbookDelta)
        assert msg.msg.price_cents == 43
        assert msg.msg.delta == 25
        assert msg.msg.side == "yes"

    def test_delta_negative_means_liquidity_removed(self) -> None:
        msg = parse_kalshi_ws_message({
            "type": "orderbook_delta",
            "sid": 1, "seq": 1,
            "msg": {"market_ticker": "X", "market_id": "1", "price_dollars": "0.5", "delta_fp": "-10", "side": "no"},
        })
        assert isinstance(msg, OrderbookDelta)
        assert msg.msg.delta == -10

    def test_delta_fp_fractional_value_preserved(self) -> None:
        """delta_fp is fixed-point and can be fractional — the parser must keep
        the fraction, not truncate it (truncation was the stale-book bug)."""
        msg = parse_kalshi_ws_message({
            "type": "orderbook_delta",
            "sid": 1, "seq": 1,
            "msg": {"market_ticker": "X", "market_id": "1", "price_dollars": "0.34", "delta_fp": "330.96", "side": "yes"},
        })
        assert isinstance(msg, OrderbookDelta)
        assert msg.msg.delta == 330.96

    def test_delta_fp_small_fraction_preserved(self) -> None:
        msg = parse_kalshi_ws_message({
            "type": "orderbook_delta",
            "sid": 1, "seq": 1,
            "msg": {"market_ticker": "X", "market_id": "1", "price_dollars": "0.34", "delta_fp": "-0.04", "side": "yes"},
        })
        assert isinstance(msg, OrderbookDelta)
        assert msg.msg.delta == -0.04


class TestFill:
    def test_fill_with_dollar_prices(self) -> None:
        msg = parse_kalshi_ws_message({
            "type": "fill",
            "sid": 4,
            "msg": {
                "trade_id": "t1",
                "order_id": "o1",
                "ticker": "KX-WC-1",
                "side": "yes",
                "action": "buy",
                "count": 5,
                "yes_price_dollars": "0.43",
                "no_price_dollars": "0.57",
                "is_taker": True,
            },
        })
        assert isinstance(msg, Fill)
        assert msg.msg.count == 5
        assert msg.msg.yes_price_cents == 43
        assert msg.msg.no_price_cents == 57
        assert msg.msg.is_taker is True

    def test_fill_with_int_prices(self) -> None:
        """Some Kalshi endpoints send yes_price/no_price as ints (cents)."""
        msg = parse_kalshi_ws_message({
            "type": "fill",
            "sid": 1,
            "msg": {
                "trade_id": "t1", "order_id": "o1",
                "ticker": "X", "side": "yes", "action": "buy",
                "count": 1,
                "yes_price": 42,
                "no_price": 58,
            },
        })
        assert isinstance(msg, Fill)
        assert msg.msg.yes_price_cents == 42
        assert msg.msg.no_price_cents == 58


class TestUserOrder:
    def test_user_order_resting(self) -> None:
        msg = parse_kalshi_ws_message({
            "type": "user_order",
            "sid": 5,
            "msg": {
                "order_id": "o1",
                "client_order_id": "c1",
                "ticker": "KX-WC-1",
                "side": "yes",
                "status": "resting",
                "yes_price_dollars": "0.42",
                "remaining_count_fp": "10",
            },
        })
        assert isinstance(msg, UserOrder)
        assert msg.msg.status == "resting"
        assert msg.msg.yes_price_cents == 42
        assert msg.msg.remaining_count == 10

    def test_user_order_executed_no_remaining(self) -> None:
        msg = parse_kalshi_ws_message({
            "type": "user_order",
            "sid": 1,
            "msg": {
                "order_id": "o1", "ticker": "X", "side": "yes",
                "status": "executed",
                "remaining_count_fp": "0",
                "yes_price_dollars": "0.42",
            },
        })
        assert isinstance(msg, UserOrder)
        assert msg.msg.status == "executed"
        assert msg.msg.remaining_count == 0


class TestMarketLifecycle:
    def test_lifecycle_settled(self) -> None:
        msg = parse_kalshi_ws_message({
            "type": "market_lifecycle",
            "sid": 9,
            "msg": {"market_ticker": "KX-WC-1", "status": "settled", "settlement_value": 100},
        })
        assert isinstance(msg, MarketLifecycle)
        assert msg.msg.status == "settled"
        assert msg.msg.settlement_value == 100


class TestUnknownTypes:
    def test_unknown_returns_none(self) -> None:
        assert parse_kalshi_ws_message({"type": "trade", "msg": {}}) is None
        assert parse_kalshi_ws_message({"type": "heartbeat"}) is None

    def test_no_type_returns_none(self) -> None:
        assert parse_kalshi_ws_message({}) is None


class TestSubscribed:
    def test_subscribed_ack(self) -> None:
        msg = parse_kalshi_ws_message({
            "type": "subscribed",
            "id": 123,
            "msg": {"sid": 7, "channel": "orderbook_delta"},
        })
        assert isinstance(msg, Subscribed)
        assert msg.id == 123
        assert msg.msg.sid == 7
        assert msg.msg.channel == "orderbook_delta"


@pytest.mark.parametrize(
    "dollar_str,expected_cents",
    [
        ("0.01", 1),
        ("0.42", 42),
        ("0.50", 50),
        ("0.99", 99),
    ],
)
def test_dollar_string_conversion(dollar_str: str, expected_cents: int) -> None:
    """Round-trip every price the parser is likely to see in production.

    Kalshi orderbook_delta prices live in the 1–99¢ contract-price range.
    Sub-cent and 100¢ settlement values come through different message types
    (market_lifecycle.settlement_value) and have separate validation.
    """
    msg = parse_kalshi_ws_message({
        "type": "orderbook_delta",
        "sid": 1, "seq": 1,
        "msg": {
            "market_ticker": "X", "market_id": "1",
            "price_dollars": dollar_str, "delta_fp": "1", "side": "yes",
        },
    })
    assert isinstance(msg, OrderbookDelta)
    assert msg.msg.price_cents == expected_cents
