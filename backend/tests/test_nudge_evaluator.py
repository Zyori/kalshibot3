"""Tests for the edge-triggered nudge evaluator (U7).

Pure logic — no DB, no supervisor. Verifies each trigger fires once per
crossing, doesn't spam while the condition persists, and resets when the
subject goes away.
"""

from __future__ import annotations

from src.services.nudge_evaluator import NudgeEvaluator, clock_to_minute

TICKER = "KXWCGAME-26JUN11MEXRSA-MEX"
EVENT = "KXWCGAME-26JUN11MEXRSA"


def test_clock_to_minute_parses_regulation_and_stoppage() -> None:
    assert clock_to_minute("67:42") == 67
    assert clock_to_minute("45+2:00") == 47
    assert clock_to_minute("90+5:00") == 95
    assert clock_to_minute(None) is None
    assert clock_to_minute("HT") is None
    assert clock_to_minute("") is None


def test_profit_fires_once_per_crossing() -> None:
    ev = NudgeEvaluator()
    # Below threshold → nothing.
    assert ev.evaluate_profit([(TICKER, 48.0)]) == []
    # Crosses +50 → one nudge.
    out = ev.evaluate_profit([(TICKER, 52.0)])
    assert len(out) == 1
    assert out[0].trigger == "profit_50"
    # Stays above → no second nudge (edge-trigger).
    assert ev.evaluate_profit([(TICKER, 55.0)]) == []


def test_profit_already_above_on_first_sight_fires_once() -> None:
    ev = NudgeEvaluator()
    out = ev.evaluate_profit([(TICKER, 70.0)])  # app start mid-game, already rich
    assert len(out) == 1
    assert ev.evaluate_profit([(TICKER, 72.0)]) == []  # suppressed after


def test_profit_resets_when_position_closes_then_reopens() -> None:
    ev = NudgeEvaluator()
    assert len(ev.evaluate_profit([(TICKER, 52.0)])) == 1
    # Position closes (absent from the list) → key resets.
    assert ev.evaluate_profit([]) == []
    # Re-opens and is rich again → fires again.
    assert len(ev.evaluate_profit([(TICKER, 52.0)])) == 1


def test_profit_none_pct_no_crash() -> None:
    ev = NudgeEvaluator()
    assert ev.evaluate_profit([(TICKER, None)]) == []


def test_clock_75_fires_once_on_crossing() -> None:
    ev = NudgeEvaluator()
    assert ev.evaluate_live_games([(EVENT, "74:10", 0)]) == []
    out = ev.evaluate_live_games([(EVENT, "76:00", 0)])
    assert len(out) == 1
    assert out[0].trigger == "clock_75"
    # Still past 75' → no repeat.
    assert ev.evaluate_live_games([(EVENT, "82:00", 0)]) == []


def test_clock_resets_when_game_ends() -> None:
    ev = NudgeEvaluator()
    assert len(ev.evaluate_live_games([(EVENT, "76:00", 0)])) == 1
    # Game ends (absent) → reset. A fresh game on the same event would re-fire.
    assert ev.evaluate_live_games([]) == []
    assert len(ev.evaluate_live_games([(EVENT, "76:00", 0)])) == 1


def test_red_card_fires_and_second_card_fires_again() -> None:
    ev = NudgeEvaluator()
    assert ev.evaluate_live_games([(EVENT, "30:00", 0)]) == []
    one = ev.evaluate_live_games([(EVENT, "31:00", 1)])
    assert len(one) == 1 and one[0].trigger == "red_card"
    # Same count → no repeat.
    assert ev.evaluate_live_games([(EVENT, "40:00", 1)]) == []
    # A second red card (count → 2) → fires again.
    two = ev.evaluate_live_games([(EVENT, "55:00", 2)])
    assert len(two) == 1 and two[0].trigger == "red_card"


def test_none_clock_pre_match_no_crash_no_nudge() -> None:
    ev = NudgeEvaluator()
    assert ev.evaluate_live_games([(EVENT, None, 0)]) == []


def test_clock_and_red_card_can_both_fire_same_tick() -> None:
    ev = NudgeEvaluator()
    out = ev.evaluate_live_games([(EVENT, "78:00", 1)])
    triggers = sorted(n.trigger for n in out)
    assert triggers == ["clock_75", "red_card"]
