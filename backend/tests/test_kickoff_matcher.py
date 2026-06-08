"""ESPN kickoff/state matcher — links a Kalshi event to its ESPN fixture so the
app uses ESPN's authoritative kickoff time (not Kalshi's unreliable proxy).

The match keys on league (slug) + date + both teams present, in EITHER
orientation: Kalshi and ESPN don't agree on home/away for every fixture, so
requiring same-direction silently dropped flipped games (they then showed
Kalshi's wrong proxy time — the ESP-PER bug).
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.ingestion.espn_scoreboard import EspnEvent, EspnSnapshot
from src.services.kickoff_matcher import find_match

SLUG = "fifa.friendly"


def _espn(espn_id: str, home: tuple[str, ...], away: tuple[str, ...],
          kickoff: datetime) -> EspnEvent:
    return EspnEvent(
        espn_id=espn_id, slug=SLUG, kickoff_utc=kickoff, state="pre",
        period=None, clock_display=None, status_detail=None,
        home_names=home, away_names=away,
    )


def _snap(*events: EspnEvent) -> EspnSnapshot:
    return EspnSnapshot(events=list(events), refreshed_at=datetime.now(timezone.utc))


def test_matches_same_orientation():
    espn = _espn("1", ("Spain", "ESP"), ("Peru", "PER"),
                 datetime(2026, 6, 9, 2, 0, tzinfo=timezone.utc))
    m = find_match(
        _snap(espn),
        event_ticker="KXINTLFRIENDLYGAME-26JUN08ESPPER",
        event_title="Spain vs Peru",
        espn_slug=SLUG,
    )
    assert m is not None and m.espn_id == "1"


def test_matches_reversed_orientation():
    # The ESP-PER bug: Kalshi lists Spain-Peru, ESPN lists Peru as host
    # ("ESP @ PER" → home=Peru, away=Spain). Same teams, flipped home/away —
    # must still match so the real ESPN kickoff (02:00Z) is used, not Kalshi's
    # 05:00Z proxy.
    espn = _espn("2", ("Peru", "PER"), ("Spain", "ESP"),
                 datetime(2026, 6, 9, 2, 0, tzinfo=timezone.utc))
    m = find_match(
        _snap(espn),
        event_ticker="KXINTLFRIENDLYGAME-26JUN08ESPPER",
        event_title="Spain vs Peru",
        espn_slug=SLUG,
    )
    assert m is not None and m.espn_id == "2"


def test_different_teams_no_match():
    espn = _espn("3", ("France", "FRA"), ("Germany", "GER"),
                 datetime(2026, 6, 9, 2, 0, tzinfo=timezone.utc))
    m = find_match(
        _snap(espn),
        event_ticker="KXINTLFRIENDLYGAME-26JUN08ESPPER",
        event_title="Spain vs Peru",
        espn_slug=SLUG,
    )
    assert m is None


def test_wrong_slug_no_match():
    espn = _espn("4", ("Spain", "ESP"), ("Peru", "PER"),
                 datetime(2026, 6, 9, 2, 0, tzinfo=timezone.utc))
    # An ESPN event from another league must not match even with the same teams.
    espn = EspnEvent(
        espn_id="4", slug="esp.1", kickoff_utc=espn.kickoff_utc, state="pre",
        period=None, clock_display=None, status_detail=None,
        home_names=("Spain", "ESP"), away_names=("Peru", "PER"),
    )
    m = find_match(
        _snap(espn),
        event_ticker="KXINTLFRIENDLYGAME-26JUN08ESPPER",
        event_title="Spain vs Peru",
        espn_slug=SLUG,
    )
    assert m is None


def test_date_too_far_no_match():
    # A same-teams fixture a week away isn't this game.
    espn = _espn("5", ("Spain", "ESP"), ("Peru", "PER"),
                 datetime(2026, 6, 15, 2, 0, tzinfo=timezone.utc))
    m = find_match(
        _snap(espn),
        event_ticker="KXINTLFRIENDLYGAME-26JUN08ESPPER",
        event_title="Spain vs Peru",
        espn_slug=SLUG,
    )
    assert m is None
