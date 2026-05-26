"""Market-tier classifier: Far / Soon / Live.

Drives the data-discipline tiering for soccer markets. Each tier has its
own refresh policy (see C2/C3); this module is pure classification.

  FAR    >24h to kickoff       REST orderbook poll, sparse cadence
  SOON   ≤24h to kickoff       WS orderbook_delta subscribed
  LIVE   kickoff has passed    WS orderbook_delta subscribed
  DONE   past LIVE_WINDOW      no data pulls (tier manager will unsubscribe)

The tiering depends on a kickoff estimate. Today that comes from the date
encoded in the Kalshi ticker (noon UTC midday proxy) — accurate to ±12h.
ESPN integration (C6) replaces this with a precise kickoff time and a
real end-of-game signal.

Why noon UTC is acceptable for now: a ±12h error means the SOON window
could open as early as 36h before real kickoff or as late as 12h before.
That's wasteful (we open WS sooner than needed) but not wrong (we never
miss the window). The fix is upstream timing data, not threshold padding.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum


class MarketTier(StrEnum):
    FAR = "far"
    SOON = "soon"
    LIVE = "live"
    DONE = "done"


SOON_WINDOW = timedelta(hours=24)
"""Switch from Far (REST polling) to Soon (WS subscribed) at this distance
from kickoff."""

LIVE_WINDOW = timedelta(hours=3, minutes=30)
"""How long a match stays in LIVE after kickoff. ~110 min match + stoppage
+ buffer. Once exceeded, the market is DONE — tier manager unsubscribes."""


@dataclass(frozen=True)
class TierClassification:
    """Result of classifying one ticker."""
    tier: MarketTier
    kickoff: datetime | None
    """The kickoff estimate used. None means we couldn't derive one — caller
    should fall back to the safest tier (FAR) and avoid WS subscription."""
    seconds_to_kickoff: float | None
    """Negative if kickoff is in the past. None if kickoff was unknown."""


def classify(kickoff: datetime | None, now: datetime) -> TierClassification:
    """Pure classifier — given a kickoff estimate (or None), return the tier.

    Caller derives `kickoff` from whatever source it trusts (ticker date,
    Kalshi open_time, ESPN event.date). This function makes no decisions
    about which source to use — it just applies the time-window policy.

    With no kickoff data, returns FAR. That's deliberately conservative:
    FAR is the cheapest tier (REST polling), so an unknown market won't
    consume WS bandwidth, and the user can force-refresh it on demand.
    """
    if kickoff is None:
        return TierClassification(tier=MarketTier.FAR, kickoff=None, seconds_to_kickoff=None)

    delta = (kickoff - now).total_seconds()

    if delta > SOON_WINDOW.total_seconds():
        return TierClassification(tier=MarketTier.FAR, kickoff=kickoff, seconds_to_kickoff=delta)

    if delta > 0:
        return TierClassification(tier=MarketTier.SOON, kickoff=kickoff, seconds_to_kickoff=delta)

    # kickoff has passed
    if -delta <= LIVE_WINDOW.total_seconds():
        return TierClassification(tier=MarketTier.LIVE, kickoff=kickoff, seconds_to_kickoff=delta)

    return TierClassification(tier=MarketTier.DONE, kickoff=kickoff, seconds_to_kickoff=delta)


def far_poll_interval(seconds_to_kickoff: float | None) -> timedelta:
    """Adaptive REST-poll cadence for FAR-tier markets.

    Wider intervals when kickoff is days out, tightening as we approach
    the SOON boundary. Once the market crosses into SOON, the WS handles
    updates and this function isn't consulted.

      >72h out:  6h cadence
      24h–72h:   2h cadence
      0–24h:     n/a — caller should have already transitioned to SOON

    None (unknown kickoff) gets the 6h default — same as far-future markets.
    """
    if seconds_to_kickoff is None:
        return timedelta(hours=6)
    if seconds_to_kickoff > 72 * 3600:
        return timedelta(hours=6)
    if seconds_to_kickoff > 24 * 3600:
        return timedelta(hours=2)
    return timedelta(minutes=30)
