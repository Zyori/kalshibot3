"""Tests for deriving a combo's display sport from its legs.

A same-sport parlay (all World Cup soccer games, all NFL games) gets that sport
for its badge; a mixed parlay or one with an unclassifiable leg gets None so we
never guess a sport for it.
"""

from __future__ import annotations

from src.sports.combo import combo_leg_pick, uniform_combo_sport


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


def test_leg_pick_prefers_title() -> None:
    assert combo_leg_pick("Brazil", "KXWCGAME-26JUN11MEXRSA-MEX") == "Brazil"


def test_leg_pick_derives_team_from_soccer_ticker() -> None:
    assert combo_leg_pick(None, "KXWCGAME-26JUN11MEXRSA-MEX") == "MEX"


def test_leg_pick_tie_reads_draw() -> None:
    assert combo_leg_pick(None, "KXWCGAME-26JUN11MEXRSA-TIE") == "Draw"


def test_leg_pick_unparseable_ticker_uses_last_segment() -> None:
    assert combo_leg_pick(None, "KXNFL-26-KC") == "KC"


def test_leg_pick_nothing_is_question_mark() -> None:
    assert combo_leg_pick(None, None) == "?"
