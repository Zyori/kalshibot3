"""Total-goals series map, isolation, and threshold parsing."""
from __future__ import annotations

from src.sports.soccer import (
    is_soccer_ticker,
    is_total_goals_ticker,
    total_goals_line,
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


def test_is_total_goals_ticker():
    # Presence check on the ticker shape — true for a total slot, regardless of
    # which line that slot turns out to be.
    assert is_total_goals_ticker("KXINTLFRIENDLYTOTAL-26JUN01COLCRI-1") is True
    assert is_total_goals_ticker("KXINTLFRIENDLYTOTAL-26JUN08NEDUZB-2") is True
    # A moneyline ticker isn't a total.
    assert is_total_goals_ticker("KXINTLFRIENDLYGAME-26JUN01COLCRI-COL") is False
    # Non-numeric suffix on a total series isn't a total-goals slot.
    assert is_total_goals_ticker("KXINTLFRIENDLYTOTAL-26JUN01COLCRI-TIE") is False


def test_line_comes_from_label_not_suffix():
    # The line is parsed from Kalshi's sub-title, NOT the ticker suffix. The
    # NEDUZB game (2026-06-08) started at Over 1.5 with suffix -2 — proving the
    # suffix is a slot index, not the line. The old suffix+0.5 formula read this
    # as 2.5 (the bug this fixes).
    assert total_goals_line("Over 1.5 goals scored") == 1.5
    assert total_goals_line("Over 2.5 goals scored") == 2.5
    assert total_goals_line("Over 3.5 goals scored") == 3.5
    # Whole-number or oddly-spaced lines still parse.
    assert total_goals_line("Over 2 goals scored") == 2.0


def test_line_none_for_unparseable_label():
    assert total_goals_line(None) is None
    assert total_goals_line("") is None
    assert total_goals_line("Total goals") is None


def test_total_event_ticker_derivation():
    from src.api.routes.events import _total_goals_event_ticker
    # Game event → totals event: same date+matchup, mapped series prefix.
    assert (
        _total_goals_event_ticker("KXINTLFRIENDLYGAME-26JUN01COLCRI")
        == "KXINTLFRIENDLYTOTAL-26JUN01COLCRI"
    )


def test_total_event_ticker_none_for_unmapped_league():
    from src.api.routes.events import _total_goals_event_ticker
    # World Cup has no per-game total series mapped yet.
    assert _total_goals_event_ticker("KXWCGAME-26JUN27COLPOR") is None
