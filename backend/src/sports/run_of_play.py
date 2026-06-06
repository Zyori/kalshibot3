"""Run-of-play serializer — the single live-game-state shape.

Turns an EspnEvent into the JSON blob the rest of the app treats as "the
live run-of-play": score, per-team stats, last event, and the per-shot
stream. The events route serves it to the frontend and LUTZ; trade-snapshot
capture freezes the same call at fill time. One serializer so the frozen
snapshot is byte-identical to what was on screen live — no parallel
game-state assembler that can drift.
"""

from __future__ import annotations

from typing import Any

from src.ingestion.espn_scoreboard import EspnEvent, MatchEvent, ShotEvent, TeamStats


def _team_stats_dict(s: TeamStats) -> dict[str, Any]:
    return {
        "score": s.score,
        "shots": s.shots,
        "shots_on_target": s.shots_on_target,
        "possession_pct": s.possession_pct,
        "corners": s.corners,
        "fouls": s.fouls,
        "yellow_cards": s.yellow_cards,
        "red_cards": s.red_cards,
        # From the richer /summary boxscore — None until a live game's summary
        # is fetched (or for leagues without the breakdown).
        "saves": s.saves,
        "blocked_shots": s.blocked_shots,
        "penalty_kicks_taken": s.penalty_kicks_taken,
        "penalty_goals": s.penalty_goals,
    }


def _match_event_dict(e: MatchEvent) -> dict[str, Any]:
    return {
        "kind": e.kind,
        "minute": e.minute,
        "player": e.player,
        "side": e.side,
        "text": e.text,
    }


def _shot_dict(s: ShotEvent) -> dict[str, Any]:
    return {
        "minute": s.minute,
        "side": s.side,
        "quality": s.quality,
        "location": s.location,
        "text": s.raw_text,
    }


def live_payload(espn: EspnEvent | None) -> dict[str, Any] | None:
    """Best-effort live snapshot: score + per-team stats + last event + shots.
    None when ESPN didn't match the event (no league mapping, or game is
    far enough out that the scoreboard returned nothing). The frontend
    treats null as "show kickoff time only", not an error."""
    if espn is None:
        return None
    home_name = espn.home_names[0] if espn.home_names else None
    away_name = espn.away_names[0] if espn.away_names else None
    return {
        "espn_id": espn.espn_id,
        "home_name": home_name,
        "away_name": away_name,
        "home": _team_stats_dict(espn.home_stats),
        "away": _team_stats_dict(espn.away_stats),
        "last_event": _match_event_dict(espn.last_event) if espn.last_event else None,
        "shots": [_shot_dict(s) for s in espn.shots],
    }
