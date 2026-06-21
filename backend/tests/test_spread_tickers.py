"""World Cup goal-spread (handicap) ticker classification + labels.

Mirrors the totals coverage. The classifier gates the import (a misfire silently
drops the position) and feeds cross-market isolation (a spread must pass
is_soccer_ticker or its order/cancel would be refused), so both are tested.
"""

from __future__ import annotations

import pytest

from src.sports.soccer import (
    is_per_game_soccer_ticker,
    is_soccer_ticker,
    is_spread_ticker,
    is_total_goals_ticker,
    spread_favorite_code,
    spread_label,
    spread_line,
)
from src.sports.tradeable import is_tradeable_ticker

SPREAD = "KXWCSPREAD-26JUN19USAAUS-USA2"
SPREAD_AWAY = "KXWCSPREAD-26JUN19USAAUS-AUS3"
MONEYLINE = "KXWCGAME-26JUN19USAAUS-USA"
TOTALS = "KXWCTOTAL-26JUN11MEXRSA-2"
POLITICS = "KXPRES-2028-DJT"


class TestSpreadClassifier:
    def test_spread_ticker_is_spread(self) -> None:
        assert is_spread_ticker(SPREAD)
        assert is_spread_ticker(SPREAD_AWAY)

    def test_two_digit_slot(self) -> None:
        assert is_spread_ticker("KXWCSPREAD-26JUN19USAAUS-USA10")

    def test_moneyline_is_not_spread(self) -> None:
        assert not is_spread_ticker(MONEYLINE)

    def test_totals_is_not_spread(self) -> None:
        assert not is_spread_ticker(TOTALS)

    def test_foreign_prefix_is_not_spread(self) -> None:
        # A spread-shaped suffix on a non-WC series must NOT classify — the prefix
        # tuple is KXWCSPREAD only (cross-market isolation).
        assert not is_spread_ticker("KXNFLSPREAD-26JUN19KCBUF-KC2")
        assert not is_spread_ticker(POLITICS)

    def test_spread_is_not_totals(self) -> None:
        assert not is_total_goals_ticker(SPREAD)


class TestSpreadIsolation:
    """A spread must pass the soccer/tradeable gates or its order, cancel, and
    settlement would all be refused as 'not a market we manage'."""

    def test_spread_is_soccer(self) -> None:
        assert is_soccer_ticker(SPREAD)

    def test_spread_is_tradeable(self) -> None:
        assert is_tradeable_ticker(SPREAD)

    def test_spread_is_per_game(self) -> None:
        assert is_per_game_soccer_ticker(SPREAD)

    def test_politics_still_excluded(self) -> None:
        assert not is_soccer_ticker(POLITICS)
        assert not is_tradeable_ticker(POLITICS)


class TestSpreadLineAndFavorite:
    def test_favorite_code(self) -> None:
        assert spread_favorite_code(SPREAD) == "USA"
        assert spread_favorite_code(SPREAD_AWAY) == "AUS"

    def test_favorite_code_none_for_non_spread(self) -> None:
        assert spread_favorite_code(MONEYLINE) is None

    @pytest.mark.parametrize(
        "title,expected",
        [
            ("USA wins by more than 1.5 goals", 1.5),
            ("USA wins by more than 1.5 goals?", 1.5),  # stored title has a '?'
            ("Scotland wins by more than 2.5 goals", 2.5),
            (None, None),
            ("not a spread title", None),
        ],
    )
    def test_line_parse(self, title: str | None, expected: float | None) -> None:
        assert spread_line(title) == expected


class TestSpreadLabel:
    def test_yes_covers_minus_sign(self) -> None:
        # YES hold backs the favorite to cover → minus sign.
        label = spread_label(SPREAD, "USA wins by more than 1.5 goals", "USA vs Australia", negate=False)
        assert label == "USA - Australia — USA -1.5"

    def test_no_fades_plus_sign(self) -> None:
        # NO hold fades the favorite → plus sign, same line.
        label = spread_label(SPREAD, "USA wins by more than 1.5 goals", "USA vs Australia", negate=True)
        assert label == "USA - Australia — USA +1.5"

    def test_ledger_frame_recovers_line_from_stored_title(self) -> None:
        # The ledger passes the stored market.title (with '?') and no event title;
        # the matchup falls back to ticker codes, the line still parses.
        label = spread_label(SPREAD, "USA wins by more than 1.5 goals?", None, negate=False)
        assert label == "USA - AUS — USA -1.5"

    def test_aged_out_no_title_keeps_favorite_and_sign(self) -> None:
        # Feed dropped the market: no sub-title, no event title. Must still produce
        # a non-None favorite+sign label (not fall through to the raw ticker).
        assert spread_label(SPREAD, None, None, negate=False) == "USA - AUS — USA -spread"
        assert spread_label(SPREAD, None, None, negate=True) == "USA - AUS — USA +spread"

    def test_non_spread_ticker_returns_none(self) -> None:
        assert spread_label(MONEYLINE, "whatever", None, negate=False) is None
