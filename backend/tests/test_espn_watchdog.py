"""ESPN poll-loop watchdog — detect-and-respawn logic.

The watchdog backstops a wedged/dead ESPN poll loop (the snapshot stops
advancing while the process stays alive — observed 2026-06-08). The decision
(`_espn_should_respawn`) and the action (`_respawn_espn_task`) are tested
directly, bound to a minimal stand-in, so the logic is verified without
constructing a full Supervisor or waiting on the watchdog's sleep loop.

The respawn correctness that matters: a wedged task is cancelled and replaced by
exactly ONE fresh task (no duplicate poll loops leaking), the scoreboard's
stopped flag is cleared so the new run() doesn't exit immediately, and a
deliberate shutdown is never fought.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.supervisor import (
    ESPN_STALE_IDLE_S,
    ESPN_STALE_LIVE_S,
    Supervisor,
)


class FakeScoreboard:
    """Stand-in for EspnScoreboard exposing only what the watchdog reads/calls.
    `run()` is a cancellable sleep — a real asyncio task, no network."""

    def __init__(
        self,
        *,
        age: float | None,
        live: bool,
        stopped: bool = False,
        kickoff_imminent: bool = False,
    ) -> None:
        self._age = age
        self._live = live
        self._stopped = stopped
        self._kickoff_imminent = kickoff_imminent
        self.resume_called = 0
        self.run_started = 0

    def seconds_since_refresh(self) -> float | None:
        return self._age

    @property
    def has_live_games(self) -> bool:
        return self._live

    @property
    def kickoff_imminent(self) -> bool:
        return self._kickoff_imminent

    @property
    def is_stopped(self) -> bool:
        return self._stopped

    def resume(self) -> None:
        self.resume_called += 1
        self._stopped = False

    async def run(self) -> None:
        self.run_started += 1
        await asyncio.sleep(3600)  # cancellable; the test cancels it


def _sup(scoreboard: FakeScoreboard, espn_task: asyncio.Task | None):
    """A minimal object carrying the three attributes the watchdog methods
    touch, with the real Supervisor methods bound to it."""
    obj = SimpleNamespace(
        espn_scoreboard=scoreboard,
        _espn_task=espn_task,
        _tasks=[espn_task] if espn_task is not None else [],
    )
    obj._espn_should_respawn = Supervisor._espn_should_respawn.__get__(obj)
    obj._respawn_espn_task = Supervisor._respawn_espn_task.__get__(obj)
    return obj


# === detection: _espn_should_respawn ===

def test_no_respawn_when_fresh_and_live():
    sb = FakeScoreboard(age=10.0, live=True)  # 10s < live threshold
    sup = _sup(sb, None)
    assert sup._espn_should_respawn() is False


def test_respawn_when_stale_during_live_games():
    sb = FakeScoreboard(age=ESPN_STALE_LIVE_S + 1, live=True)
    sup = _sup(sb, None)
    assert sup._espn_should_respawn() is True


def test_no_respawn_when_idle_gap_under_idle_threshold():
    # A long gap that's fine when idle (between the live and idle thresholds)
    # must NOT trigger — the loop legitimately sleeps 30 min when no game is on.
    sb = FakeScoreboard(age=ESPN_STALE_LIVE_S + 60, live=False)
    sup = _sup(sb, None)
    assert sup._espn_should_respawn() is False


def test_respawn_when_idle_gap_exceeds_idle_threshold():
    sb = FakeScoreboard(age=ESPN_STALE_IDLE_S + 1, live=False)
    sup = _sup(sb, None)
    assert sup._espn_should_respawn() is True


def test_respawn_when_kickoff_imminent_and_stale_past_live_threshold():
    # The Armenia case: no game shows 'in' yet (the poller slept through the
    # kickoff with a pre-kickoff snapshot), but a kickoff is imminent and the
    # snapshot is stale past the LIVE threshold. The kickoff_imminent arm must
    # apply the tight bound and respawn — not wait for the 40-min idle bound.
    sb = FakeScoreboard(age=ESPN_STALE_LIVE_S + 60, live=False, kickoff_imminent=True)
    sup = _sup(sb, None)
    assert sup._espn_should_respawn() is True


def test_no_respawn_when_kickoff_imminent_but_fresh():
    # Imminent kickoff but the poller is refreshing fast (fresh snapshot) — the
    # tight bound applies, the gap is under it, so no respawn.
    sb = FakeScoreboard(age=10.0, live=False, kickoff_imminent=True)
    sup = _sup(sb, None)
    assert sup._espn_should_respawn() is False


def test_no_respawn_before_first_refresh():
    # age None = never refreshed yet (just started); not a fault.
    sb = FakeScoreboard(age=None, live=True)
    sup = _sup(sb, None)
    assert sup._espn_should_respawn() is False


@pytest.mark.asyncio
async def test_respawn_when_task_died_even_if_snapshot_fresh():
    # A task that raised and exited is a fault regardless of snapshot age.
    async def boom() -> None:
        raise RuntimeError("poll loop crashed")

    dead = asyncio.create_task(boom())
    await asyncio.sleep(0)  # let it run and finish
    assert dead.done()
    sb = FakeScoreboard(age=5.0, live=True)  # snapshot looks fresh
    sup = _sup(sb, dead)
    assert sup._espn_should_respawn() is True
    dead.exception()  # retrieve to silence the loop warning


# === action: _respawn_espn_task ===

@pytest.mark.asyncio
async def test_respawn_replaces_with_exactly_one_fresh_task():
    # An old task that's wedged (sleeping forever).
    old = asyncio.create_task(asyncio.sleep(3600))
    sb = FakeScoreboard(age=ESPN_STALE_LIVE_S + 1, live=True)
    sup = _sup(sb, old)

    sup._respawn_espn_task()
    await asyncio.sleep(0)  # let the freshly-created task reach its first line

    # Old task cancelled, removed from tracking; a single new task tracked.
    assert old.cancelled() or old.cancelling()
    assert sup._espn_task is not old
    assert sup._espn_task is not None
    assert sb.run_started == 1
    assert sb.resume_called == 1  # stopped flag cleared before the new run()
    # Exactly one ESPN task tracked — no duplicate poll loop leaked.
    assert sup._tasks.count(sup._espn_task) == 1
    assert old not in sup._tasks

    sup._espn_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await sup._espn_task


@pytest.mark.asyncio
async def test_respawn_from_dead_task_starts_fresh():
    async def boom() -> None:
        raise RuntimeError("crashed")

    dead = asyncio.create_task(boom())
    await asyncio.sleep(0)  # let it raise; respawn retrieves the exception
    sb = FakeScoreboard(age=None, live=True)
    sup = _sup(sb, dead)

    sup._respawn_espn_task()
    await asyncio.sleep(0)  # let the freshly-created task reach its first line

    assert sb.run_started == 1
    assert sup._espn_task is not dead
    assert sup._tasks == [sup._espn_task]
    # The crashed task's exception was retrieved by respawn (no asyncio
    # "exception never retrieved" warning leaks).
    assert dead.exception() is not None

    sup._espn_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await sup._espn_task
