"""Position reconciliation against Kalshi.

The user's Kalshi account holds positions across many domains — soccer
(this app), politics, weather, etc. This app must NEVER touch the
non-soccer positions: not read them into our DB, not cancel their orders,
not factor them into any displayed P&L. See memory:
feedback_cross_market_isolation.

The sync loop:
  1. Fetch every page of /portfolio/positions from Kalshi.
  2. Filter to tracked tickers (is_tradeable_ticker — soccer + combos) BEFORE
     any further work.
  3. UPSERT each tracked position into our POSITION table.
  4. Mark any soccer positions we have that Kalshi doesn't as zeroed.
       (User closed it on kalshi.com — bet_service still has the BET row,
        but the position is gone.)
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from collections.abc import Awaitable, Callable

from src.core.db import get_session_factory
from src.core.logging import get_logger
from src.core.types import BetSide, BetStatus, MarketStatus, Sport, dollars_str_to_cents
from src.kalshi.live_state import LiveState, MarketBook
from src.kalshi.rest import KalshiRestClient
from src.kalshi.schemas import PortfolioPosition
from src.models import Bet, Market, Position
from src.sports.combo import is_combo_ticker
from src.sports.tradeable import is_tradeable_ticker


def _ticker_sport(ticker: str) -> Sport:
    """Sport for a tracked ticker. Combos are their own category; everything
    else this app tracks is soccer. Mirrors bet_service._ticker_to_sport so a
    combo position never gets mislabeled soccer (which would pollute the
    soccer-only stats Sport.COMBO exists to keep clean)."""
    return Sport.COMBO if is_combo_ticker(ticker) else Sport.SOCCER

log = get_logger(__name__)

POLL_INTERVAL_S = 60


async def _get_or_create_market_id(session: AsyncSession, *, ticker: str) -> int:
    """Same helper as bet_service — kept duplicated for clarity; both need it
    and importing across services would pull in extra concerns."""
    existing = await session.scalar(select(Market).where(Market.kalshi_ticker == ticker))
    if existing is not None:
        return existing.id

    m = Market(
        sport=_ticker_sport(ticker),
        game_id=None,
        kalshi_ticker=ticker,
        market_type="combo" if is_combo_ticker(ticker) else "match_result",
        title=ticker,
        yes_price_cents=None,
        no_price_cents=None,
        volume=None,
        close_time=None,
        status=MarketStatus.OPEN,
    )
    session.add(m)
    await session.flush()
    return m.id


def _signed_position_to_side_and_qty(signed: int) -> tuple[BetSide, int]:
    """Kalshi's `position` is signed: positive = YES exposure, negative = NO."""
    if signed >= 0:
        return BetSide.YES, signed
    return BetSide.NO, abs(signed)


def _mark_price_cents(book: MarketBook | None, side: BetSide) -> int | None:
    """Midpoint mark for `side` from the live book: (best_bid + best_ask)/2.

    Per-side bid/ask come straight from MarketBook's accessors (the ask on one
    side is derived from the opposite side's best bid — Kalshi stores only bids
    per side). Returns None when either leg is missing so the caller leaves the
    mark null instead of inventing a price.
    """
    if book is None:
        return None
    if side is BetSide.YES:
        bid, ask = book.yes_best_bid, book.yes_best_ask
    else:
        bid, ask = book.no_best_bid, book.no_best_ask
    if bid is None or ask is None:
        return None
    mid: int = round((bid + ask) / 2)
    return mid


def _rest_mark_price_cents(market: dict[str, object], side: BetSide) -> int | None:
    """Mark for `side` from a REST /markets payload's dollar fields.

    The fallback for positions with no live WS book — illiquid combos and
    longshot outrights aren't WS-subscribed, so _mark_price_cents returns None
    for them and the card would show no current price. Kalshi exposes the book
    top on the market object as dollar strings (yes_bid_dollars etc.); route
    them through the single canonical converter.

    A book with no genuine quotes reports the full 0↔100 boundary
    (bid=0, ask=100) on a side — its midpoint is a meaningless 50¢, NOT a price.
    In that case fall back to last_price (the last real trade) for that side, and
    only return None if there's no last trade either — never invent a 50¢ mark.
    """
    if side is BetSide.YES:
        bid_raw, ask_raw = market.get("yes_bid_dollars"), market.get("yes_ask_dollars")
    else:
        bid_raw, ask_raw = market.get("no_bid_dollars"), market.get("no_ask_dollars")
    if isinstance(bid_raw, str) and isinstance(ask_raw, str):
        bid = dollars_str_to_cents(bid_raw)
        ask = dollars_str_to_cents(ask_raw)
        # Genuine two-sided-ish book: at least one side is quoted inside the
        # 0↔100 boundary. (One-sided quotes — e.g. yes_ask 7¢, yes_bid 0¢ — are
        # real and keep their midpoint.)
        if not (bid == 0 and ask == 100):
            return round((bid + ask) / 2)
    # No real book on this side. last_price is market-wide on the YES scale; the
    # NO holder's mark is its complement.
    last_raw = market.get("last_price_dollars")
    if isinstance(last_raw, str):
        yes_last = dollars_str_to_cents(last_raw)
        if yes_last > 0:
            return yes_last if side is BetSide.YES else 100 - yes_last
    return None


async def _upsert_position(
    session: AsyncSession,
    *,
    p: PortfolioPosition,
    avg_entry_price_cents: int,
    current_price_cents: int | None,
    unrealized_pnl_cents: int | None,
) -> None:
    """UPSERT one POSITION row.

    avg_entry_price_cents is the clamped whole-cent value (CHECK constraint);
    cost_basis_cents/realized_pnl_cents/fees_paid_cents mirror Kalshi's exact
    authoritative numbers so display can derive the true fractional price and
    show fee-inclusive PnL without our own lossy reconstruction.

    Side-flip cleanup: a market can't hold both YES and NO exposure
    simultaneously (Kalshi nets them — buying YES against a NO position
    just closes contracts). If we previously cached the opposite side
    and the current position is on this side, the cached row is stale
    and must be deleted. Skipping this leaves zombie positions in the
    UI (the bug that surfaced 2026-05-26 when the user closed a NO
    position and opened a YES one on the same ticker).
    """
    side, qty = _signed_position_to_side_and_qty(p.position)
    market_id = await _get_or_create_market_id(session, ticker=p.ticker)
    opposite_side = BetSide.NO if side is BetSide.YES else BetSide.YES

    # Drop any cached row on the opposite side — the current Kalshi snapshot
    # says it's not held.
    opposite = await session.scalar(
        select(Position).where(
            Position.market_id == market_id,
            Position.side == opposite_side,
        )
    )
    if opposite is not None:
        await session.delete(opposite)

    existing = await session.scalar(
        select(Position).where(
            Position.market_id == market_id,
            Position.side == side,
        )
    )

    if qty == 0:
        # Zero quantity = closed position. Delete the row so the UI doesn't
        # show stale exposure.
        if existing is not None:
            await session.delete(existing)
        return

    cost_basis = abs(p.market_exposure)
    if existing is None:
        new_row = Position(
            sport=_ticker_sport(p.ticker),
            kalshi_ticker=p.ticker,
            market_id=market_id,
            side=side,
            quantity=qty,
            avg_entry_price_cents=avg_entry_price_cents,
            cost_basis_cents=cost_basis,
            current_price_cents=current_price_cents,
            unrealized_pnl_cents=unrealized_pnl_cents,
            realized_pnl_cents=p.realized_pnl,
            fees_paid_cents=p.fees_paid,
            last_synced=datetime.now(timezone.utc),
        )
        session.add(new_row)
    else:
        existing.quantity = qty
        existing.avg_entry_price_cents = avg_entry_price_cents
        existing.cost_basis_cents = cost_basis
        existing.current_price_cents = current_price_cents
        existing.unrealized_pnl_cents = unrealized_pnl_cents
        existing.realized_pnl_cents = p.realized_pnl
        existing.fees_paid_cents = p.fees_paid
        existing.last_synced = datetime.now(timezone.utc)


def _estimate_avg_entry(p: PortfolioPosition) -> int:
    """market_exposure / |position|, clamped to 1-99¢. Falls back to 50 when
    the math doesn't make sense (closed-out positions etc.)."""
    if p.position == 0:
        return 50
    raw = abs(p.market_exposure) // abs(p.position)
    return max(1, min(99, raw))


# Above this gap (cents) between Kalshi's realized PnL and our reconstructed
# ledger PnL, we log a warning. A persistent gap means our fill handling has
# drifted from reality — the observability we kept instead of mirroring
# Kalshi's PnL into the ledger wholesale (see the precision-fix decision).
_PNL_DIVERGENCE_THRESHOLD_CENTS = 1


async def _log_pnl_divergence(session: AsyncSession, *, p: PortfolioPosition) -> None:
    """Compare Kalshi's authoritative realized PnL for this position against
    the sum of our ledger bets' realized_pnl_cents on the same market. Logs
    a warning past the threshold. Read-only — never writes."""
    kalshi_realized = p.realized_pnl
    if kalshi_realized == 0:
        return  # nothing realized yet; no meaningful comparison
    market = await session.scalar(
        select(Market).where(Market.kalshi_ticker == p.ticker)
    )
    if market is None:
        return
    ours = await session.scalar(
        select(func.coalesce(func.sum(Bet.realized_pnl_cents), 0))
        .where(Bet.market_id == market.id)
    )
    ours_int = int(ours or 0)
    if abs(ours_int - kalshi_realized) > _PNL_DIVERGENCE_THRESHOLD_CENTS:
        log.warning(
            "pnl_divergence",
            ticker=p.ticker,
            kalshi_realized_cents=kalshi_realized,
            our_realized_cents=ours_int,
            delta_cents=ours_int - kalshi_realized,
        )


async def sync_positions_once(live_state: LiveState | None = None) -> dict[str, object]:
    """One full reconciliation pass. Returns counts and the set of tickers
    that transitioned from held to closed this pass — used to trigger
    settlement sweeps on the markets where Kalshi just paid us out.

    `live_state` (when supplied) is used to mark each position to the live
    book midpoint and derive unrealized PnL. None during tests / cold start —
    positions still sync, just without a live mark.
    """
    tracked_positions: list[PortfolioPosition] = []
    other_count = 0

    # REST-sourced marks for positions with no live WS book, filled below while
    # the client is open. ticker -> midpoint cents (or None when unquoted).
    rest_marks: dict[str, int | None] = {}

    async with KalshiRestClient() as client:
        cursor: str | None = None
        while True:
            resp = await client.get_positions(cursor=cursor)
            for p in resp.market_positions:
                # Cross-market isolation: filter at the top, before any work.
                # Tracked = soccer markets we follow + combo (MVE) markets.
                if is_tradeable_ticker(p.ticker):
                    tracked_positions.append(p)
                else:
                    other_count += 1
            cursor = resp.cursor
            if not cursor:
                break

        # Fallback marks: any held ticker without a live WS book (illiquid
        # combos, longshot outrights) gets one REST market read so its card can
        # still show a current price + unrealized PnL. Only these — liquid
        # markets already have a book mark. Best-effort: a per-market failure
        # leaves that one unmarked, never aborts the sync.
        for p in tracked_positions:
            if p.position == 0:
                continue
            book = live_state.books.get(p.ticker) if live_state is not None else None
            side, _qty = _signed_position_to_side_and_qty(p.position)
            if _mark_price_cents(book, side) is not None:
                continue
            try:
                raw = await client.get_market(p.ticker)
                market = raw.get("market", raw)
                rest_marks[p.ticker] = _rest_mark_price_cents(market, side)
            except Exception:  # noqa: BLE001 — one bad market never fails the sweep
                log.warning("position_rest_mark_failed", ticker=p.ticker)

    factory = get_session_factory()
    closed_with_open_bet: set[str] = set()
    async with factory() as session:
        # Snapshot which tickers we currently have in our DB so we can null
        # out the ones Kalshi no longer reports.
        existing_tickers = {
            row[0] for row in (await session.execute(
                select(Position.kalshi_ticker)
            )).all()
        }

        seen_tickers: set[str] = set()
        for p in tracked_positions:
            side, qty = _signed_position_to_side_and_qty(p.position)
            # Mark to the live book midpoint; unrealized = (mark - entry)·qty.
            # entry comes from Kalshi's exact cost basis, not the floored
            # avg, so the % the UI derives matches the dollar figure.
            book = live_state.books.get(p.ticker) if live_state is not None else None
            # Live WS book first; fall back to the REST mark for book-less
            # (illiquid) positions collected above.
            mark = _mark_price_cents(book, side)
            if mark is None:
                mark = rest_marks.get(p.ticker)
            unrealized: int | None = None
            if mark is not None and qty > 0:
                entry_exact = abs(p.market_exposure) / qty  # cents/contract, fractional
                unrealized = round((mark - entry_exact) * qty)
            await _upsert_position(
                session,
                p=p,
                avg_entry_price_cents=_estimate_avg_entry(p),
                current_price_cents=mark,
                unrealized_pnl_cents=unrealized,
            )
            seen_tickers.add(p.ticker)
            await _log_pnl_divergence(session, p=p)

        # Any soccer position we had that Kalshi didn't return — closed.
        for ticker in existing_tickers - seen_tickers:
            for row in (await session.execute(
                select(Position).where(Position.kalshi_ticker == ticker)
            )).scalars():
                await session.delete(row)
            # If there's still an OPEN bet on this market, the position
            # vanishing is likely a settlement payout we missed via WS.
            still_open = await session.scalar(
                select(Bet.id)
                .join(Market, Market.id == Bet.market_id)
                .where(Market.kalshi_ticker == ticker)
                .where(Bet.status == BetStatus.OPEN)
                .limit(1)
            )
            if still_open is not None:
                closed_with_open_bet.add(ticker)

        await session.commit()

    log.info(
        "position_sync_complete",
        tracked=len(tracked_positions),
        untracked_skipped=other_count,
        closed_with_open_bet=len(closed_with_open_bet),
    )
    return {
        "tracked": len(tracked_positions),
        "untracked_skipped": other_count,
        "closed_with_open_bet": closed_with_open_bet,
    }


class PositionSyncer:
    """Long-running poller. Lives on the supervisor."""

    def __init__(self, live_state: LiveState | None = None) -> None:
        self._stopped = False
        self._last_run_at: float | None = None
        self._live_state = live_state
        self._on_position_closed: Callable[[], Awaitable[None]] | None = None
        self._on_synced: Callable[[], Awaitable[None]] | None = None

    def set_on_position_closed(
        self, cb: Callable[[], Awaitable[None]]
    ) -> None:
        """Called after a sync that detected a Kalshi position dropping to
        zero while we still have an OPEN bet on that market — the supervisor
        wires this to settlement_sweeper.trigger()."""
        self._on_position_closed = cb

    def set_on_synced(self, cb: Callable[[], Awaitable[None]]) -> None:
        """Called after every successful sync commit. The supervisor wires
        this to broadcast a `position_synced` browser event — emitting it
        post-commit guarantees any refetch it triggers reads fresh DB state
        (the fix for the lag between a fill and the position appearing)."""
        self._on_synced = cb

    @property
    def last_run_age_s(self) -> float | None:
        if self._last_run_at is None:
            return None
        return time.monotonic() - self._last_run_at

    async def run(self) -> None:
        # First sync ASAP at startup; then on the interval.
        await self._tick()
        while not self._stopped:
            await asyncio.sleep(POLL_INTERVAL_S)
            await self._tick()

    async def _tick(self) -> None:
        try:
            result = await sync_positions_once(self._live_state)
            self._last_run_at = time.monotonic()
        except Exception:  # noqa: BLE001
            log.exception("position_sync_failed")
            return
        if self._on_synced is not None:
            try:
                await self._on_synced()
            except Exception:  # noqa: BLE001
                log.exception("on_synced_callback_failed")
        closed = result.get("closed_with_open_bet") or set()
        if closed and self._on_position_closed is not None:
            try:
                await self._on_position_closed()
            except Exception:  # noqa: BLE001
                log.exception("on_position_closed_callback_failed")

    async def trigger(self) -> None:
        """Fire an extra sync now — called after order placement."""
        await self._tick()

    async def stop(self) -> None:
        self._stopped = True
