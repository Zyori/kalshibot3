"""Total-goals series map, isolation, and threshold parsing."""
from __future__ import annotations

from src.sports.soccer import (
    is_soccer_ticker,
    total_goals_threshold,
    total_series_for_game,
)


def test_total_tickers_pass_isolation():
    # Total-goals markets are soccer — orders/positions on them must not be
    # refused by the cross-market guard.
    assert is_soccer_ticker("KXINTLFRIENDLYTOTAL-26JUN01COLCRI-3") is True


def test_non_soccer_still_refused():
    assert is_soccer_ticker("KXPRES-2028-DEM") is False


def test_game_series_maps_to_total():
    assert total_series_for_game("KXINTLFRIENDLYGAME") == "KXINTLFRIENDLYTOTAL"


def test_unmapped_game_series_returns_none():
    # World Cup per-game totals aren't listed yet — no mapping until verified.
    assert total_series_for_game("KXWCGAME") is None
    assert total_series_for_game("KXNOTAGAME") is None


def test_threshold_from_total_ticker():
    assert total_goals_threshold("KXINTLFRIENDLYTOTAL-26JUN01COLCRI-1") == 1.5
    assert total_goals_threshold("KXINTLFRIENDLYTOTAL-26JUN01COLCRI-4") == 4.5


def test_threshold_none_for_non_total_ticker():
    # A moneyline ticker isn't a total — no threshold.
    assert total_goals_threshold("KXINTLFRIENDLYGAME-26JUN01COLCRI-COL") is None


def test_threshold_none_for_garbage_suffix():
    assert total_goals_threshold("KXINTLFRIENDLYTOTAL-26JUN01COLCRI-TIE") is None
