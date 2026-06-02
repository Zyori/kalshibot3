"""ESPN /summary enrichment — shot-stream + boxscore parsing.

The parsers are pure (no HTTP), so these test the classification and the
raw-text floor directly: a shot line always becomes a ShotEvent, an
unrecognized phrasing degrades to quality='unknown'/location=None with the
raw text kept, and a non-shot line is skipped. Boxscore mapping is matched by
ESPN's human labels and keyed to the right side by homeAway.

Fixtures mirror the shape verified live against AUT-TUN (event 401856597).
"""
from __future__ import annotations

from src.ingestion.espn_scoreboard import (
    EspnEvent,
    _enrich_with_summary,
    _parse_commentary_shots,
    _parse_summary_boxscore_side,
)
from datetime import datetime, timezone

HOME = ("Austria", "austria", "aut")
AWAY = ("Tunisia", "tunisia", "tun")


def _commentary(text: str, minute: str | None = "10'") -> dict:
    item: dict = {"text": text}
    if minute is not None:
        item["time"] = {"displayValue": minute}
    return item


def test_shot_quality_classification():
    """Each templated stem maps to its quality; side + minute come through."""
    items = [
        _commentary("Attempt saved. Marcel Sabitzer (Austria) left footed shot from outside the box is saved.", "4'"),
        _commentary("Attempt missed. Romano Schmid (Austria) right footed shot from the centre of the box.", "6'"),
        _commentary("Attempt blocked. Hannibal Mejbri (Tunisia) shot from outside the box is blocked.", "20'"),
        _commentary("Firas Chaouat (Tunisia) hits the bar with a right footed shot from inside the box.", "30'"),
    ]
    shots = _parse_commentary_shots(items, HOME, AWAY)
    assert [s.quality for s in shots] == ["saved", "missed", "blocked", "woodwork"]
    assert [s.side for s in shots] == ["home", "home", "away", "away"]
    assert [s.minute for s in shots] == ["4'", "6'", "20'", "30'"]


def test_shot_location_buckets():
    """Inside/outside box phrases bucket; an unstated location is None."""
    items = [
        _commentary("Attempt missed. X (Austria) shot from outside the box."),
        _commentary("Attempt saved. Y (Tunisia) shot from the centre of the box."),
        _commentary("Attempt missed. Z (Austria) shot from a difficult angle."),  # no box phrase
    ]
    shots = _parse_commentary_shots(items, HOME, AWAY)
    assert [s.location for s in shots] == ["outside_box", "inside_box", None]


def test_goal_classification():
    """A 'Goal!' line is graded as a goal."""
    items = [_commentary("Goal! Austria 1, Tunisia 0. Player (Austria) shot from the centre of the box.", "63'")]
    shots = _parse_commentary_shots(items, HOME, AWAY)
    assert len(shots) == 1
    assert shots[0].quality == "goal"
    assert shots[0].minute == "63'"


def test_away_goal_attributed_to_away_not_home():
    """Regression: ESPN 'Goal!' lines carry the scoreline naming BOTH teams.
    The shooter's team is the FIRST parenthesized name, not 'any team in the
    line' — an away goal must not be mis-sided to home."""
    items = [_commentary(
        "Goal! Austria 1, Tunisia 1. Hannibal Mejbri (Tunisia) right footed shot from the centre of the box.",
        "70'",
    )]
    shots = _parse_commentary_shots(items, HOME, AWAY)
    assert shots[0].side == "away"  # the shooter is Tunisia, despite 'Austria' appearing first
    assert shots[0].quality == "goal"


def test_keeper_team_does_not_steal_side():
    """The keeper's team is named in a later paren ('saved by X (Tunisia)').
    The shooter's (first) paren wins."""
    items = [_commentary(
        "Attempt saved. Marcel Sabitzer (Austria) shot from outside the box is saved by Chamakh (Tunisia)."
    )]
    shots = _parse_commentary_shots(items, HOME, AWAY)
    assert shots[0].side == "home"


def test_rebound_goal_off_woodwork_is_goal_not_woodwork():
    """Regression: 'goal!' is checked before woodwork, so a rebound goal that
    mentions the post still grades as a goal."""
    items = [_commentary("Goal! Player (Austria) scores on the rebound after his shot hits the post.", "80'")]
    shots = _parse_commentary_shots(items, HOME, AWAY)
    assert shots[0].quality == "goal"


def test_no_phantom_goal_from_towards_goal():
    """Regression: a non-goal attempt mentioning 'goal' ('shot towards goal')
    must NOT be graded a goal — there is no loose word-'goal' fallback."""
    items = [_commentary("Attempt missed. Player (Austria) drags his shot towards goal but well wide.")]
    shots = _parse_commentary_shots(items, HOME, AWAY)
    assert shots[0].quality == "missed"  # matched the 'attempt missed' stem, not 'goal'


def test_abbreviation_does_not_collide_in_prose():
    """Regression: a 3-letter abbrev in home_names ('aut') must not match inside
    an unrelated word. Side resolves only from the first parenthesized team."""
    home = ("Austria", "austria", "aut")
    away = ("Tunisia", "tunisia", "tun")
    # No parenthesized team; prose contains 'authentic' (has 'aut') and 'tunnel'
    items = [_commentary("Attempt blocked. An authentic scramble in the tunnel-like crowd of bodies.")]
    shots = _parse_commentary_shots(items, home, away)
    assert shots[0].side is None  # not 'home' via 'aut' in 'authentic'


def test_non_shot_lines_skipped():
    """Fouls, corners, substitutions are not shots — dropped, not recorded."""
    items = [
        _commentary("Foul by Ali Abdi (Tunisia)."),
        _commentary("Corner, Austria. Conceded by Ismael."),
        _commentary("Substitution, Austria. Player on for Player."),
        _commentary("Stefan Posch (Austria) wins a free kick on the right wing."),
    ]
    assert _parse_commentary_shots(items, HOME, AWAY) == ()


def test_unknown_quality_degrades_with_raw_text():
    """A shot-like line ('attempt') with no recognizable quality stem records
    the shot as 'unknown', keeping the raw text — the raw-text floor (R5)."""
    items = [_commentary("Attempt by someone, the wording here is novel and unmatched.")]
    shots = _parse_commentary_shots(items, HOME, AWAY)
    assert len(shots) == 1
    assert shots[0].quality == "unknown"
    assert shots[0].location is None
    assert "novel and unmatched" in shots[0].raw_text


def test_side_unresolved_when_team_name_absent():
    """A shot whose text names neither team still records, side=None."""
    items = [_commentary("Attempt saved. A substitute whose club we can't match shoots wide.")]
    shots = _parse_commentary_shots(items, HOME, AWAY)
    assert len(shots) == 1
    assert shots[0].side is None


def test_empty_commentary():
    """Pre-match / no commentary → no shots, no error."""
    assert _parse_commentary_shots([], HOME, AWAY) == ()
    assert _parse_commentary_shots(None, HOME, AWAY) == ()  # type: ignore[arg-type]


def test_boxscore_extra_stats():
    """The four extra labels map to their fields; unknown labels ignored."""
    stats = [
        {"label": "Saves", "displayValue": "3"},
        {"label": "Blocked Shots", "displayValue": "1"},
        {"label": "Penalty Kicks Taken", "displayValue": "1"},
        {"label": "Penalty Goals", "displayValue": "0"},
        {"label": "Accurate Passes", "displayValue": "347"},  # ignored
    ]
    out = _parse_summary_boxscore_side(stats)
    assert out == {"saves": 3, "blocked_shots": 1, "penalty_kicks_taken": 1, "penalty_goals": 0}


def test_boxscore_bad_value_skipped():
    """A non-numeric stat value is skipped, not crashed."""
    out = _parse_summary_boxscore_side([{"label": "Saves", "displayValue": "--"}])
    assert "saves" not in out


def _bare_event() -> EspnEvent:
    return EspnEvent(
        espn_id="401856597",
        slug="fifa.friendly",
        kickoff_utc=datetime(2026, 6, 1, 18, 30, tzinfo=timezone.utc),
        state="in",
        period=2,
        clock_display="63:00",
        status_detail="63'",
        home_names=HOME,
        away_names=AWAY,
    )


def test_enrich_attaches_shots_and_keys_boxscore_by_homeaway():
    """End-to-end enrich: shots attach, and boxscore stats land on the correct
    side via homeAway (not list order)."""
    payload = {
        "commentary": [
            _commentary("Attempt saved. Sabitzer (Austria) shot from outside the box.", "4'"),
            _commentary("Goal! Player (Tunisia) shot from inside the box.", "63'"),
        ],
        "boxscore": {
            "teams": [
                {"homeAway": "away", "statistics": [{"label": "Saves", "displayValue": "5"}]},
                {"homeAway": "home", "statistics": [{"label": "Saves", "displayValue": "2"}]},
            ]
        },
    }
    out = _enrich_with_summary(_bare_event(), payload)
    assert len(out.shots) == 2
    assert out.shots[0].side == "home"
    assert out.shots[1].quality == "goal"
    # homeAway keying: home got 2 saves, away got 5 — despite away being first.
    assert out.home_stats.saves == 2
    assert out.away_stats.saves == 5


def test_enrich_survives_missing_boxscore():
    """A payload with no boxscore still attaches shots and leaves stats None."""
    payload = {"commentary": [_commentary("Attempt missed. X (Austria) shot from outside the box.")]}
    out = _enrich_with_summary(_bare_event(), payload)
    assert len(out.shots) == 1
    assert out.home_stats.saves is None


def test_boxscore_carries_forward_when_empty():
    """ESPN's boxscore is intermittent. When this poll has no boxscore, the
    previous poll's saves/blocks/penalties carry forward instead of nulling."""
    from dataclasses import replace
    # prev poll had real stats
    prev = _bare_event()
    prev = replace(
        prev,
        home_stats=replace(prev.home_stats, saves=3, blocked_shots=1, penalty_kicks_taken=1),
        away_stats=replace(prev.away_stats, saves=5),
    )
    # this poll's /summary has shots but an EMPTY boxscore
    payload = {"commentary": [_commentary("Attempt saved. X (Austria) shot from outside the box.")]}
    out = _enrich_with_summary(_bare_event(), payload, prev)
    assert out.home_stats.saves == 3
    assert out.home_stats.blocked_shots == 1
    assert out.away_stats.saves == 5
    assert len(out.shots) == 1  # shots still parsed fresh


def test_fresh_boxscore_overwrites_carried():
    """A non-empty boxscore this poll wins over the carried-forward value."""
    from dataclasses import replace
    prev = _bare_event()
    prev = replace(prev, home_stats=replace(prev.home_stats, saves=3))
    payload = {
        "commentary": [],
        "boxscore": {"teams": [{"homeAway": "home", "statistics": [{"label": "Saves", "displayValue": "7"}]}]},
    }
    out = _enrich_with_summary(_bare_event(), payload, prev)
    assert out.home_stats.saves == 7  # fresh value, not the carried 3
