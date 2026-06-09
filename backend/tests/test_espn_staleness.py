"""ESPN poll-loop staleness helpers — the signals the supervisor watchdog reads
to detect a wedged/dead poller and respawn it.

The watchdog itself needs a live Supervisor (many deps), so these cover the
pure helpers its correctness rests on: refresh age, live-game detection, and the
stop/resume flag. See _espn_staleness_watchdog in supervisor.py.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.ingestion.espn_scoreboard import (
    KICKOFF_IMMINENT_LAG_S,
    KICKOFF_IMMINENT_LEAD_S,
    EspnEvent,
    EspnScoreboard,
    EspnSnapshot,
)


def _event(
    espn_id: str, state: str, kickoff_utc: datetime | None = None
) -> EspnEvent:
    return EspnEvent(
        espn_id=espn_id,
        slug="fifa.friendly",
        kickoff_utc=kickoff_utc or datetime(2026, 6, 8, 19, 0, tzinfo=timezone.utc),
        state=state,
        period=1 if state == "in" else None,
        clock_display="16'" if state == "in" else None,
        status_detail=None,
        home_names=("Netherlands", "netherlands", "ned"),
        away_names=("Uzbekistan", "uzbekistan", "uzb"),
    )


def _pre_kicking_off_in(seconds: float) -> EspnEvent:
    """A 'pre' event whose scheduled kickoff is `seconds` from now (negative =
    kickoff already passed but the snapshot still shows it pre)."""
    return _event(
        "k", "pre", datetime.now(timezone.utc) + timedelta(seconds=seconds)
    )


def test_seconds_since_refresh_none_before_first_refresh():
    sb = EspnScoreboard(slugs=["fifa.friendly"])
    assert sb.seconds_since_refresh() is None


def test_seconds_since_refresh_grows_with_age():
    sb = EspnScoreboard(slugs=["fifa.friendly"])
    sb.snapshot = EspnSnapshot(
        events=[],
        refreshed_at=datetime.now(timezone.utc) - timedelta(seconds=120),
    )
    age = sb.seconds_since_refresh()
    assert age is not None and 115 < age < 130


def test_has_live_games():
    sb = EspnScoreboard(slugs=["fifa.friendly"])
    assert sb.has_live_games is False
    sb.snapshot = EspnSnapshot(events=[_event("1", "pre"), _event("2", "post")])
    assert sb.has_live_games is False
    sb.snapshot = EspnSnapshot(events=[_event("1", "pre"), _event("2", "in")])
    assert sb.has_live_games is True


def test_kickoff_imminent_window():
    sb = EspnScoreboard(slugs=["fifa.friendly"])
    # No events: not imminent.
    assert sb.kickoff_imminent is False
    # A pre game far out (beyond the lead window): not imminent.
    sb.snapshot = EspnSnapshot(events=[_pre_kicking_off_in(KICKOFF_IMMINENT_LEAD_S + 600)])
    assert sb.kickoff_imminent is False
    # Inside the lead window (about to start): imminent.
    sb.snapshot = EspnSnapshot(events=[_pre_kicking_off_in(KICKOFF_IMMINENT_LEAD_S - 60)])
    assert sb.kickoff_imminent is True
    # Kickoff passed but snapshot still 'pre' (the Armenia case): imminent until
    # the lag bound.
    sb.snapshot = EspnSnapshot(events=[_pre_kicking_off_in(-300)])
    assert sb.kickoff_imminent is True
    # Far past the lag bound (stale/mislabeled fixture): not imminent — the
    # window is bounded so it can't pin the poller fast forever.
    sb.snapshot = EspnSnapshot(events=[_pre_kicking_off_in(-(KICKOFF_IMMINENT_LAG_S + 600))])
    assert sb.kickoff_imminent is False


def test_kickoff_imminent_ignores_non_pre_states():
    # An 'in' game drives has_live_games, not kickoff_imminent; a 'post' game in
    # its kickoff window must not count as imminent.
    sb = EspnScoreboard(slugs=["fifa.friendly"])
    now = datetime.now(timezone.utc)
    sb.snapshot = EspnSnapshot(events=[_event("p", "post", now - timedelta(seconds=300))])
    assert sb.kickoff_imminent is False


def test_stop_resume_flag():
    sb = EspnScoreboard(slugs=["fifa.friendly"])
    assert sb.is_stopped is False
    sb._stopped = True
    assert sb.is_stopped is True
    sb.resume()
    assert sb.is_stopped is False
