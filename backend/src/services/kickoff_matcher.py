"""Match Kalshi events to ESPN events to get true kickoff times.

Kalshi event titles are "Home vs Away" strings. ESPN events carry
{home_names, away_names} tuples (display, short, abbreviation, lower-cased).
We match on (date, home/away team names) within a fuzz tolerance.

The fuzz tolerance is deliberately tight: a *miss* is fine (falls back
to Kalshi's occurrence_datetime), but a *wrong match* would silently put
the wrong kickoff on a market. So we err toward returning None.

Rules:
  - Date must match (compare YYYY-MM-DD of the Kalshi event's ticker date
    against the ESPN event's local date in the same time zone — we use
    UTC since the ticker is encoded in UTC date too).
  - The two Kalshi teams must match ESPN's two teams (each shares a token
    with one ESPN side) in EITHER orientation. Home/away is NOT reliable
    across the two sources — friendlies in particular disagree (Kalshi
    ESP-PER vs ESPN "ESP @ PER") — so direction must not gate the match;
    same teams + same league + same date is the high-confidence signal.

Ported from V2's kalshi_market_service.py team-alias logic.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timezone

from src.core.logging import get_logger
from src.ingestion.espn_scoreboard import EspnEvent, EspnSnapshot

log = get_logger(__name__)


# Common suffixes/prefixes that vary between sources and shouldn't matter.
_DROP_TOKENS = frozenset({
    "fc", "cf", "ac", "afc", "sc", "sk", "ec", "ca", "sv", "rc",
    "de", "del", "do", "da", "la", "le", "el", "los",
    "club", "clube", "city", "united", "utd",
})


def _strip_accents(s: str) -> str:
    """José → Jose. ESPN and Kalshi disagree on accents constantly."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", s)
        if not unicodedata.combining(c)
    )


def _tokens(name: str) -> frozenset[str]:
    """Return the meaningful tokens of a team name, lower-cased, deaccented,
    with common stop-words (fc, club, de, ...) dropped. Used as the unit
    of comparison — a Kalshi team matches an ESPN team if their token sets
    overlap by at least one non-trivial token."""
    s = _strip_accents(name.lower())
    # Replace non-letters with spaces; keeps unicode letters via \w fallback.
    s = re.sub(r"[^\w\s]+", " ", s, flags=re.UNICODE)
    raw = s.split()
    return frozenset(t for t in raw if t and t not in _DROP_TOKENS and len(t) > 1)


def _names_match(kalshi_team: str, espn_team_names: tuple[str, ...]) -> bool:
    """True if any of ESPN's name variants for this team shares at least
    one non-trivial token with the Kalshi team string."""
    k_toks = _tokens(kalshi_team)
    if not k_toks:
        return False
    for espn_name in espn_team_names:
        e_toks = _tokens(espn_name)
        if k_toks & e_toks:
            return True
    return False


def _parse_kalshi_event_title(title: str) -> tuple[str, str] | None:
    """'Santos vs Deportivo Cuenca' → ('Santos', 'Deportivo Cuenca').
    Returns None if the title isn't in the expected form."""
    # Kalshi uses ' vs ' with single spaces. Defensive on case.
    parts = re.split(r"\s+vs\s+", title, maxsplit=1, flags=re.IGNORECASE)
    if len(parts) != 2:
        return None
    home, away = parts[0].strip(), parts[1].strip()
    if not home or not away:
        return None
    return home, away


# Kalshi ticker date encoding: -YYMONDD before the team codes.
# Matches the parser in market_discovery.py.
_TICKER_DATE_RE = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})[A-Z]", re.ASCII)
_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


def _ticker_date(ticker_or_event: str) -> date | None:
    """Date encoded in the ticker (e.g. 26MAY27 → 2026-05-27)."""
    m = _TICKER_DATE_RE.search(ticker_or_event)
    if m is None:
        return None
    yy, mon, dd = m.group(1), m.group(2), m.group(3)
    month = _MONTHS.get(mon)
    if month is None:
        return None
    try:
        return date(2000 + int(yy), month, int(dd))
    except ValueError:
        return None


def find_match(
    snapshot: EspnSnapshot,
    *,
    event_ticker: str,
    event_title: str,
    espn_slug: str | None,
) -> EspnEvent | None:
    """Best ESPN match for a Kalshi event. Returns the full EspnEvent or
    None if no high-confidence match exists. Use .kickoff_utc for kickoff
    time, .state/.period/.clock_display/.status_detail for live game info.

    `espn_slug` narrows the candidate set to ESPN events from the same
    league — same Kalshi series → same ESPN slug. Skipping this would
    invite cross-league matches on common names (e.g. 'River' in Argentina
    vs in Uruguay).
    """
    if not snapshot.events or espn_slug is None:
        return None

    parsed = _parse_kalshi_event_title(event_title)
    if parsed is None:
        return None
    k_home, k_away = parsed

    target_date = _ticker_date(event_ticker)

    candidates = []
    for e in snapshot.events:
        if e.slug != espn_slug:
            continue
        # Date filter when we have one — ticker date is UTC, ESPN kickoff
        # is UTC, so compare in UTC. Allow ±1 day because late-night games
        # straddle the date boundary in Kalshi's ticker encoding.
        if target_date is not None:
            espn_date = e.kickoff_utc.date()
            if abs((espn_date - target_date).days) > 1:
                continue
        # Team match in EITHER orientation. Kalshi and ESPN don't agree on
        # home/away for every fixture — friendlies especially (ESP-PER: Kalshi
        # lists Spain-Peru, ESPN lists Peru as host, "ESP @ PER"). Same two
        # teams + same league (espn_slug) + same date is already high
        # confidence, so the direction must not gate the match — requiring
        # Kalshi-home == ESPN-home silently dropped every flipped fixture, which
        # then fell back to Kalshi's unreliable proxy kickoff time.
        forward = _names_match(k_home, e.home_names) and _names_match(k_away, e.away_names)
        reverse = _names_match(k_home, e.away_names) and _names_match(k_away, e.home_names)
        if forward or reverse:
            candidates.append(e)

    if not candidates:
        return None

    # Multiple candidates after filtering would mean ESPN has two same-
    # league, same-day, same-teams games — pathological but possible if
    # we matched too loosely. Pick the one nearest in time to the ticker
    # date so we err toward the right session.
    if len(candidates) > 1 and target_date is not None:
        candidates.sort(
            key=lambda e: abs(
                (e.kickoff_utc.date() - target_date).total_seconds()
            )
        )
    return candidates[0]


# Back-compat alias for the previous name.
find_kickoff = find_match
