"""Tests for WC standings parsing + team→group lookup.

ESPN serves the whole table in one call; these pin that we parse a group entry
into a TeamStanding correctly (stats, record, qualification note, advanced flag)
and that the team lookups match on both abbreviation and full name.
"""

from __future__ import annotations

from src.ingestion.wc_standings import (
    StandingsSnapshot,
    _group_from_raw,
)


def _entry(abbr: str, name: str, *, rank: int, w: int, d: int, lo: int,
           pts: int, note: str | None) -> dict:
    return {
        "team": {"id": "1", "displayName": name, "abbreviation": abbr},
        "note": {"description": note} if note else {},
        "stats": [
            {"name": "rank", "displayValue": str(rank)},
            {"name": "gamesPlayed", "displayValue": str(w + d + lo)},
            {"name": "wins", "displayValue": str(w)},
            {"name": "ties", "displayValue": str(d)},
            {"name": "losses", "displayValue": str(lo)},
            {"name": "points", "displayValue": str(pts)},
            {"name": "pointDifferential", "displayValue": "3"},
            {"name": "pointsFor", "displayValue": "5"},
            {"name": "pointsAgainst", "displayValue": "2"},
            {"name": "advanced", "displayValue": "1" if pts >= 6 else "0"},
            {"name": "overall", "displayValue": f"{w}-{d}-{lo}"},
        ],
    }


def _raw_group() -> dict:
    return {
        "name": "Group A",
        "abbreviation": "A",
        "standings": {
            "entries": [
                _entry("MEX", "Mexico", rank=1, w=2, d=0, lo=0, pts=6,
                       note="Advance to Round of 32"),
                _entry("RSA", "South Africa", rank=2, w=1, d=1, lo=0, pts=4,
                       note="Advance to Round of 32"),
                _entry("CZE", "Czechia", rank=4, w=0, d=0, lo=2, pts=0,
                       note="Eliminated"),
            ]
        },
    }


def test_group_parses_teams_and_stats() -> None:
    g = _group_from_raw(_raw_group())
    assert g is not None
    assert g.name == "Group A"
    mex = g.teams[0]
    assert mex.abbreviation == "MEX"
    assert mex.points == 6
    assert mex.wins == 2 and mex.draws == 0 and mex.losses == 0
    assert mex.record == "2-0-0"
    assert mex.advanced is True
    assert mex.qualification == "Advance to Round of 32"


def test_group_sorted_by_rank() -> None:
    g = _group_from_raw(_raw_group())
    assert g is not None
    # Czechia is rank 4 → must land last even though it was listed before some.
    assert [t.abbreviation for t in g.teams] == ["MEX", "RSA", "CZE"]


def test_eliminated_team_carries_note() -> None:
    g = _group_from_raw(_raw_group())
    cze = next(t for t in g.teams if t.abbreviation == "CZE")
    assert cze.qualification == "Eliminated"
    assert cze.advanced is False


def test_snapshot_lookup_by_abbr_and_name() -> None:
    g = _group_from_raw(_raw_group())
    snap = StandingsSnapshot(groups=[g])
    assert snap.group_for_team("MEX").name == "Group A"
    assert snap.group_for_team("Mexico").name == "Group A"
    assert snap.group_for_team("south africa").name == "Group A"  # case-insensitive
    assert snap.team("RSA").name == "South Africa"
    assert snap.group_for_team("Brazil") is None


def test_empty_group_is_dropped() -> None:
    assert _group_from_raw({"name": "Group Z", "standings": {"entries": []}}) is None
