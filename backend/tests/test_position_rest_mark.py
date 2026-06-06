"""Tests for the REST mark fallback used when a position has no live WS book.

The trap this guards: an empty book reports the full 0↔100 boundary, whose
midpoint is a meaningless 50¢. The fallback must use last_price there, never a
fabricated 50¢, while still trusting a genuine one-sided quote's midpoint.
"""

from __future__ import annotations

from src.core.types import BetSide
from src.services.position_sync import _rest_mark_price_cents


def test_empty_book_uses_last_price_not_fabricated_midpoint() -> None:
    m = {
        "yes_bid_dollars": "0.0000", "yes_ask_dollars": "1.0000",
        "no_bid_dollars": "0.0000", "no_ask_dollars": "1.0000",
        "last_price_dollars": "0.2110",
    }
    # No real book → fall back to last trade (21¢), not (0+100)/2 = 50¢.
    assert _rest_mark_price_cents(m, BetSide.YES) == 21


def test_no_side_complements_last_price() -> None:
    m = {
        "yes_bid_dollars": "0.0000", "yes_ask_dollars": "1.0000",
        "no_bid_dollars": "0.0000", "no_ask_dollars": "1.0000",
        "last_price_dollars": "0.2000",
    }
    # last_price is on the YES scale; the NO holder's mark is its complement.
    assert _rest_mark_price_cents(m, BetSide.NO) == 80


def test_one_sided_real_quote_keeps_midpoint() -> None:
    m = {
        "yes_bid_dollars": "0.0000", "yes_ask_dollars": "0.0700",
        "no_bid_dollars": "0.9300", "no_ask_dollars": "1.0000",
        "last_price_dollars": "0.1640",
    }
    # Genuine quote on the YES side (ask 7¢): midpoint (0+7)/2 = 3.5 → 4¢.
    assert _rest_mark_price_cents(m, BetSide.YES) == 4
    # NO side has bid 93¢: midpoint (93+100)/2 = 96.5 → 96¢.
    assert _rest_mark_price_cents(m, BetSide.NO) == 96


def test_no_quotes_and_no_last_returns_none() -> None:
    m = {
        "yes_bid_dollars": "0.0000", "yes_ask_dollars": "1.0000",
        "no_bid_dollars": "0.0000", "no_ask_dollars": "1.0000",
        "last_price_dollars": "0.0000",
    }
    assert _rest_mark_price_cents(m, BetSide.YES) is None


def test_missing_dollar_fields_returns_none() -> None:
    assert _rest_mark_price_cents({}, BetSide.YES) is None
