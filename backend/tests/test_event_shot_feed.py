"""Event serializer carries the /summary shot feed + extra boxscore stats.

These are pure-function serializer tests (the route composition is exercised
live). They lock the wire shape LUTZ and the site both read: a flat `shots`
array and the four new per-side boxscore fields.
"""
from __future__ import annotations

from datetime import datetime, timezone

from src.api.routes.events import _live_payload, _shot_dict, _team_stats_dict
from src.ingestion.espn_scoreboard import EspnEvent, ShotEvent, TeamStats


def test_team_stats_dict_includes_new_boxscore_fields():
    s = TeamStats(score=1, shots=7, saves=3, blocked_shots=1, penalty_kicks_taken=1, penalty_goals=0)
    out = _team_stats_dict(s)
    assert out["saves"] == 3
    assert out["blocked_shots"] == 1
    assert out["penalty_kicks_taken"] == 1
    assert out["penalty_goals"] == 0
    # existing fields still present
    assert out["shots"] == 7 and out["score"] == 1


def test_team_stats_dict_new_fields_default_none():
    """A TeamStats from /scoreboard only (no summary yet) → new fields None."""
    out = _team_stats_dict(TeamStats(score=0, shots=2))
    assert out["saves"] is None
    assert out["penalty_kicks_taken"] is None


def test_shot_dict_shape():
    shot = ShotEvent(minute="4'", side="home", quality="saved", location="outside_box", raw_text="Attempt saved. ...")
    assert _shot_dict(shot) == {
        "minute": "4'",
        "side": "home",
        "quality": "saved",
        "location": "outside_box",
        "text": "Attempt saved. ...",
    }


def _event_with_shots(shots: tuple[ShotEvent, ...]) -> EspnEvent:
    return EspnEvent(
        espn_id="1",
        slug="fifa.friendly",
        kickoff_utc=datetime(2026, 6, 1, 18, 30, tzinfo=timezone.utc),
        state="in",
        period=2,
        clock_display="63:00",
        status_detail="63'",
        home_names=("Austria",),
        away_names=("Tunisia",),
        shots=shots,
    )


def test_live_payload_serializes_shots_in_order():
    shots = (
        ShotEvent(minute="4'", side="home", quality="saved", location="outside_box", raw_text="a"),
        ShotEvent(minute="63'", side="away", quality="goal", location="inside_box", raw_text="b"),
    )
    out = _live_payload(_event_with_shots(shots))
    assert out is not None
    assert [s["quality"] for s in out["shots"]] == ["saved", "goal"]
    assert [s["minute"] for s in out["shots"]] == ["4'", "63'"]


def test_live_payload_empty_shots():
    """A game with no shots → shots: [], not a missing key or error."""
    out = _live_payload(_event_with_shots(()))
    assert out is not None
    assert out["shots"] == []


def test_live_payload_none_when_no_espn():
    assert _live_payload(None) is None
