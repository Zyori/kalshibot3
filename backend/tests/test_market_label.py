"""_market_label — readable 'League — Home v Away — Selection' for the ledger."""
from __future__ import annotations

from types import SimpleNamespace

from src.api.routes.ledger import _market_label
from src.core.types import Sport


def _bet(**kw) -> SimpleNamespace:
    base = dict(
        sport=Sport.SOCCER,
        home_code="AUT", away_code="TUN", home_name="Austria", away_name="Tunisia",
        event_series="KXINTLFRIENDLYGAME", selection_code="AUT",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_full_names_selection_is_home():
    assert _market_label(_bet(selection_code="AUT"), "t") == "International Friendly — Austria v Tunisia — Austria"


def test_full_names_selection_is_away():
    assert _market_label(_bet(selection_code="TUN"), "t") == "International Friendly — Austria v Tunisia — Tunisia"


def test_draw_selection_renders_as_draw():
    assert _market_label(_bet(selection_code="TIE"), "t") == "International Friendly — Austria v Tunisia — Draw"


def test_falls_back_to_codes_when_no_names():
    label = _market_label(_bet(home_name=None, away_name=None, event_series="KXWCGAME", selection_code="AUT"), "t")
    assert label == "World Cup — AUT v TUN — AUT"


def test_unknown_series_uses_series_string():
    label = _market_label(_bet(event_series="KXMYSTERYGAME", home_name=None, away_name=None), "t")
    assert label == "KXMYSTERYGAME — AUT v TUN — AUT"


def test_falls_back_to_ticker_when_no_codes():
    # Old bet / futures: no structured codes → raw ticker.
    b = _bet(home_code=None, away_code=None, home_name=None, away_name=None,
             event_series=None, selection_code=None)
    assert _market_label(b, "KXSOMETHING-RAW-XYZ") == "KXSOMETHING-RAW-XYZ"


def test_no_codes_and_no_ticker_is_dash():
    b = _bet(home_code=None, away_code=None, event_series=None, selection_code=None)
    assert _market_label(b, None) == "—"


def test_combo_renders_as_parlay_with_leg_count():
    b = _bet(sport=Sport.COMBO, home_code=None, away_code=None,
             event_series=None, selection_code=None)
    assert _market_label(b, "KXMVE-RAW", leg_count=5) == "Parlay (5 legs)"


def test_combo_without_legs_falls_back_to_ticker():
    b = _bet(sport=Sport.COMBO, home_code=None, away_code=None,
             event_series=None, selection_code=None)
    assert _market_label(b, "KXMVE-RAW", leg_count=0) == "KXMVE-RAW"


def test_totals_include_match_context():
    """A totals bet carries the same team fields as a moneyline bet, so its label
    gets the full 'League — Home v Away — Over X goals' context, not a bare line."""
    b = _bet(event_series="KXWCGAME", home_name="Argentina", away_name="Austria",
             home_code="ARG", away_code="AUT")
    label = _market_label(b, "KXWCTOTAL-26JUN22ARGAUT-3", market_title="Over 1.5 goals scored")
    assert label == "World Cup — Argentina v Austria — Over 1.5 goals"


def test_totals_without_codes_falls_back_to_matchup():
    """Older totals bet missing team codes: fall back to the ticker matchup codes."""
    b = _bet(home_code=None, away_code=None, home_name=None, away_name=None,
             event_series=None, selection_code=None)
    label = _market_label(b, "KXWCTOTAL-26JUN22ARGAUT-3", market_title="Over 2.5 goals scored")
    assert label == "ARG - AUT — Over 2.5 goals"
