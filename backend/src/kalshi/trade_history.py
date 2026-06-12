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

Pagination stops at kickoff, not at a flat trade count. Kalshi returns trades
newest-first, so once a page's *oldest* trade predates the kickoff cutoff,
every remaining page is older still and would be trimmed away — there's
nothing left to gain by paging further. A flat trade cap got this exactly
backwards on a super-high-volume market: 5000 newest trades could all fall
inside the last few minutes, so pagination stopped before ever reaching
kickoff and the chart showed only recent history while a normal-volume game
spanned the whole match. Bounding by kickoff fixes coverage (always reaches
kickoff) and still bounds work (never pages into pre-match noise).

Fetching deals in raw Kalshi trade dicts (cents conversion stays at the route
boundary). Downsampling runs on normalized rows that already carry an integer
`yes_price`, so the two halves stay decoupled.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.kalshi.rest import KalshiRestClient

# Kalshi's per-request hard max for /markets/trades (limit range 1-1000).
_PAGE_SIZE = 1000

# Absolute safety ceiling so an unknown-kickoff market (no cutoff) can't page
# Kalshi's cursor forever. With a cutoff we stop at kickoff long before this;
# this only bites the rare ticker missing from the discovery feed.
_MAX_TRADES = 20_000


async def fetch_all_trades(
    client: KalshiRestClient, ticker: str, cutoff_ts: int | None = None
) -> list[dict[str, Any]]:
    """Every trade for `ticker` back to `cutoff_ts`, newest-first (Kalshi's order).

    Pages through Kalshi's cursor until a page reaches past `cutoff_ts` (epoch
    seconds), the history is exhausted, or the `_MAX_TRADES` safety ceiling is
    hit. When `cutoff_ts` is None (kickoff unknown) only the ceiling and the
    cursor end the walk. Returns Kalshi's raw trade dicts; the route normalizes
    and trims to kickoff.
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
        # Newest-first: once this page's oldest trade predates the cutoff, every
        # later page is older still — stop, the route trims the rest anyway.
        if cutoff_ts is not None and _page_reached_cutoff(page, cutoff_ts):
            break
    return trades


def _page_reached_cutoff(page: list[dict[str, Any]], cutoff_ts: int) -> bool:
    """True if `page`'s oldest trade is at or before `cutoff_ts` (epoch sec).

    Kalshi orders trades newest-first, so the oldest is the last entry with a
    parseable `created_time`. A page with no timestamps can't advance us, so
    treat it as not-yet-reached and let pagination continue."""
    for t in reversed(page):
        ts = t.get("created_time")
        if isinstance(ts, str):
            return _iso_to_epoch(ts) <= cutoff_ts
    return False


def _iso_to_epoch(ts: str) -> float:
    """Parse a Kalshi 'Z'-suffixed ISO timestamp to epoch seconds."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


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
