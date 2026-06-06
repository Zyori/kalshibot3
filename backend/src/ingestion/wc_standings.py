"""ESPN World Cup group-standings ingestion.

LUTZ reasons about WC markets (who wins a game, who advances) but, without this,
is blind to the live group table — points, rank, and whether a team has clinched
or been eliminated. That qualification state is exactly what moves WC moneyline
and advancement prices and isn't in the model's training knowledge for an
in-progress tournament.

ESPN serves the entire table — all 12 groups, every team, full stats, plus a
per-team qualification `note` ("Advance to Round of 32" / "Best 8 advance" /
"Eliminated") — in ONE free call. We poll that into an in-memory snapshot, same
ephemeral pattern as espn_news / espn_scoreboard: no DB, a restart re-fetches.

Note on form: ESPN's last-5 `WWWDD` string lives only on the heavy per-team
object (48 separate fetches). Not worth polling for a 5-char string; the `record`
field here ("2-0-1", from the standings `overall`) carries the same W-D-L signal
in the one call. Head-to-head is sourced separately from the live /summary the
scoreboard already fetches (see partner context).
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from src.core.logging import get_logger

log = get_logger(__name__)

STANDINGS_URL = "https://site.api.espn.com/apis/v2/sports/soccer/fifa.world/standings"
SEASON = 2026
# Standings only change when a match ends, so polling is cheap and slow by
# default. We poll faster while a WC game is live/imminent (the window where the
# table actually moves), same kickoff_soon hook the news poller uses.
POLL_INTERVAL_IDLE_S = 1800   # 30 min — nothing in play
POLL_INTERVAL_HOT_S = 300     # 5 min — a WC game is live or imminent
HTTP_TIMEOUT_S = 10.0


@dataclass(frozen=True)
class TeamStanding:
    """One team's row in its group. `record` is the W-D-L summary ("2-0-1").
    `qualification` is ESPN's note ("Advance to Round of 32", "Eliminated",
    "Best 8 advance") — None pre-tournament / when ESPN omits it. `advanced` is
    True once the team has clinched a knockout spot."""
    team_id: str
    name: str
    abbreviation: str
    rank: int | None
    played: int
    wins: int
    draws: int
    losses: int
    points: int
    goal_difference: int
    goals_for: int
    goals_against: int
    record: str
    advanced: bool
    qualification: str | None


@dataclass(frozen=True)
class Group:
    """One WC group (A–L), teams ordered by rank (table order)."""
    name: str            # "Group A"
    abbreviation: str    # "A"
    teams: tuple[TeamStanding, ...]


@dataclass
class StandingsSnapshot:
    """The reader sees this; the poller swaps it in-place each cycle."""
    groups: list[Group] = field(default_factory=list)
    refreshed_at: datetime | None = None

    def group_for_team(self, abbr_or_name: str) -> Group | None:
        """The group containing a team, matched on abbreviation or name
        (case-insensitive). Lets the partner join a market's teams to their
        group context."""
        key = abbr_or_name.strip().lower()
        for g in self.groups:
            for t in g.teams:
                if t.abbreviation.lower() == key or t.name.lower() == key:
                    return g
        return None

    def team(self, abbr_or_name: str) -> TeamStanding | None:
        """One team's standing row, matched on abbreviation or name."""
        key = abbr_or_name.strip().lower()
        for g in self.groups:
            for t in g.teams:
                if t.abbreviation.lower() == key or t.name.lower() == key:
                    return t
        return None


def _stat(stats: list[dict[str, Any]], name: str) -> Any:
    """Pull a stat's value by ESPN's `name` field. ESPN gives both a numeric
    `value` and a string `displayValue`; we take displayValue (it carries the
    record string "2-0-1" and is safe to int() for the numeric ones)."""
    for s in stats:
        if s.get("name") == name:
            return s.get("displayValue")
    return None


def _int(stats: list[dict[str, Any]], name: str) -> int:
    raw = _stat(stats, name)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _team_standing(entry: dict[str, Any]) -> TeamStanding | None:
    team = entry.get("team") or {}
    name = team.get("displayName")
    if not name:
        return None
    stats = entry.get("stats") or []
    note = entry.get("note") or {}
    return TeamStanding(
        team_id=str(team.get("id") or ""),
        name=str(name),
        abbreviation=str(team.get("abbreviation") or ""),
        rank=_int(stats, "rank") or None,
        played=_int(stats, "gamesPlayed"),
        wins=_int(stats, "wins"),
        draws=_int(stats, "ties"),
        losses=_int(stats, "losses"),
        points=_int(stats, "points"),
        goal_difference=_int(stats, "pointDifferential"),
        goals_for=_int(stats, "pointsFor"),
        goals_against=_int(stats, "pointsAgainst"),
        record=str(_stat(stats, "overall") or "0-0-0"),
        advanced=_int(stats, "advanced") > 0,
        qualification=(note.get("description") or None),
    )


def _group_from_raw(raw: dict[str, Any]) -> Group | None:
    entries = ((raw.get("standings") or {}).get("entries")) or []
    teams = tuple(
        t for t in (_team_standing(e) for e in entries) if t is not None
    )
    if not teams:
        return None
    # Table order: by rank when present, else by points then GD (ESPN usually
    # pre-sorts, but don't trust it — a wrong table order misleads the read).
    teams = tuple(sorted(
        teams,
        key=lambda t: (t.rank if t.rank is not None else 99, -t.points, -t.goal_difference),
    ))
    return Group(
        name=str(raw.get("name") or ""),
        abbreviation=str(raw.get("abbreviation") or ""),
        teams=teams,
    )


class WcStandings:
    """Polls ESPN's WC standings into an in-memory snapshot. One instance on the
    supervisor; the /api/wc route + partner context read `.snapshot`."""

    def __init__(self, kickoff_soon: Callable[[], bool] | None = None) -> None:
        self.snapshot = StandingsSnapshot()
        self._stopped = False
        # True when a WC game is live/imminent → poll fast. None → always slow.
        self._kickoff_soon = kickoff_soon

    async def run(self) -> None:
        await self._refresh_once()
        while not self._stopped:
            await self._wait_next_poll()
            if self._stopped:
                break
            try:
                await self._refresh_once()
            except Exception:  # noqa: BLE001 — a bad poll never kills the loop
                log.exception("wc_standings_refresh_failed")

    async def _wait_next_poll(self) -> None:
        """Hot now → one hot interval. Idle → sleep in hot-interval chunks up to
        the idle total, returning early when a kickoff enters the window."""
        soon = self._kickoff_soon
        if soon is not None and soon():
            await asyncio.sleep(POLL_INTERVAL_HOT_S)
            return
        waited = 0
        while waited < POLL_INTERVAL_IDLE_S and not self._stopped:
            await asyncio.sleep(POLL_INTERVAL_HOT_S)
            waited += POLL_INTERVAL_HOT_S
            if soon is not None and soon():
                return

    async def stop(self) -> None:
        self._stopped = True

    async def _refresh_once(self) -> None:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S) as client:
            r = await client.get(STANDINGS_URL, params={"season": SEASON})
        if r.status_code != 200:
            log.warning("wc_standings_fetch_non_200", status=r.status_code)
            return
        children = r.json().get("children") or []
        groups = [g for g in (_group_from_raw(c) for c in children) if g is not None]
        self.snapshot = StandingsSnapshot(
            groups=groups,
            refreshed_at=datetime.now(timezone.utc),
        )
        log.info("wc_standings_refreshed", groups=len(groups))
