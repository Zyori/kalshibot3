"""ESPN poll-loop staleness helpers — the signals the supervisor watchdog reads
to detect a wedged/dead poller and respawn it.

The watchdog itself needs a live Supervisor (many deps), so these cover the
pure helpers its correctness rests on: refresh age, live-game detection, and the
stop/resume flag. See _espn_staleness_watchdog in supervisor.py.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.ingestion.espn_scoreboard import (
    EspnEvent,
    EspnScoreboard,
    EspnSnapshot,
)


def _event(espn_id: str, state: str) -> EspnEvent:
    return EspnEvent(
        espn_id=espn_id,
        slug="fifa.friendly",
        kickoff_utc=datetime(2026, 6, 8, 19, 0, tzinfo=timezone.utc),
        state=state,
        period=1 if state == "in" else None,
        clock_display="16'" if state == "in" else None,
        status_detail=None,
        home_names=("Netherlands", "netherlands", "ned"),
        away_names=("Uzbekistan", "uzbekistan", "uzb"),
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


def test_stop_resume_flag():
    sb = EspnScoreboard(slugs=["fifa.friendly"])
    assert sb.is_stopped is False
    sb._stopped = True
    assert sb.is_stopped is True
    sb.resume()
    assert sb.is_stopped is False
