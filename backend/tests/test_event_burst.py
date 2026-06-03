"""Event-burst polling: a sharp market-mid jump (likely a goal/red card) tightens
the ESPN poll cadence so the /summary detail lands fast. Two units under test:

  - EspnScoreboard.request_burst / _bursting — the burst window mechanism.
  - Supervisor._detect_market_spikes / _ticker_spiked — the spike detector that
    fires it, including threshold, slow-drift rejection, cooldown, and per-event
    dedupe.

The detector is exercised on a real Supervisor (it constructs bare) with its
price_history hand-seeded and request_burst spied — no WS or network.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from src.ingestion.espn_scoreboard import BURST_WINDOW_S, EspnScoreboard
from src.ingestion.market_discovery import FeedMarket
from src.supervisor import (
    BURST_COOLDOWN_S,
    BURST_SPIKE_LOOKBACK_S,
    BURST_SPIKE_THRESHOLD_CENTS,
    Supervisor,
)


def _market(ticker: str, event_ticker: str) -> FeedMarket:
    """A minimal live FeedMarket — only the fields the spike detector reads
    (ticker, event_ticker) carry meaning; the rest are filler."""
    return FeedMarket(
        ticker=ticker,
        event_ticker=event_ticker,
        event_title="A vs B",
        market_title="A vs B Winner?",
        yes_sub_title="A",
        series="KXWCGAME",
        status="active",
        open_time=datetime(2026, 6, 11, tzinfo=timezone.utc),
        close_time=None,
        volume=None,
        bucket="live",
    )


# === burst window mechanism (EspnScoreboard) ===


def test_burst_off_by_default() -> None:
    sb = EspnScoreboard(slugs=["fifa.world"])
    assert sb._bursting is False


def test_request_burst_turns_on_then_decays(monkeypatch: pytest.MonkeyPatch) -> None:
    sb = EspnScoreboard(slugs=["fifa.world"])
    clock = {"t": 1000.0}
    monkeypatch.setattr(time, "monotonic", lambda: clock["t"])

    sb.request_burst()
    assert sb._bursting is True

    # Still hot just before the window closes.
    clock["t"] = 1000.0 + BURST_WINDOW_S - 1
    assert sb._bursting is True

    # Cold once the window passes.
    clock["t"] = 1000.0 + BURST_WINDOW_S + 1
    assert sb._bursting is False


def test_request_burst_re_extends_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second spike mid-window pushes the deadline out — it doesn't stack, it
    re-extends from now."""
    sb = EspnScoreboard(slugs=["fifa.world"])
    clock = {"t": 0.0}
    monkeypatch.setattr(time, "monotonic", lambda: clock["t"])

    sb.request_burst()
    clock["t"] = BURST_WINDOW_S - 5  # near the end
    sb.request_burst()  # re-arm
    clock["t"] = BURST_WINDOW_S + 10  # past the FIRST deadline...
    assert sb._bursting is True  # ...but inside the re-extended one


# === spike detector (Supervisor) ===


def _seed_series(sup: Supervisor, ticker: str, samples: list[tuple[float, int]]) -> None:
    """Inject a raw (monotonic_ts, mid_cents) series for a ticker."""
    from collections import deque

    sup.price_history._series[ticker] = deque(samples, maxlen=64)


def _spy_burst(sup: Supervisor) -> list[int]:
    calls: list[int] = []
    sup.espn_scoreboard.request_burst = lambda: calls.append(1)  # type: ignore[method-assign]
    return calls


def test_sharp_jump_fires_burst(monkeypatch: pytest.MonkeyPatch) -> None:
    sup = Supervisor()
    calls = _spy_burst(sup)
    now = 5000.0
    monkeypatch.setattr(time, "monotonic", lambda: now)
    # 20¢ jump within the lookback window → fires.
    _seed_series(sup, "MKT-A", [(now - 20, 30), (now, 50)])

    sup._detect_market_spikes([_market("MKT-A", "EVT-1")])
    assert calls == [1]


def test_small_move_does_not_fire(monkeypatch: pytest.MonkeyPatch) -> None:
    sup = Supervisor()
    calls = _spy_burst(sup)
    now = 5000.0
    monkeypatch.setattr(time, "monotonic", lambda: now)
    # 4¢ move (< threshold) → normal book churn, no burst.
    assert BURST_SPIKE_THRESHOLD_CENTS > 4
    _seed_series(sup, "MKT-A", [(now - 20, 30), (now, 34)])

    sup._detect_market_spikes([_market("MKT-A", "EVT-1")])
    assert calls == []


def test_slow_drift_does_not_fire(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 10¢ move spread across minutes is the game evolving, not an event — the
    sample old enough to span it is outside the lookback window, so the in-window
    comparison sees only a small step."""
    sup = Supervisor()
    calls = _spy_burst(sup)
    now = 5000.0
    monkeypatch.setattr(time, "monotonic", lambda: now)
    old = now - BURST_SPIKE_LOOKBACK_S - 30  # outside lookback
    _seed_series(
        sup,
        "MKT-A",
        [
            (old, 30),
            (now - 5, 39),  # within-lookback baseline...
            (now, 40),  # ...latest only 1¢ above it
        ],
    )

    sup._detect_market_spikes([_market("MKT-A", "EVT-1")])
    assert calls == []


def test_cooldown_suppresses_repeat(monkeypatch: pytest.MonkeyPatch) -> None:
    sup = Supervisor()
    calls = _spy_burst(sup)
    now = {"t": 5000.0}
    monkeypatch.setattr(time, "monotonic", lambda: now["t"])
    _seed_series(sup, "MKT-A", [(now["t"] - 20, 30), (now["t"], 50)])

    sup._detect_market_spikes([_market("MKT-A", "EVT-1")])
    assert calls == [1]

    # Same spike one tick later, still inside cooldown → suppressed.
    now["t"] += BURST_COOLDOWN_S - 10
    _seed_series(sup, "MKT-A", [(now["t"] - 20, 30), (now["t"], 50)])
    sup._detect_market_spikes([_market("MKT-A", "EVT-1")])
    assert calls == [1]

    # Past cooldown → fires again.
    now["t"] += 20
    _seed_series(sup, "MKT-A", [(now["t"] - 20, 30), (now["t"], 50)])
    sup._detect_market_spikes([_market("MKT-A", "EVT-1")])
    assert calls == [1, 1]


def test_two_sides_of_one_event_burst_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both moneyline sides of a match spike on a goal; dedupe by event_ticker
    means one burst, not two."""
    sup = Supervisor()
    calls = _spy_burst(sup)
    now = 5000.0
    monkeypatch.setattr(time, "monotonic", lambda: now)
    _seed_series(sup, "MKT-A", [(now - 20, 60), (now, 40)])  # side A drops 20
    _seed_series(sup, "MKT-B", [(now - 20, 40), (now, 60)])  # side B rises 20

    sup._detect_market_spikes(
        [
            _market("MKT-A", "EVT-1"),
            _market("MKT-B", "EVT-1"),
        ]
    )
    assert calls == [1]


def test_cooldown_entries_pruned_for_dead_events(monkeypatch: pytest.MonkeyPatch) -> None:
    """An event that fired then dropped off the live feed has its cooldown entry
    cleaned up — bounded growth across a season."""
    sup = Supervisor()
    _spy_burst(sup)
    now = 5000.0
    monkeypatch.setattr(time, "monotonic", lambda: now)
    _seed_series(sup, "MKT-A", [(now - 20, 30), (now, 50)])
    sup._detect_market_spikes([_market("MKT-A", "EVT-1")])
    assert "EVT-1" in sup._last_burst_at

    # Next tick, EVT-1 no longer live (empty feed) → entry pruned.
    sup._detect_market_spikes([])
    assert "EVT-1" not in sup._last_burst_at
