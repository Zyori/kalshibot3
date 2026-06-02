"""parse_market_ticker — decode a per-game ticker into structured codes."""
from __future__ import annotations

from src.sports.soccer import parse_market_ticker


def test_parses_friendly_team_selection():
    p = parse_market_ticker("KXINTLFRIENDLYGAME-26JUN01AUTTUN-AUT")
    assert p is not None
    assert p.series == "KXINTLFRIENDLYGAME"
    assert p.home_code == "AUT"
    assert p.away_code == "TUN"
    assert p.selection_code == "AUT"


def test_parses_draw_selection():
    p = parse_market_ticker("KXINTLFRIENDLYGAME-26JUN01AUTTUN-TIE")
    assert p is not None
    assert p.selection_code == "TIE"
    assert (p.home_code, p.away_code) == ("AUT", "TUN")


def test_parses_conmebol_ticker():
    p = parse_market_ticker("KXCONMEBOLSUDGAME-26MAY26SLAREC-REC")
    assert p is not None
    assert p.series == "KXCONMEBOLSUDGAME"
    assert (p.home_code, p.away_code, p.selection_code) == ("SLA", "REC", "REC")


def test_world_cup_game():
    p = parse_market_ticker("KXWCGAME-26JUN11MEXRSA-MEX")
    assert p is not None
    assert (p.home_code, p.away_code, p.selection_code) == ("MEX", "RSA", "MEX")


def test_returns_none_for_futures_or_derivative():
    # Futures/derivative tickers don't match the per-game shape.
    assert parse_market_ticker("KXMENWORLDCUP-26-BRA") is None
    assert parse_market_ticker("KXWCGOALLEADER-26-MESSI") is None


def test_returns_none_for_garbage():
    assert parse_market_ticker("") is None
    assert parse_market_ticker("not-a-ticker") is None
    assert parse_market_ticker("KXEPLGAME-badformat-XXX") is None
