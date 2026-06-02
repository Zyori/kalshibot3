"""_market_label — readable 'League — Home v Away — Selection' for the ledger."""
from __future__ import annotations

from types import SimpleNamespace

from src.api.routes.ledger import _market_label


def _bet(**kw) -> SimpleNamespace:
    base = dict(
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
