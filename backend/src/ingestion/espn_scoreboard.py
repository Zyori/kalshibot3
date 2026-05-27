"""ESPN scoreboard ingestion — true kickoff times + game state for soccer.

Kalshi's `occurrence_datetime` is unreliable for kickoff: it's the
settlement deadline, which on CONMEBOL games runs 3+ hours past real
kickoff. ESPN's per-league scoreboard publishes the actual kickoff to
the minute, plus pre/in/post state we can use to know when a game is
actually live (not just 'we passed the proxy time').

This module:
  - polls each ESPN league we have a slug for, on an adaptive cadence
  - normalizes events into a flat list of records we can search by
    (date, team-name pair)
  - exposes a snapshot dict the matcher reads — no Kalshi knowledge here,
    just ESPN as a source of truth

We deliberately do not store ESPN's `id` long-term — match-window
lookups by (date, normalized teams) keep us decoupled from ESPN's own
identifiers, which would couple us to their schema if it ever shifts.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from src.core.logging import get_logger
from src.sports.soccer import SOCCER_ESPN_SLUGS

log = get_logger(__name__)

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
HTTP_TIMEOUT_S = 8.0
POLL_INTERVAL_IDLE_S = 1800   # 30 min when no games are live
POLL_INTERVAL_LIVE_S = 60     # 1 min when at least one game is in progress
FETCH_WINDOW_DAYS_BACK = 1  # yesterday — covers UTC/ET date-boundary games
FETCH_WINDOW_DAYS_FORWARD = 3  # today + next 2 days
# Total window: 4 days. ESPN buckets events by US-local date, so a Kalshi
# ticker dated 26MAY26 for an 8:30 PM ET game lives under ESPN's ?dates=20260526
# even though the UTC kickoff is 2026-05-27 00:30Z. Without yesterday-UTC in
# the fetch set we miss every evening-of-the-previous-day game.


@dataclass(frozen=True)
class EspnEvent:
    """One ESPN event normalized into the fields we care about."""
    espn_id: str
    slug: str
    kickoff_utc: datetime
    state: str  # 'pre' | 'in' | 'post'
    period: int | None
    """Soccer: 1 = first half, 2 = second half, 3 = ET first, 4 = ET second,
    5 = penalties. None for pre/post."""
    clock_display: str | None
    """e.g. '67:42' or '45+2:00'. None for pre/post."""
    status_detail: str | None
    """ESPN's human label: 'HT', 'FT', 'AET', 'Penalties', etc. Useful
    for halftime / fulltime where clock alone is ambiguous."""
    home_names: tuple[str, ...]   # display + short + abbreviation
    away_names: tuple[str, ...]


@dataclass
class EspnSnapshot:
    """The matcher reads this. Refreshed by the poller in-place."""
    events: list[EspnEvent] = field(default_factory=list)
    refreshed_at: datetime | None = None


def _parse_kickoff(iso: str) -> datetime | None:
    """ESPN sends UTC ISO with Z suffix; sometimes with seconds, sometimes not."""
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None


def _team_names(team: dict[str, Any]) -> tuple[str, ...]:
    """All the names we'll try to match on. De-duped, non-empty, lower-cased."""
    raw = (
        team.get("displayName"),
        team.get("shortDisplayName"),
        team.get("name"),
        team.get("abbreviation"),
    )
    seen: list[str] = []
    for r in raw:
        if not r:
            continue
        v = str(r).strip().lower()
        if v and v not in seen:
            seen.append(v)
    return tuple(seen)


def _event_from_raw(raw: dict[str, Any], slug: str) -> EspnEvent | None:
    """Convert one /scoreboard event into our normalized record. None if
    the payload is missing the fields we need."""
    espn_id = raw.get("id")
    date = raw.get("date")
    if not espn_id or not date:
        return None
    kickoff = _parse_kickoff(date)
    if kickoff is None:
        return None
    status = raw.get("status", {})
    state = status.get("type", {}).get("state", "")
    # ESPN sends a few related fields for in-progress games:
    #   status.period: int (1, 2, etc.)
    #   status.displayClock: '67:42' or '45+2:00'
    #   status.type.shortDetail / detail: human like 'HT', '67', 'FT'
    period = status.get("period")
    period_int = int(period) if isinstance(period, (int, float)) and period > 0 else None
    clock = status.get("displayClock")
    clock_str = str(clock) if clock else None
    detail = status.get("type", {}).get("shortDetail") or status.get("type", {}).get("detail")
    detail_str = str(detail) if detail else None
    comps = raw.get("competitions", [{}])
    if not comps:
        return None
    teams = comps[0].get("competitors", [])
    home = next((t for t in teams if t.get("homeAway") == "home"), None)
    away = next((t for t in teams if t.get("homeAway") == "away"), None)
    if home is None or away is None:
        return None
    return EspnEvent(
        espn_id=str(espn_id),
        slug=slug,
        kickoff_utc=kickoff.astimezone(timezone.utc),
        state=state or "pre",
        period=period_int,
        clock_display=clock_str,
        status_detail=detail_str,
        home_names=_team_names(home.get("team", {})),
        away_names=_team_names(away.get("team", {})),
    )


class EspnScoreboard:
    """Polls ESPN scoreboards for every soccer slug we have, on a 5-min
    cadence. Single instance per process, lives on the supervisor.

    The matcher reads from `.snapshot` directly — that's a single
    EspnSnapshot dataclass we mutate in place each poll cycle. Atomic
    swap so a partial fetch never makes the matcher see a half-built list.
    """

    def __init__(self, slugs: Iterable[str] | None = None) -> None:
        # Distinct slugs only (multiple Kalshi prefixes can map to the same
        # ESPN slug, e.g. KXFIFAGAME and KXINTLFRIENDLYGAME both → fifa.friendly).
        if slugs is None:
            slugs = (s for s in SOCCER_ESPN_SLUGS.values() if s is not None)
        self._slugs: tuple[str, ...] = tuple(sorted(set(slugs)))
        self.snapshot = EspnSnapshot()
        self._stopped = False
        self._task: asyncio.Task | None = None

    async def run(self) -> None:
        """Long-running poller. Initial fetch on start, then adaptive cadence:
        30 min when nothing is live, 60s when at least one game is in
        progress (so the match-clock UI updates roughly per minute)."""
        await self._refresh_once()
        while not self._stopped:
            live_now = any(e.state == "in" for e in self.snapshot.events)
            interval = POLL_INTERVAL_LIVE_S if live_now else POLL_INTERVAL_IDLE_S
            await asyncio.sleep(interval)
            try:
                await self._refresh_once()
            except Exception:  # noqa: BLE001 — never let a bad poll kill the loop
                log.exception("espn_refresh_failed")

    async def stop(self) -> None:
        self._stopped = True

    async def _refresh_once(self) -> None:
        """One full pass: fetch each slug for the current FETCH_WINDOW.
        Build a fresh list and swap it in atomically."""
        now = datetime.now(timezone.utc)
        dates = [
            (now + _day_offset(d)).strftime("%Y%m%d")
            for d in range(-FETCH_WINDOW_DAYS_BACK, FETCH_WINDOW_DAYS_FORWARD)
        ]
        events: list[EspnEvent] = []
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S) as client:
            for slug in self._slugs:
                for date_str in dates:
                    try:
                        events.extend(await self._fetch_one(client, slug, date_str))
                    except Exception as e:  # noqa: BLE001
                        log.warning("espn_fetch_failed", slug=slug, date=date_str, error=str(e)[:120])
        # De-dupe by espn_id (same event can appear under "today" and "tomorrow"
        # at the date boundary depending on ESPN's timezone interpretation).
        deduped: dict[str, EspnEvent] = {}
        for e in events:
            deduped[e.espn_id] = e
        self.snapshot = EspnSnapshot(
            events=list(deduped.values()),
            refreshed_at=now,
        )
        log.info("espn_refreshed", slugs=len(self._slugs), events=len(deduped))

    async def _fetch_one(
        self, client: httpx.AsyncClient, slug: str, date_str: str,
    ) -> list[EspnEvent]:
        url = f"{ESPN_BASE}/{slug}/scoreboard?dates={date_str}"
        r = await client.get(url)
        if r.status_code != 200:
            # 400 means ESPN doesn't recognize the slug (already pruned in
            # SOCCER_ESPN_SLUGS, but defensive). 5xx is transient — let
            # the caller log and move on.
            return []
        payload = r.json()
        out: list[EspnEvent] = []
        for raw in payload.get("events", []) or []:
            ev = _event_from_raw(raw, slug)
            if ev is not None:
                out.append(ev)
        return out


def _day_offset(d: int):
    """Avoid importing timedelta at module top for a one-line helper."""
    from datetime import timedelta
    return timedelta(days=d)
