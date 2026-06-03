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
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from src.core.logging import get_logger
from src.sports.soccer import SOCCER_ESPN_SLUGS

log = get_logger(__name__)

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
HTTP_TIMEOUT_S = 8.0
POLL_INTERVAL_IDLE_S = 1800   # 30 min when no games are live
POLL_INTERVAL_LIVE_S = 40     # when at least one game is in progress — keeps
                              # the score/clock within ~1 poll of real time
POLL_INTERVAL_BURST_S = 10    # when a watched market just spiked (likely a goal/
                              # red card) — tighten to catch the /summary detail
                              # within ~10s instead of ~40s. ESPN is free + unmetered,
                              # so the burst's only cost is a few extra requests.
BURST_WINDOW_S = 75           # how long a single burst request stays hot before
                              # decaying back to the live cadence
FETCH_WINDOW_DAYS_BACK = 1  # yesterday — covers UTC/ET date-boundary games
FETCH_WINDOW_DAYS_FORWARD = 3  # today + next 2 days
# Total window: 4 days. ESPN buckets events by US-local date, so a Kalshi
# ticker dated 26MAY26 for an 8:30 PM ET game lives under ESPN's ?dates=20260526
# even though the UTC kickoff is 2026-05-27 00:30Z. Without yesterday-UTC in
# the fetch set we miss every evening-of-the-previous-day game.


@dataclass(frozen=True)
class TeamStats:
    """In-game stats per side. Numeric fields are int (counts) or float
    (percentages). None means ESPN didn't ship the stat (pre-match, or
    leagues without that breakdown).

    `saves`/`blocked_shots`/`penalty_kicks_taken`/`penalty_goals` come from the
    richer /summary boxscore (not the /scoreboard payload) — None until a live
    game's summary is fetched. The penalty fields are the coarse PK signal LUTZ
    asked for: they say a spot-kick was taken, not that one is imminent."""
    score: int | None = None
    shots: int | None = None
    shots_on_target: int | None = None
    possession_pct: float | None = None
    corners: int | None = None
    fouls: int | None = None
    yellow_cards: int = 0
    red_cards: int = 0
    saves: int | None = None
    blocked_shots: int | None = None
    penalty_kicks_taken: int | None = None
    penalty_goals: int | None = None


@dataclass(frozen=True)
class ShotEvent:
    """One shot from the /summary commentary stream. `quality` and `location`
    are best-effort parses of ESPN's templated commentary text; `raw_text` is
    always the original sentence, so a parse miss degrades to a less-detailed
    shot, never a lost or broken one (the raw-text floor)."""
    minute: str | None
    """Match clock as ESPN renders it, e.g. "4'" or "45+2'"."""
    side: str | None  # 'home' | 'away' | None (when the team name didn't resolve)
    quality: str
    """'goal' | 'saved' | 'missed' | 'blocked' | 'woodwork' | 'unknown'."""
    location: str | None
    """'inside_box' | 'outside_box' | None (unparseable / not stated)."""
    raw_text: str


@dataclass(frozen=True)
class MatchEvent:
    """One detail row from ESPN's `competition.details` — a goal, card,
    substitution, etc. We only carry the ones we display in-line."""
    kind: str
    """'goal' | 'yellow' | 'red' | 'other'."""
    minute: str | None
    """ESPN's `clock.displayValue`: '23'', '45+2'', or None."""
    player: str | None
    side: str | None  # 'home' | 'away' | None
    text: str
    """ESPN's raw text label, e.g. 'Yellow Card', 'Goal - Header'."""


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
    home_stats: TeamStats = field(default_factory=TeamStats)
    away_stats: TeamStats = field(default_factory=TeamStats)
    last_event: MatchEvent | None = None
    """The most recent goal/card/etc. — what we render as 'Last: ...' in
    the header. None when nothing has happened yet (pre or quiet first
    few minutes)."""
    shots: tuple[ShotEvent, ...] = ()
    """Per-shot stream from /summary, in event order. Empty until a live game's
    summary is fetched (and for any game ESPN has no commentary for)."""


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
    """All the names we'll try to match on. De-duped, non-empty.

    Index 0 is the displayName preserved in its original case (used by the
    event API for the live score header). Subsequent entries are lower-cased
    for case-insensitive matching against Kalshi titles. Consumers that
    only care about matching should `.lower()` the entries themselves."""
    raw = (
        team.get("displayName"),
        team.get("shortDisplayName"),
        team.get("name"),
        team.get("abbreviation"),
    )
    seen: list[str] = []
    for i, r in enumerate(raw):
        if not r:
            continue
        v = str(r).strip() if i == 0 else str(r).strip().lower()
        if v and v not in seen:
            seen.append(v)
    return tuple(seen)


_STAT_KEYS = {
    "totalShots": "shots",
    "shotsOnTarget": "shots_on_target",
    "possessionPct": "possession_pct",
    "wonCorners": "corners",
    "foulsCommitted": "fouls",
}


def _parse_team_stats(competitor: dict[str, Any]) -> TeamStats:
    """Pull the in-game numeric stats off a competitor. Score comes from
    competitor.score (string); the rest live under competitor.statistics
    as {name, displayValue, abbreviation}."""
    raw_score = competitor.get("score")
    score: int | None
    try:
        score = int(raw_score) if raw_score is not None else None
    except (TypeError, ValueError):
        score = None

    kwargs: dict[str, Any] = {"score": score}
    for stat in competitor.get("statistics") or []:
        name = stat.get("name")
        key = _STAT_KEYS.get(name)
        if key is None:
            continue
        raw = stat.get("displayValue")
        if raw is None:
            continue
        try:
            kwargs[key] = float(raw) if "Pct" in name else int(float(raw))
        except (TypeError, ValueError):
            continue

    return TeamStats(**kwargs)


def _classify_detail(text: str) -> str:
    """Map ESPN's verbose `type.text` to one of our kinds."""
    t = (text or "").lower()
    if "red card" in t:
        return "red"
    if "yellow card" in t:
        return "yellow"
    if "goal" in t and "no goal" not in t:
        return "goal"
    return "other"


def _enrich_with_details(
    raw_details: list[dict[str, Any]],
    home_id: str | None,
    away_id: str | None,
    home_stats: TeamStats,
    away_stats: TeamStats,
) -> tuple[TeamStats, TeamStats, MatchEvent | None]:
    """Walk `competition.details` (in event order) to count yellow/red cards
    per side and to find the most recent display-worthy event. ESPN's score
    is already in the competitor block; we don't double-count goals here."""
    yellow = {"home": 0, "away": 0}
    red = {"home": 0, "away": 0}
    last: MatchEvent | None = None
    for d in raw_details:
        text = (d.get("type") or {}).get("text") or ""
        kind = _classify_detail(text)
        team_id = str((d.get("team") or {}).get("id") or "")
        side = (
            "home" if team_id == home_id
            else "away" if team_id == away_id
            else None
        )
        if kind == "yellow" and side is not None:
            yellow[side] += 1
        elif kind == "red" and side is not None:
            red[side] += 1

        if kind in ("goal", "yellow", "red"):
            athletes = d.get("athletesInvolved") or []
            player = athletes[0].get("displayName") if athletes else None
            clock = (d.get("clock") or {}).get("displayValue")
            last = MatchEvent(
                kind=kind,
                minute=clock,
                player=player,
                side=side,
                text=text,
            )

    home_out = TeamStats(
        score=home_stats.score,
        shots=home_stats.shots,
        shots_on_target=home_stats.shots_on_target,
        possession_pct=home_stats.possession_pct,
        corners=home_stats.corners,
        fouls=home_stats.fouls,
        yellow_cards=yellow["home"],
        red_cards=red["home"],
    )
    away_out = TeamStats(
        score=away_stats.score,
        shots=away_stats.shots,
        shots_on_target=away_stats.shots_on_target,
        possession_pct=away_stats.possession_pct,
        corners=away_stats.corners,
        fouls=away_stats.fouls,
        yellow_cards=yellow["away"],
        red_cards=red["away"],
    )
    return home_out, away_out, last


# === /summary enrichment: per-shot commentary stream + extra boxscore stats ===

# Quality stems, checked in order. ESPN's commentary is templated provider text
# (Stats Perform), so these are stable. Order matters and is deliberate:
# `goal!` is checked FIRST so a rebound goal ("Goal! ... after his shot hits the
# post") grades as a goal, not woodwork. A goal is ALWAYS prefixed "Goal!" in
# this feed — there is no loose "contains the word goal" fallback, because
# phrases like "shot towards goal" / "attempt on goal" are misses, not goals.
_QUALITY_PATTERNS: tuple[tuple[str, str], ...] = (
    ("goal", "goal!"),
    ("woodwork", "hits the bar"),
    ("woodwork", "hits the post"),
    ("woodwork", "hits the woodwork"),
    ("saved", "attempt saved"),
    ("missed", "attempt missed"),
    ("blocked", "attempt blocked"),
)

# The shooter's team is the FIRST parenthesized name on a shot line, e.g.
# "Marcel Sabitzer (Austria) ... saved ... by Chamakh (Tunisia)". Later parens
# (the keeper's team) and the "Goal! Austria 1, Tunisia 0" scoreline both name
# the other team, so matching "any team name in the line" mis-sides — we take
# the first parenthesized group only.
_FIRST_PAREN = re.compile(r"\(([^)]+)\)")

_INSIDE_BOX_PHRASES = (
    "inside the box",
    "centre of the box",
    "center of the box",
    "six yard box",
    "from close range",
    "from very close range",
)
_OUTSIDE_BOX_PHRASES = (
    "outside the box",
    "from long range",
    "from a long way out",
)

# A commentary line is shot-like if it carries one of these markers; non-shot
# lines (fouls, corners, substitutions) are skipped entirely.
_SHOT_MARKERS = ("attempt", "hits the bar", "hits the post", "hits the woodwork", "goal!")


def _classify_shot_quality(text_lower: str) -> str | None:
    """Map a commentary line to a shot quality, or None if it isn't a shot.
    Returns 'unknown' for a shot-like line whose quality we can't pin down —
    that still records the shot (raw-text floor), just ungraded."""
    for quality, stem in _QUALITY_PATTERNS:
        if stem in text_lower:
            return quality
    if any(m in text_lower for m in _SHOT_MARKERS):
        return "unknown"
    return None


def _classify_shot_location(text_lower: str) -> str | None:
    """Bucket a shot's location from the commentary, or None if not stated.
    Inside checked before outside is irrelevant (phrases are disjoint), but we
    keep inside first for readability."""
    if any(p in text_lower for p in _INSIDE_BOX_PHRASES):
        return "inside_box"
    if any(p in text_lower for p in _OUTSIDE_BOX_PHRASES):
        return "outside_box"
    return None


def _shot_side(text: str, home_names: tuple[str, ...], away_names: tuple[str, ...]) -> str | None:
    """Resolve which side took the shot from the FIRST parenthesized team on the
    line — that's the shooter's team ("Sabitzer (Austria) ... saved by Chamakh
    (Tunisia)"; "Goal! Austria 1, Tunisia 0. Sabitzer (Austria) ..."). Matching
    the whole first group against the name set (exact, case-insensitive) avoids
    both the scoreline trap (both teams named) and abbreviation substring
    collisions ('aut' inside 'authentic'). None when no team is parenthesized or
    it matches neither side."""
    m = _FIRST_PAREN.search(text)
    if m is None:
        return None
    paren = m.group(1).strip().lower()
    # Exact match against the carried names (home_names[0] is original case;
    # all are compared lowered). The parenthesized group is the bare team name,
    # so equality — not substring — is what we want.
    home = {n.lower() for n in home_names}
    away = {n.lower() for n in away_names}
    if paren in home:
        return "home"
    if paren in away:
        return "away"
    return None


def _parse_commentary_shots(
    commentary: list[dict[str, Any]],
    home_names: tuple[str, ...],
    away_names: tuple[str, ...],
) -> tuple[ShotEvent, ...]:
    """Walk /summary `commentary` for shot events. Each shot keeps its raw text
    no matter what; an unrecognized shot phrasing is logged at debug so the
    pattern set can be tightened over real games (self-reporting gaps)."""
    out: list[ShotEvent] = []
    for item in commentary or []:
        text = item.get("text") or ""
        if not text:
            continue
        t = text.lower()
        quality = _classify_shot_quality(t)
        if quality is None:
            continue  # not a shot — foul, corner, sub, etc.
        if quality == "unknown":
            log.debug("espn_shot_unmatched_quality", text=text[:140])
        minute = (item.get("time") or {}).get("displayValue") or None
        out.append(ShotEvent(
            minute=minute,
            side=_shot_side(text, home_names, away_names),
            quality=quality,
            location=_classify_shot_location(t),
            raw_text=text,
        ))
    return tuple(out)


# Boxscore stat labels (the /summary boxscore uses human labels, unlike the
# /scoreboard statistics which use camelCase names). Only the ones we don't
# already get from /scoreboard.
_SUMMARY_STAT_LABELS = {
    "saves": "saves",
    "blocked shots": "blocked_shots",
    "penalty kicks taken": "penalty_kicks_taken",
    "penalty goals": "penalty_goals",
}


def _parse_summary_boxscore_side(stats: list[dict[str, Any]]) -> dict[str, int]:
    """Pull the extra stats (saves, blocked, penalties) off one boxscore team's
    statistics list. Labels are matched case-insensitively; unknown labels are
    ignored (we only want the four we don't already have)."""
    out: dict[str, int] = {}
    for s in stats or []:
        label = str(s.get("label") or s.get("name") or "").strip().lower()
        key = _SUMMARY_STAT_LABELS.get(label)
        if key is None:
            continue
        raw = s.get("displayValue", s.get("value"))
        if raw is None:
            continue
        try:
            out[key] = int(float(raw))
        except (TypeError, ValueError):
            continue
    return out


_CARRY_FORWARD_FIELDS = ("saves", "blocked_shots", "penalty_kicks_taken", "penalty_goals")
"""Boxscore extras (from /summary, not /scoreboard) that carry forward across
polls when ESPN ships an empty boxscore. Monotonic counts — a stale-low carried
value is harmless; a flickering null is not."""


def _carry_forward(fresh: TeamStats, prev: TeamStats | None) -> TeamStats:
    """Seed `fresh` (rebuilt from /scoreboard, so the extras are None) with the
    previous poll's boxscore extras, so they don't flicker to null on a poll
    where ESPN's /summary boxscore is empty. A later non-empty poll overwrites."""
    if prev is None:
        return fresh
    carry = {
        f: getattr(prev, f)
        for f in _CARRY_FORWARD_FIELDS
        if getattr(fresh, f) is None and getattr(prev, f) is not None
    }
    return replace(fresh, **carry) if carry else fresh


def _enrich_with_summary(
    event: EspnEvent, payload: dict[str, Any], prev: EspnEvent | None = None,
) -> EspnEvent:
    """Attach the /summary-derived shot stream + extra boxscore stats to an
    EspnEvent (frozen, so we build a new one via replace). Boxscore extras carry
    forward from `prev` when this poll's are empty (ESPN ships the boxscore
    intermittently). Team order in ESPN's payload is [home, away] for soccer, but
    we map by the homeAway flag, not index. Never raises."""
    shots = _parse_commentary_shots(
        payload.get("commentary") or [],
        event.home_names,
        event.away_names,
    )

    # Start from last poll's extras so an empty boxscore this cycle keeps them.
    home_stats = _carry_forward(event.home_stats, prev.home_stats if prev else None)
    away_stats = _carry_forward(event.away_stats, prev.away_stats if prev else None)
    for team in (payload.get("boxscore") or {}).get("teams") or []:
        extra = _parse_summary_boxscore_side(team.get("statistics") or [])
        if not extra:
            continue
        # Match by the team's own homeAway flag rather than list position —
        # the payload carries it and it's more robust than trusting index order.
        if team.get("homeAway") == "home":
            home_stats = replace(home_stats, **extra)
        elif team.get("homeAway") == "away":
            away_stats = replace(away_stats, **extra)

    return replace(event, shots=shots, home_stats=home_stats, away_stats=away_stats)


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
    comp = comps[0]
    teams = comp.get("competitors", [])
    home = next((t for t in teams if t.get("homeAway") == "home"), None)
    away = next((t for t in teams if t.get("homeAway") == "away"), None)
    if home is None or away is None:
        return None

    home_stats_raw = _parse_team_stats(home)
    away_stats_raw = _parse_team_stats(away)
    home_id = str((home.get("team") or {}).get("id") or "")
    away_id = str((away.get("team") or {}).get("id") or "")
    home_stats, away_stats, last_event = _enrich_with_details(
        comp.get("details") or [],
        home_id=home_id,
        away_id=away_id,
        home_stats=home_stats_raw,
        away_stats=away_stats_raw,
    )

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
        home_stats=home_stats,
        away_stats=away_stats,
        last_event=last_event,
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
        self._task: asyncio.Task[None] | None = None
        self._burst_until: float = 0.0
        """Monotonic deadline; while now < this, poll at the burst cadence.
        Set by request_burst() when a watched market spikes."""

    def request_burst(self) -> None:
        """Tighten the poll cadence to ~10s for BURST_WINDOW_S. Called when a
        watched market just moved sharply (likely a goal / red card) so the
        /summary detail lands within ~10s instead of ~40s. Idempotent — a fresh
        spike just re-extends the window; overlapping spikes don't stack."""
        self._burst_until = time.monotonic() + BURST_WINDOW_S

    @property
    def _bursting(self) -> bool:
        return time.monotonic() < self._burst_until

    async def run(self) -> None:
        """Long-running poller. Initial fetch on start, then adaptive cadence:
        30 min idle, 40s when a game is live, 10s for ~75s after a watched
        market spikes (event-burst — see request_burst)."""
        await self._refresh_once()
        while not self._stopped:
            live_now = any(e.state == "in" for e in self.snapshot.events)
            if live_now and self._bursting:
                interval = POLL_INTERVAL_BURST_S
            elif live_now:
                interval = POLL_INTERVAL_LIVE_S
            else:
                interval = POLL_INTERVAL_IDLE_S
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
        for ev in events:
            deduped[ev.espn_id] = ev

        # Enrich live games with the richer /summary feed (per-shot stream +
        # extra boxscore stats). Live-only: the bounded set, on the same 40s
        # cadence this refresh already runs at. One game's summary failing is
        # swallowed — the scoreboard data for every game still stands.
        #
        # Each refresh rebuilds events from /scoreboard (no boxscore extras), so
        # we pass the PREVIOUS snapshot's event in: ESPN's /summary boxscore is
        # intermittent (present some polls, empty others, per game), and without
        # carry-forward the saves/blocks/penalty counts would flicker to null
        # every time a poll catches an empty boxscore. These are monotonic
        # counts, so a carried value can only be stale-low by a poll — never
        # misleading.
        prev_by_id = {e.espn_id: e for e in self.snapshot.events}
        live = [(eid, ev) for eid, ev in deduped.items() if ev.state == "in"]
        # Enrich live games CONCURRENTLY. Serially, N games × the per-request
        # timeout could blow past the live poll cadence during simultaneous WC
        # kickoffs — freezing score/clock for every game exactly when the most
        # are live. gather bounds the wall time to the slowest single summary,
        # not their sum. Per-game failures are isolated (each task swallows its
        # own error and returns the un-enriched event), so one slow/failed
        # summary never drops another game's scoreboard data.
        if live:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S) as client:
                enriched = await asyncio.gather(*(
                    self._fetch_summary_safe(client, ev, prev_by_id.get(eid))
                    for eid, ev in live
                ))
            for (eid, _), ev in zip(live, enriched):
                deduped[eid] = ev

        self.snapshot = EspnSnapshot(
            events=list(deduped.values()),
            refreshed_at=now,
        )
        log.info("espn_refreshed", slugs=len(self._slugs), events=len(deduped))

    async def _fetch_summary_safe(
        self, client: httpx.AsyncClient, event: EspnEvent, prev: EspnEvent | None = None,
    ) -> EspnEvent:
        """_fetch_summary with per-game error isolation, for concurrent gather:
        a transport error on one game's summary returns that game un-enriched
        rather than failing the whole batch."""
        try:
            return await self._fetch_summary(client, event, prev)
        except Exception as e:  # noqa: BLE001
            log.warning(
                "espn_summary_failed", espn_id=event.espn_id, slug=event.slug,
                error=str(e)[:120],
            )
            return event

    async def _fetch_summary(
        self, client: httpx.AsyncClient, event: EspnEvent, prev: EspnEvent | None = None,
    ) -> EspnEvent:
        """Fetch /summary for one live game and return the event enriched with
        its shot stream + extra boxscore stats. `prev` is last poll's version of
        the same game — its boxscore extras carry forward when this poll's are
        empty. On a non-200, returns the event unchanged (caller wraps transport
        errors)."""
        url = f"{ESPN_BASE}/{event.slug}/summary?event={event.espn_id}"
        r = await client.get(url)
        if r.status_code != 200:
            return event
        return _enrich_with_summary(event, r.json(), prev)

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


def _day_offset(d: int) -> timedelta:
    return timedelta(days=d)
