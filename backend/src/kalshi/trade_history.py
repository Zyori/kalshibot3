"""Market trade-history fetching + downsampling for the price chart.

The price chart wants the *whole match* per market, not a trailing window of
the last N trades. A count-based window silently truncates the busiest line
(the one with the most trades has the shortest time-coverage), so on a long
game its early history disappears off the left of the chart while quieter
lines still stretch back to kickoff. Paginating Kalshi's cursor to pull the
full history fixes that: every line spans the same range, then the route
trims to kickoff.

We do NOT use Kalshi's `min_ts` filter: despite being documented on
/markets/trades, the live API silently ignores it (verified 2026-05-30 —
set vs unset returns byte-identical results at every `limit`). Kickoff
scoping happens in the route, client-side, on the trade timestamps.

Raw match history can be thousands of trades per line, which makes the chart
chug. We downsample to a target point count, keeping the first point, the
last point, and local extrema — the price swings are the whole point of the
chart, so naive every-Nth sampling (which can drop a spike) is wrong here.

Fetching deals in raw Kalshi trade dicts (cents conversion stays at the route
boundary). Downsampling runs on normalized rows that already carry an integer
`yes_price`, so the two halves stay decoupled.
"""

from __future__ import annotations

from typing import Any

from src.kalshi.rest import KalshiRestClient

# Kalshi's per-request hard max for /markets/trades (limit range 1-1000).
_PAGE_SIZE = 1000

# Worst-case trades fetched per market before we stop paginating, regardless
# of match length. 5 pages covers any realistic single soccer outcome — even
# a frantic final rarely exceeds a few thousand trades on one line — while
# bounding REST load and rate-limit pressure on first chart load.
_MAX_TRADES = 5000


async def fetch_all_trades(
    client: KalshiRestClient, ticker: str
) -> list[dict[str, Any]]:
    """Every available trade for `ticker`, newest-first (Kalshi's order).

    Pages through Kalshi's cursor until the history is exhausted or the
    `_MAX_TRADES` safety cap is hit. Returns Kalshi's raw trade dicts; the
    route normalizes and trims to kickoff.
    """
    trades: list[dict[str, Any]] = []
    cursor: str | None = None
    while len(trades) < _MAX_TRADES:
        data = await client.get_trades(ticker, limit=_PAGE_SIZE, cursor=cursor)
        page = data.get("trades", [])
        trades.extend(page)
        cursor = data.get("cursor") or None
        # Empty cursor or a short page both mean no more history.
        if cursor is None or len(page) < _PAGE_SIZE:
            break
    return trades


def downsample(rows: list[dict[str, Any]], target: int) -> list[dict[str, Any]]:
    """Thin chronological `rows` to ~`target` points, keeping price shape.

    Each row must carry an integer `yes_price`. Always keeps the first and
    last point. Within each interior bucket, keeps the local min and max by
    `yes_price` so swings survive — a flat-then-spike-then-flat line keeps
    its spike instead of being sampled into a flat line. Order is preserved.

    Returns `rows` unchanged when already at or under `target`.
    """
    n = len(rows)
    if n <= target or target < 3:
        return rows

    # Reserve the two endpoints; bucket the interior. Each bucket contributes
    # at most two points (its min and max by price), so size buckets to land
    # near `target`: (target - 2) / 2 buckets over the interior.
    interior = rows[1:-1]
    bucket_count = max(1, (target - 2) // 2)
    bucket_size = max(1, len(interior) // bucket_count)

    kept_idx: set[int] = {0, n - 1}
    for start in range(0, len(interior), bucket_size):
        chunk = interior[start : start + bucket_size]
        if not chunk:
            continue
        lo = min(range(len(chunk)), key=lambda i: chunk[i]["yes_price"])
        hi = max(range(len(chunk)), key=lambda i: chunk[i]["yes_price"])
        # +1 maps the interior index back into the full `rows` list.
        kept_idx.add(start + lo + 1)
        kept_idx.add(start + hi + 1)

    return [rows[i] for i in sorted(kept_idx)]
