"""In-memory price-history buffer — recent mid-prices per market.

LUTZ reads the current top-of-book from /partner/context, but a snapshot hides
the path: "draw at 47" means one thing climbing from 30, another falling from
55. This keeps a short, bounded series of recent mids per subscribed market so
the partner can read the tape, not just the tick.

In-memory and ephemeral by design (mirrors nudge_evaluator): a price trajectory
is session context, not money. A restart loses the path and the buffer refills
within minutes — no table, no migration, no eviction job. Bounded by
construction: a fixed-length deque per ticker, and keys for closed/unsubscribed
markets are dropped so the buffer can't grow across a session.

Sampling is decoupled from the WS delta firehose — the supervisor calls
record() on a modest tick reading the resolved book mid, not on every fractional
delta. Money stays integer cents end to end.
"""
from __future__ import annotations

import time
from collections import deque

MAX_SAMPLES = 20
"""Per-ticker depth. Enough to read a trajectory at the sampling cadence
without bloating the context payload. ~20 samples at a ~20s tick ≈ the last
~6-7 minutes of price action."""


class PriceHistory:
    """Bounded recent-mid series per market ticker. One instance on the
    supervisor; sync (single asyncio loop), like LiveState."""

    def __init__(self, max_samples: int = MAX_SAMPLES) -> None:
        self._max = max_samples
        self._series: dict[str, deque[tuple[float, int]]] = {}
        """ticker -> deque[(monotonic_ts, mid_cents)], newest last."""

    def record(self, ticker: str, mid_cents: int) -> None:
        """Append one mid sample for a ticker. Mid is integer cents."""
        buf = self._series.get(ticker)
        if buf is None:
            buf = deque(maxlen=self._max)
            self._series[ticker] = buf
        buf.append((time.monotonic(), mid_cents))

    def series(self, ticker: str) -> list[tuple[float, int]]:
        """Recent samples for a ticker, oldest first. Empty if untracked."""
        buf = self._series.get(ticker)
        return list(buf) if buf is not None else []

    def retain_only(self, tickers: set[str]) -> None:
        """Drop every series whose ticker isn't in `tickers`. The bounded-growth
        guard: called each sampling tick with the currently WS-subscribed set so
        markets we've stopped following fall out automatically."""
        for dead in self._series.keys() - tickers:
            self._series.pop(dead, None)
