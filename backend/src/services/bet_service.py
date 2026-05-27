"""BET row persistence.

Two write paths:
  - record_placed_order: called by the orders route after Kalshi accepts
    the order. Creates a BET in status=OPEN with kalshi_order_id pinned.
  - record_fill: called by the WS dispatcher when a fill event arrives.
    Updates the matching BET (entry price refines, status moves toward
    terminal once fully filled).

We do NOT create a BET per fill — that would dupe the row. One BET per
order; fills update it in place. Position reconciliation (chunk 13) handles
the edge case where Kalshi knows about a fill we don't.

Cross-market isolation: every write checks is_soccer_ticker first. A fill
for a politics market that somehow arrives on our WS (shouldn't happen,
but) gets logged and dropped, never persisted.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logging import get_logger
from src.core.types import (
    BetSide,
    BetSource,
    BetStatus,
    Confidence,
    ExitType,
    MarketSettlement,
    MarketStatus,
    Sport,
    Strategy,
    Timing,
)
from src.kalshi.schemas import Order
from src.kalshi.ws_wire import Fill, UserOrder
from src.models import Bet, Market
from src.sports.soccer import is_soccer_ticker

log = get_logger(__name__)


def _ticker_to_sport(ticker: str) -> Sport:
    """Best-effort sport classifier. Today: soccer-or-bust."""
    return Sport.SOCCER  # soccer is the only sport this app handles today


async def record_placed_order(
    session: AsyncSession,
    *,
    order: Order,
    client_order_id: str,
    requested_count: int,
    requested_price_cents: int,
    source: BetSource = BetSource.HUMAN,
    strategy: Strategy = Strategy.MANUAL,
    confidence: Confidence = Confidence.MEDIUM,
    timing: Timing = Timing.PRE_MATCH,
    human_reasoning: str | None = None,
) -> Bet:
    """Persist a freshly-placed order as a BET row.

    Idempotent on (kalshi_order_id) — if a row already exists for this
    order_id we return it instead of inserting a dupe. This protects
    against a route retry after a network blip.
    """
    if not is_soccer_ticker(order.ticker):
        raise ValueError(f"refusing to record non-soccer ticker {order.ticker}")

    # Idempotency check.
    existing = await session.scalar(
        select(Bet).where(Bet.kalshi_order_id == order.order_id)
    )
    if existing is not None:
        log.info("bet_record_skipped_duplicate", order_id=order.order_id)
        return existing

    # Look up the market_id for the ticker. We need it because BET.market_id
    # is NOT NULL. If we haven't seen the market yet, create a minimal Market
    # row — the discovery poller will fill in the rest on its next cycle.
    market_id = await _get_or_create_market(session, ticker=order.ticker)

    bet = Bet(
        sport=_ticker_to_sport(order.ticker),
        market_id=market_id,
        suggestion_id=None,
        parent_bet_id=None,
        kalshi_order_id=order.order_id,
        kalshi_fill_id=None,
        client_order_id=client_order_id,
        side=BetSide(order.side),
        entry_price_cents=order.yes_price or requested_price_cents,
        exit_price_cents=None,
        quantity=requested_count,
        stake_cents=requested_count * (order.yes_price or requested_price_cents),
        pnl_cents=None,
        status=BetStatus.OPEN,
        exit_type=None,
        source=source,
        strategy=strategy,
        confidence=confidence,
        kelly_fraction_bps=None,
        ai_probability_pct=None,
        human_override_sizing=False,
        human_override_direction=False,
        human_reasoning=human_reasoning,
        ai_reasoning=None,
        timing=timing,
        game_period=None,
        game_clock=None,
        tags=None,
        verified=True,
        version=1,
        placed_at=datetime.now(timezone.utc),
        settled_at=None,
    )
    session.add(bet)
    await session.flush()
    log.info(
        "bet_recorded",
        bet_id=bet.id,
        ticker=order.ticker,
        order_id=order.order_id,
        side=order.side,
        count=requested_count,
        price_cents=order.yes_price,
    )
    return bet


async def record_fill(session: AsyncSession, fill: Fill) -> None:
    """Update the BET matching the fill's order_id with refined fields.

    Fills carry the actual execution price, which may differ from the
    quoted price on a market order. We update entry_price_cents (weighted
    average across multiple fills) but don't transition status here —
    status transitions happen on settlement, not on fills.
    """
    ticker = fill.msg.ticker
    if not is_soccer_ticker(ticker):
        log.warning("fill_dropped_non_soccer_ticker", ticker=ticker)
        return

    bet = await session.scalar(
        select(Bet).where(Bet.kalshi_order_id == fill.msg.order_id)
    )
    if bet is None:
        # Fill for an order we don't have a BET for — reconcile in chunk 13.
        log.info("fill_orphan", order_id=fill.msg.order_id, ticker=ticker)
        return

    # Weighted average: existing_qty * existing_price + new_qty * new_price.
    new_qty = fill.msg.count
    new_price = (
        fill.msg.yes_price_cents if bet.side == BetSide.YES
        else fill.msg.no_price_cents
    )
    if new_qty <= 0 or new_price < 1 or new_price > 99:
        return  # bug-input guard

    # If this fill is the first reported one, just take its price as
    # entry_price. Otherwise blend.
    if bet.kalshi_fill_id is None:
        bet.entry_price_cents = new_price
    else:
        # We don't track partial fills with per-fill rows yet; this blends.
        blended = (
            (bet.entry_price_cents * bet.quantity) + (new_price * new_qty)
        ) // (bet.quantity + new_qty)
        bet.entry_price_cents = blended

    bet.kalshi_fill_id = fill.msg.trade_id
    bet.version += 1
    await session.flush()
    log.info(
        "bet_fill_recorded",
        bet_id=bet.id,
        trade_id=fill.msg.trade_id,
        new_entry_cents=bet.entry_price_cents,
    )


async def mark_bet_terminal_by_order_id(
    session: AsyncSession,
    *,
    order_id: str,
    status: BetStatus,
    exit_type: ExitType | None = None,
) -> Bet | None:
    """Transition the BET matching `order_id` to a terminal status (CANCELLED
    or one of WON/LOST/etc.). Idempotent — already-terminal bets are left
    alone so we don't clobber a settled WON with a CANCELLED.

    Used by:
      - the cancel route, after Kalshi acks the cancel (mark CANCELLED).
      - the WS user_order handler, when status arrives as 'canceled' or
        'executed' on the wire (defense in depth — the route already
        handles the cancel-from-us case).

    Returns the updated BET (or None if we have no BET for this order).
    """
    bet = await session.scalar(
        select(Bet).where(Bet.kalshi_order_id == order_id)
    )
    if bet is None:
        # External order or one we never recorded — no-op. See
        # feedback_no_external_fill_reconciliation: we don't auto-create
        # BETs for stuff that bypassed our place route.
        return None
    if bet.status != BetStatus.OPEN:
        # Already terminal. Don't overwrite a WON with CANCELLED if a
        # late cancel event arrives after settlement, for example.
        return bet
    bet.status = status
    if exit_type is not None:
        bet.exit_type = exit_type
    bet.version += 1
    await session.flush()
    log.info(
        "bet_transitioned",
        bet_id=bet.id, order_id=order_id,
        new_status=status, exit_type=exit_type,
    )
    return bet


async def settle_bets_for_market(
    session: AsyncSession,
    *,
    ticker: str,
    settlement_value_cents: int,
) -> int:
    """Transition every OPEN BET on this market to WON or LOST.

    `settlement_value_cents` is the YES-side payoff in cents per contract:
      0   = NO won outright
      100 = YES won outright
      anything else is a scalar/partial settle — rare on soccer moneylines;
            we still compute P&L from it directly.

    P&L formula per bet:
      YES side: pnl = (settlement - entry) * quantity
      NO side:  pnl = ((100 - settlement) - (100 - entry)) * quantity
                    = (entry - settlement) * quantity

    Cross-market isolation: refuses non-soccer tickers. Returns the count
    of bets transitioned, for logging / health metrics.

    Idempotent: bets already in a terminal state are skipped. Safe to call
    from both the WS market_lifecycle handler and a periodic sweep.
    """
    if not is_soccer_ticker(ticker):
        log.warning("settle_dropped_non_soccer_ticker", ticker=ticker)
        return 0

    market = await session.scalar(select(Market).where(Market.kalshi_ticker == ticker))
    if market is None:
        # No BETs can exist without a market row (FK), so nothing to settle.
        return 0

    settled_at = datetime.now(timezone.utc)
    # Mark the market settled too — single source of truth for "this market is done."
    market.status = MarketStatus.SETTLED
    market.settlement_detected_at = settled_at
    market.settlement = (
        MarketSettlement.YES if settlement_value_cents >= 50
        else MarketSettlement.NO
    )

    open_bets = (
        await session.execute(
            select(Bet)
            .where(Bet.market_id == market.id)
            .where(Bet.status == BetStatus.OPEN)
        )
    ).scalars().all()

    transitioned = 0
    for bet in open_bets:
        if bet.side is BetSide.YES:
            pnl = (settlement_value_cents - bet.entry_price_cents) * bet.quantity
        else:
            pnl = (bet.entry_price_cents - settlement_value_cents) * bet.quantity
        bet.exit_price_cents = settlement_value_cents if bet.side is BetSide.YES else 100 - settlement_value_cents
        bet.pnl_cents = pnl
        bet.status = BetStatus.WON if pnl > 0 else BetStatus.LOST
        bet.exit_type = ExitType.HELD_TO_SETTLEMENT
        bet.settled_at = settled_at
        bet.version += 1
        transitioned += 1

    await session.flush()
    log.info(
        "bets_settled",
        ticker=ticker,
        settlement_value_cents=settlement_value_cents,
        transitioned=transitioned,
    )
    return transitioned


async def _get_or_create_market(session: AsyncSession, *, ticker: str) -> int:
    """Return market_id for a ticker, inserting a minimal row if absent.

    BET.market_id is NOT NULL. If the discovery poller hasn't created this
    market's row yet (because we're hitting Kalshi directly via paste-box),
    insert a placeholder so the FK constraint is satisfied. The discovery
    cycle will UPDATE the row with prices and metadata when it next runs.
    """
    from src.core.types import MarketStatus
    from src.models import Market

    existing = await session.scalar(select(Market).where(Market.kalshi_ticker == ticker))
    if existing is not None:
        return existing.id

    m = Market(
        sport=_ticker_to_sport(ticker),
        game_id=None,
        kalshi_ticker=ticker,
        market_type="match_result",
        title=ticker,  # better title is filled in by the discovery poller
        yes_price_cents=None,
        no_price_cents=None,
        volume=None,
        close_time=None,
        status=MarketStatus.OPEN,
        settlement=None,
        settlement_detected_at=None,
    )
    session.add(m)
    await session.flush()
    return m.id
