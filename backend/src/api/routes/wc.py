"""World Cup group standings — read-only board.

The live WC group table (see ingestion/wc_standings.py): points, rank, W-D-L,
goal difference, and each team's qualification state ("Advance to Round of 32" /
"Best 8 advance" / "Eliminated"). The price-moving tournament context LUTZ would
otherwise be blind to. Served here on demand and piped into the partner context.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from src.core.types import utc_iso
from src.ingestion.wc_standings import Group, TeamStanding

router = APIRouter()


def _team_to_dict(t: TeamStanding) -> dict[str, Any]:
    return {
        "team_id": t.team_id,
        "name": t.name,
        "abbreviation": t.abbreviation,
        "rank": t.rank,
        "played": t.played,
        "wins": t.wins,
        "draws": t.draws,
        "losses": t.losses,
        "points": t.points,
        "goal_difference": t.goal_difference,
        "goals_for": t.goals_for,
        "goals_against": t.goals_against,
        "record": t.record,
        "advanced": t.advanced,
        "qualification": t.qualification,
    }


def _group_to_dict(g: Group) -> dict[str, Any]:
    return {
        "name": g.name,
        "abbreviation": g.abbreviation,
        "teams": [_team_to_dict(t) for t in g.teams],
    }


@router.get("/wc/standings")
async def get_wc_standings(request: Request) -> dict[str, Any]:
    """The full WC group table, groups A–L, teams in table order. Empty until the
    first poll lands (or before the tournament when ESPN's table is all zeros —
    the structure is present, the numbers fill in as games play)."""
    wc = getattr(request.app.state, "wc_standings", None)
    if wc is None:
        return {"groups": [], "refreshed_at": None}
    snap = wc.snapshot
    return {
        "groups": [_group_to_dict(g) for g in snap.groups],
        "refreshed_at": utc_iso(snap.refreshed_at),
    }
