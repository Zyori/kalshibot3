"""Tests for deriving a combo's display sport from its legs.

A same-sport parlay (all World Cup soccer games, all NFL games) gets that sport
for its badge; a mixed parlay or one with an unclassifiable leg gets None so we
never guess a sport for it.
"""

from __future__ import annotations

from src.sports.combo import uniform_combo_sport


def test_all_world_cup_soccer_is_soccer() -> None:
    assert uniform_combo_sport([
        "KXWCGAME-26JUN11MEXRSA-MEX",
        "KXWCGAME-26JUN13BRAMAR-BRA",
    ]) == "soccer"


def test_all_friendlies_is_soccer() -> None:
    assert uniform_combo_sport([
        "KXINTLFRIENDLYGAME-26JUN06BRAEGY-BRA",
        "KXINTLFRIENDLYGAME-26JUN06QATSLV-QAT",
    ]) == "soccer"


def test_mixed_soccer_and_nfl_is_none() -> None:
    assert uniform_combo_sport([
        "KXWCGAME-26JUN11MEXRSA-MEX",
        "KXNFL-26-KC",
    ]) is None


def test_all_nfl_is_nfl() -> None:
    assert uniform_combo_sport(["KXNFL-26-KC", "KXNFL-26-BUF"]) == "nfl"


def test_unclassifiable_leg_is_none() -> None:
    # A leg we can't map to any sport poisons the whole result — don't guess.
    assert uniform_combo_sport([
        "KXWCGAME-26JUN11MEXRSA-MEX",
        "KXSOMETHINGELSE-123",
    ]) is None


def test_none_leg_is_none() -> None:
    assert uniform_combo_sport(["KXWCGAME-26JUN11MEXRSA-MEX", None]) is None


def test_empty_is_none() -> None:
    assert uniform_combo_sport([]) is None
