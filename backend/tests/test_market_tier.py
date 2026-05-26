"""Tier-classifier policy tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.services.market_tier import (
    LIVE_WINDOW,
    SOON_WINDOW,
    MarketTier,
    classify,
    far_poll_interval,
)


NOW = datetime(2026, 6, 11, 12, 0, tzinfo=timezone.utc)


def test_unknown_kickoff_classifies_as_far():
    """No kickoff data → FAR (cheapest tier, no WS pressure)."""
    r = classify(kickoff=None, now=NOW)
    assert r.tier is MarketTier.FAR
    assert r.kickoff is None
    assert r.seconds_to_kickoff is None


def test_far_when_kickoff_well_beyond_soon_window():
    kickoff = NOW + timedelta(days=10)
    r = classify(kickoff=kickoff, now=NOW)
    assert r.tier is MarketTier.FAR
    assert r.seconds_to_kickoff == 10 * 86400


def test_soon_at_boundary_minus_one_second():
    """Just inside SOON_WINDOW is SOON, not FAR."""
    kickoff = NOW + SOON_WINDOW - timedelta(seconds=1)
    assert classify(kickoff=kickoff, now=NOW).tier is MarketTier.SOON


def test_soon_at_exact_boundary():
    """Exactly at SOON_WINDOW is SOON (the boundary belongs to the closer tier)."""
    kickoff = NOW + SOON_WINDOW
    assert classify(kickoff=kickoff, now=NOW).tier is MarketTier.SOON


def test_far_one_second_beyond_boundary():
    kickoff = NOW + SOON_WINDOW + timedelta(seconds=1)
    assert classify(kickoff=kickoff, now=NOW).tier is MarketTier.FAR


def test_soon_one_minute_before_kickoff():
    kickoff = NOW + timedelta(minutes=1)
    assert classify(kickoff=kickoff, now=NOW).tier is MarketTier.SOON


def test_live_one_minute_after_kickoff():
    kickoff = NOW - timedelta(minutes=1)
    r = classify(kickoff=kickoff, now=NOW)
    assert r.tier is MarketTier.LIVE
    assert r.seconds_to_kickoff == -60


def test_live_at_inside_edge_of_live_window():
    kickoff = NOW - LIVE_WINDOW + timedelta(seconds=1)
    assert classify(kickoff=kickoff, now=NOW).tier is MarketTier.LIVE


def test_done_just_past_live_window():
    kickoff = NOW - LIVE_WINDOW - timedelta(seconds=1)
    assert classify(kickoff=kickoff, now=NOW).tier is MarketTier.DONE


def test_far_poll_interval_buckets():
    assert far_poll_interval(seconds_to_kickoff=None) == timedelta(hours=6)
    assert far_poll_interval(seconds_to_kickoff=10 * 86400) == timedelta(hours=6)
    assert far_poll_interval(seconds_to_kickoff=72 * 3600 + 1) == timedelta(hours=6)
    assert far_poll_interval(seconds_to_kickoff=72 * 3600) == timedelta(hours=2)
    assert far_poll_interval(seconds_to_kickoff=25 * 3600) == timedelta(hours=2)
    assert far_poll_interval(seconds_to_kickoff=24 * 3600) == timedelta(minutes=30)
    assert far_poll_interval(seconds_to_kickoff=60) == timedelta(minutes=30)
