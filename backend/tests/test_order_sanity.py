"""Tests for src/services/order_sanity.py.

The sanity guard is the only thing between a typed-too-fast user and a
$0.91 buy on a 71¢-ask market. Every code path is covered.
"""

from __future__ import annotations

import pytest

from src.services.order_sanity import (
    LOUD_MIN_OFFSET_CENTS,
    SanityInput,
    Verdict,
    check_order,
)


def _input(**overrides) -> SanityInput:
    """Default to a sane buy: YES at 42¢, ask is 42¢, count 1."""
    base = dict(
        side="yes", action="buy", price_cents=42, count=1,
        yes_best_bid=41, yes_best_ask=42,
        no_best_bid=58, no_best_ask=59,
    )
    base.update(overrides)
    return SanityInput(**base)


class TestHardRefuse:
    def test_count_zero(self) -> None:
        r = check_order(_input(count=0))
        assert r.verdict == Verdict.HARD_REFUSE
        assert "Count" in r.reasons[0]

    def test_count_negative(self) -> None:
        assert check_order(_input(count=-5)).verdict == Verdict.HARD_REFUSE

    def test_price_zero(self) -> None:
        assert check_order(_input(price_cents=0)).verdict == Verdict.HARD_REFUSE

    def test_price_hundred(self) -> None:
        # Settlement-only; not valid for an order.
        assert check_order(_input(price_cents=100)).verdict == Verdict.HARD_REFUSE


class TestOk:
    def test_at_the_ask(self) -> None:
        """Bidding exactly at the ask is the canonical 'buy now'."""
        r = check_order(_input(price_cents=42))
        assert r.verdict == Verdict.OK
        assert r.reasons == []

    def test_below_the_ask(self) -> None:
        r = check_order(_input(price_cents=40))
        assert r.verdict == Verdict.OK

    def test_at_the_bid_on_sell(self) -> None:
        r = check_order(_input(action="sell", price_cents=41))
        assert r.verdict == Verdict.OK


class TestSoftWarn:
    def test_buy_one_above_ask_in_tight_market(self) -> None:
        """1¢-spread market, paying 1¢ over ask — borderline, not loud."""
        # spread = 42 - 41 = 1; excess = 43 - 42 = 1; soft_threshold = 1
        # excess > soft_threshold is False → still OK. Bump to 2 over.
        r = check_order(_input(price_cents=44, yes_best_bid=41, yes_best_ask=42))
        # excess = 2; spread = 1; loud_threshold = max(2, 10) = 10 → not loud
        # soft_threshold = 1; excess > 1 → soft warn
        assert r.verdict == Verdict.SOFT_WARN
        assert any("above the best ask" in r for r in r.reasons)

    def test_sell_below_bid_soft(self) -> None:
        r = check_order(_input(
            action="sell", price_cents=39, yes_best_bid=41, yes_best_ask=42,
        ))
        assert r.verdict == Verdict.SOFT_WARN

    def test_depth_warning_partial_fill(self) -> None:
        """Order wants 100, only 5 at the top — proceed but warn."""
        r = check_order(_input(
            price_cents=42, count=100,
            yes_best_bid=41, yes_best_ask=42, yes_top_qty=5,
        ))
        assert r.verdict == Verdict.SOFT_WARN
        assert any("fill at worse prices" in r for r in r.reasons)


class TestLoudConfirm:
    def test_dramatically_overbid(self) -> None:
        """The 91/71 scenario from the spec."""
        r = check_order(_input(
            price_cents=91, yes_best_bid=69, yes_best_ask=71,
        ))
        # excess = 91 - 71 = 20; spread = 2; loud_threshold = max(4, 10) = 10
        # 20 >= 10 → LOUD
        assert r.verdict == Verdict.LOUD_CONFIRM
        assert any("over market" in r for r in r.reasons)

    def test_tight_market_with_10c_over_still_loud(self) -> None:
        """Even on a 1¢-spread market the LOUD_MIN_OFFSET floor catches it."""
        r = check_order(_input(
            price_cents=52, yes_best_bid=41, yes_best_ask=42,
        ))
        # excess = 10; spread = 1; loud_threshold = max(2, 10) = 10
        # 10 >= 10 → LOUD
        assert r.verdict == Verdict.LOUD_CONFIRM
        assert r.reasons

    def test_loud_min_offset_is_ten(self) -> None:
        """Sanity-check the floor."""
        assert LOUD_MIN_OFFSET_CENTS == 10

    def test_dramatically_undersell(self) -> None:
        r = check_order(_input(
            action="sell", price_cents=20, yes_best_bid=41, yes_best_ask=42,
        ))
        assert r.verdict == Verdict.LOUD_CONFIRM


class TestEmptyBook:
    def test_no_orderbook_is_soft_warn(self) -> None:
        r = check_order(_input(
            yes_best_bid=None, yes_best_ask=None,
            no_best_bid=None, no_best_ask=None,
        ))
        assert r.verdict == Verdict.SOFT_WARN
        assert "No visible orderbook" in r.reasons[0]

    def test_one_sided_book_no_ask(self) -> None:
        """Buy when there's only a bid (no ask). Don't fire price warnings."""
        r = check_order(_input(yes_best_ask=None, yes_best_bid=41))
        assert r.verdict == Verdict.OK


class TestNoSide:
    def test_no_buy_uses_no_book(self) -> None:
        """Buying NO should look at the NO book, not the YES book."""
        r = check_order(_input(
            side="no", action="buy", price_cents=80,
            no_best_bid=57, no_best_ask=58,
        ))
        # excess = 80 - 58 = 22; spread = 1; loud_threshold = max(2, 10) = 10
        assert r.verdict == Verdict.LOUD_CONFIRM

    def test_no_sell_uses_no_bid(self) -> None:
        r = check_order(_input(
            side="no", action="sell", price_cents=40,
            no_best_bid=57, no_best_ask=58,
        ))
        # excess = 57 - 40 = 17 — well above loud threshold
        assert r.verdict == Verdict.LOUD_CONFIRM
