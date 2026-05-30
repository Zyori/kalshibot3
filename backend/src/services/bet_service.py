"""BET row persistence.

One bet = one buy decision. Sells aggregate onto the oldest matching OPEN
bet via FIFO, decrementing remaining_quantity until the bet fully closes.
Per-fill detail lives in bet_fill (one row per Kalshi trade_id).

Write paths:
  - record_placed_order: called by the orders route after Kalshi accepts a
    BUY order. Creates an OPEN bet pinned to that order's id. Sells do not
    create a new bet — they get matched in record_fill against existing
    OPEN bets on the same (market, side).
  - record_fill: called by the WS dispatcher per fill event. Upserts a
    bet_fill row, then either refines the buy bet's entry price or
    FIFO-attributes a sell against open bets and decrements remaining
    quantity.

Fees: bet_fill.fee_cents is populated by the periodic fills-sync sweep
(WS fills don't carry fees). Bet-level entry_fees_cents and exit_fees_cents
are recomputed from bet_fill sums whenever fees arrive.

Cross-market isolation: every write checks is_soccer_ticker first.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

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
from src.kalshi.ws_wire import Fill
from src.models import Bet, BetFill, Market
from src.sports.soccer import is_soccer_ticker

log = get_logger(__name__)


def _ticker_to_sport(ticker: str) -> Sport:
    """Best-effort sport classifier. Today: soccer-or-bust."""
    return Sport.SOCCER  # soccer is the only sport this app handles today


def _materialize_bet_fill(
    *,
    existing: BetFill | None,
    fill: Fill,
    price_cents: int,
    quantity_centi: int,
    session: AsyncSession,
) -> BetFill:
    """Return the bet_fill row to attach a WS fill to. Either reuse the
    existing fills_sync-inserted row (preserving its fee_cents) or create
    a fresh one. Centralized so the buy/sell paths don't repeat the
    constructor."""
    if existing is not None:
        return existing
    bf = BetFill(
        bet_id=None,
        trade_id=fill.msg.trade_id,
        order_id=fill.msg.order_id,
        ticker=fill.msg.ticker,
        side=fill.msg.side,
        action=fill.msg.action,
        price_cents=price_cents,
        quantity_centi=quantity_centi,
        fee_cents=None,
        is_taker=fill.msg.is_taker,
        fee_synced_at=None,
        created_time=fill.msg.ts,
    )
    session.add(bf)
    return bf


async def _recompute_bet_fees(session: AsyncSession, *, bet: Bet) -> None:
    """Refresh entry_fees_cents and exit_fees_cents from this bet's bet_fill
    rows. Always derived; never accumulated. Safe to call whenever a
    bet_fill on the bet changes (fee arrived, attribution shifted)."""
    fills = (await session.execute(
        select(BetFill).where(BetFill.bet_id == bet.id)
    )).scalars().all()
    bet.entry_fees_cents = sum(
        (f.fee_cents or 0) for f in fills if f.action == "buy"
    )
    bet.exit_fees_cents = sum(
        (f.fee_cents or 0) for f in fills if f.action == "sell"
    )


async def recompute_bet_from_fills(session: AsyncSession, *, bet: Bet) -> None:
    """Recompute every derived field on Bet from its bet_fill rows. Used
    when fills_sync back-links an orphan bet_fill to its Bet — we don't
    know which buy/sell paths to re-run, so we re-derive the whole
    aggregate state in one place.

    This is also a natural future home for the buy/sell-path inline
    recomputes; they currently duplicate slices of this logic."""
    fills = (await session.execute(
        select(BetFill).where(BetFill.bet_id == bet.id)
    )).scalars().all()
    buys = [f for f in fills if f.action == "buy"]
    sells = [f for f in fills if f.action == "sell"]

    buy_centi = sum(f.quantity_centi for f in buys)
    if buy_centi > 0:
        weighted = sum(f.price_cents * f.quantity_centi for f in buys)
        bet.entry_price_cents = max(1, min(99, weighted // buy_centi))
        bet.stake_cents = (bet.entry_price_cents * buy_centi) // 100

    sell_centi = sum(f.quantity_centi for f in sells)
    if sell_centi > 0:
        weighted = sum(f.price_cents * f.quantity_centi for f in sells)
        bet.exit_price_cents = max(1, min(99, weighted // sell_centi))
        bet.realized_pnl_cents = (
            sum((f.price_cents - bet.entry_price_cents) * f.quantity_centi for f in sells)
            // 100
        )

    ordered_centi = bet.quantity * 100
    bet.remaining_quantity_centi = max(0, ordered_centi - sell_centi)
    bet.remaining_quantity = bet.remaining_quantity_centi // 100

    bet.entry_fees_cents = sum((f.fee_cents or 0) for f in buys)
    bet.exit_fees_cents = sum((f.fee_cents or 0) for f in sells)

    if bet.remaining_quantity_centi == 0 and bet.status == BetStatus.OPEN and sell_centi > 0:
        bet.status = (
            BetStatus.WON if (bet.realized_pnl_cents or 0) > 0 else BetStatus.LOST
        )
        bet.exit_type = ExitType.CLOSED_EARLY
        bet.settled_at = datetime.now(timezone.utc)
        bet.pnl_cents = bet.realized_pnl_cents
    bet.version += 1


async def record_placed_order(
    session: AsyncSession,
    *,
    order: Order,
    client_order_id: str,
    requested_count: int,
    requested_price_cents: int,
    action: Literal["buy", "sell"] = "buy",
    source: BetSource = BetSource.HUMAN,
    strategy: Strategy = Strategy.MANUAL,
    confidence: Confidence = Confidence.MEDIUM,
    timing: Timing = Timing.PRE_MATCH,
    human_reasoning: str | None = None,
) -> Bet | None:
    """Persist a freshly-placed order.

    BUY: insert a new OPEN bet with remaining_quantity = requested_count.
    SELL: do NOT insert a new bet — sells are matched as fills against the
        oldest matching OPEN bet inside record_fill. Returns the opener bet
        if one exists (so the API can echo its id), or None.

    Idempotent on (kalshi_order_id) for buys — repeated route calls return
    the existing row.
    """
    if not is_soccer_ticker(order.ticker):
        raise ValueError(f"refusing to record non-soccer ticker {order.ticker}")

    if action == "sell":
        # Sells don't create a bet row. Return the opener for the API echo.
        market_id = await _get_or_create_market(session, ticker=order.ticker)
        opener = await session.scalar(
            select(Bet)
            .where(Bet.market_id == market_id)
            .where(Bet.side == BetSide(order.side))
            .where(Bet.status == BetStatus.OPEN)
            .order_by(Bet.placed_at.asc().nulls_last(), Bet.id.asc())
        )
        return opener

    existing = await session.scalar(
        select(Bet).where(Bet.kalshi_order_id == order.order_id)
    )
    if existing is not None:
        log.info("bet_record_skipped_duplicate", order_id=order.order_id)
        return existing

    market_id = await _get_or_create_market(session, ticker=order.ticker)
    # Record the entry price for the SIDE we actually bought. Kalshi's order
    # response populates BOTH yes_price and no_price (complementary: a NO buy at
    # 35¢ comes back yes_price=65, no_price=35), so always taking yes_price
    # stored the complement on every NO bet and corrupted its P&L.
    side_price = order.no_price if order.side == "no" else order.yes_price
    entry_price = side_price or requested_price_cents

    bet = Bet(
        sport=_ticker_to_sport(order.ticker),
        market_id=market_id,
        suggestion_id=None,
        parent_bet_id=None,
        kalshi_order_id=order.order_id,
        client_order_id=client_order_id,
        side=BetSide(order.side),
        entry_price_cents=entry_price,
        exit_price_cents=None,
        quantity=requested_count,
        remaining_quantity=requested_count,
        remaining_quantity_centi=requested_count * 100,
        stake_cents=requested_count * entry_price,
        pnl_cents=None,
        realized_pnl_cents=None,
        entry_fees_cents=0,
        exit_fees_cents=0,
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
        price_cents=entry_price,
    )
    return bet


async def record_fill(session: AsyncSession, fill: Fill) -> None:
    """Apply a WS fill event.

    Inserts a bet_fill row (idempotent on trade_id) and then either:
      - BUY:  refines the opener bet's entry_price_cents (centi-weighted
              avg over all buy bet_fill rows). remaining_quantity is the
              full ordered amount; buy fills don't decrement it.
      - SELL: FIFO-matches against the oldest OPEN bet on (market, side),
              attaches the bet_fill to that bet, and recomputes opener's
              remaining_quantity, realized_pnl_cents, and exit_price_cents
              by re-summing all attached bet_fill rows. Bet flips to
              WON/LOST when remaining_quantity hits 0.

    Quantities flow as integer centicontracts (centi = contracts * 100)
    because Kalshi reports fractional `count_fp` when a fill spans fee
    tiers (e.g. 0.97 + 0.03 = 1 contract). Bet-level fields are still in
    whole contracts; centi -> contract conversion at the bet boundary.

    Fees: bet_fill.fee_cents is left NULL here — WS fills don't carry fees.
    fills_sync populates them later from REST.
    """
    ticker = fill.msg.ticker
    if not is_soccer_ticker(ticker):
        log.warning("fill_dropped_non_soccer_ticker", ticker=ticker)
        return

    new_centi = fill.msg.count_centi
    new_price = (
        fill.msg.yes_price_cents if fill.msg.side == "yes"
        else fill.msg.no_price_cents
    )
    if new_centi <= 0 or new_price < 1 or new_price > 99:
        return

    existing_fill = await session.scalar(
        select(BetFill).where(BetFill.trade_id == fill.msg.trade_id)
    )
    if existing_fill is not None and existing_fill.bet_id is not None:
        # WS replay/reconnect, or fills_sync already attached this trade.
        log.info("fill_already_recorded", trade_id=fill.msg.trade_id)
        return

    # Resolve which bet this fill attaches to BEFORE persisting a bet_fill
    # row. The old shape session.add()'d the row up-front and bailed on
    # missing-opener paths, leaving bet_id=NULL rows that polluted the
    # external-fill audit surface (feedback_no_external_fill_reconciliation).
    if fill.msg.action == "buy":
        bet = await session.scalar(
            select(Bet).where(Bet.kalshi_order_id == fill.msg.order_id)
        )
        if bet is None:
            # WS arrived before the orders route committed the Bet. Don't
            # persist a phantom external row — fills_sync will record this
            # trade with its fee_cost on the next sweep if it's genuinely
            # external; otherwise the orders route commit will land first
            # next time.
            log.info(
                "fill_orphan_buy_dropped",
                trade_id=fill.msg.trade_id,
                order_id=fill.msg.order_id,
                ticker=ticker,
            )
            return

        bet_fill = _materialize_bet_fill(
            existing=existing_fill, fill=fill, price_cents=new_price,
            quantity_centi=new_centi, session=session,
        )
        bet_fill.bet_id = bet.id
        await session.flush()
        buy_fills = (await session.execute(
            select(BetFill)
            .where(BetFill.bet_id == bet.id)
            .where(BetFill.action == "buy")
        )).scalars().all()
        total_centi = sum(f.quantity_centi for f in buy_fills)
        if total_centi > 0:
            weighted_centi = sum(f.price_cents * f.quantity_centi for f in buy_fills)
            bet.entry_price_cents = max(1, min(99, weighted_centi // total_centi))
            bet.stake_cents = (bet.entry_price_cents * total_centi) // 100
        await _recompute_bet_fees(session, bet=bet)
        bet.version += 1
        await session.flush()
        log.info(
            "bet_buy_fill_recorded",
            bet_id=bet.id,
            trade_id=fill.msg.trade_id,
            entry_price_cents=bet.entry_price_cents,
            buy_centi_total=total_centi,
        )
        return

    # SELL: peek at the first FIFO opener BEFORE persisting a bet_fill row.
    # If no opener exists (e.g. settle race), bail without leaving a phantom
    # bet_id=NULL row in the audit surface.
    market_id = await _get_or_create_market(session, ticker=ticker)
    side = BetSide(fill.msg.side)
    first_opener = await session.scalar(
        select(Bet)
        .where(Bet.market_id == market_id)
        .where(Bet.side == side)
        .where(Bet.status == BetStatus.OPEN)
        .where(Bet.remaining_quantity_centi > 0)
        .order_by(Bet.placed_at.asc().nulls_last(), Bet.id.asc())
    )
    if first_opener is None:
        log.warning(
            "sell_fill_no_opener_dropped",
            ticker=ticker,
            trade_id=fill.msg.trade_id,
            centi=new_centi,
        )
        return

    bet_fill = _materialize_bet_fill(
        existing=existing_fill, fill=fill, price_cents=new_price,
        quantity_centi=new_centi, session=session,
    )

    remaining_to_attribute_centi = new_centi
    primary_bet_id: int | None = None
    # Carry the REST-side fee (if fills_sync already populated it) so we
    # can pro-rate at write time across cross-opener splits, instead of
    # waiting for the next fills_sync sweep to heal misallocation.
    total_fee_to_split: int | None = bet_fill.fee_cents

    while remaining_to_attribute_centi > 0:
        opener = first_opener if primary_bet_id is None else await session.scalar(
            select(Bet)
            .where(Bet.market_id == market_id)
            .where(Bet.side == side)
            .where(Bet.status == BetStatus.OPEN)
            .where(Bet.remaining_quantity_centi > 0)
            .order_by(Bet.placed_at.asc().nulls_last(), Bet.id.asc())
        )
        if opener is None:
            log.warning(
                "sell_fill_no_opener",
                ticker=ticker,
                trade_id=fill.msg.trade_id,
                unattributed_centi=remaining_to_attribute_centi,
            )
            break

        chunk_centi = min(opener.remaining_quantity_centi, remaining_to_attribute_centi)

        # Attach a proportional bet_fill row to this opener. If the sell
        # only touched one opener, the whole bet_fill attaches; if it
        # spans openers, we split it into one bet_fill per opener so each
        # bet's drill-down shows the actual centi attributed to it.
        if chunk_centi == new_centi:
            # Whole sell goes to this opener.
            bet_fill.bet_id = opener.id
            primary_bet_id = opener.id
        else:
            # Cross-opener split: the originally-created bet_fill becomes
            # the chunk attached to the FIRST opener; we create extras
            # for subsequent openers. The total still sums correctly.
            if primary_bet_id is None:
                bet_fill.bet_id = opener.id
                bet_fill.quantity_centi = chunk_centi
                primary_bet_id = opener.id
            else:
                split = BetFill(
                    bet_id=opener.id,
                    trade_id=f"{fill.msg.trade_id}#{opener.id}",
                    order_id=fill.msg.order_id,
                    ticker=ticker,
                    side=fill.msg.side,
                    action=fill.msg.action,
                    price_cents=new_price,
                    quantity_centi=chunk_centi,
                    fee_cents=None,
                    is_taker=fill.msg.is_taker,
                    fee_synced_at=None,
                    created_time=fill.msg.ts,
                )
                session.add(split)

        await session.flush()

        # Recompute opener's totals from its bet_fill rows (single source).
        opener_fills = (await session.execute(
            select(BetFill).where(BetFill.bet_id == opener.id)
        )).scalars().all()
        sell_centi = sum(f.quantity_centi for f in opener_fills if f.action == "sell")
        # Ordered amount in centi minus sold centi = exact remaining. Kalshi
        # may split a single contract across fee tiers (0.97 + 0.03); using
        # whole contracts here would floor the residual to 0 and flip the
        # bet terminal while exposure remained.
        ordered_centi = opener.quantity * 100
        opener.remaining_quantity_centi = max(0, ordered_centi - sell_centi)
        opener.remaining_quantity = opener.remaining_quantity_centi // 100

        # realized_pnl: for each sell fill, (sell_price - entry) * centi / 100
        realized_centi_x100 = sum(
            (f.price_cents - opener.entry_price_cents) * f.quantity_centi
            for f in opener_fills if f.action == "sell"
        )
        opener.realized_pnl_cents = realized_centi_x100 // 100

        # exit_price_cents = centi-weighted avg of sell prices
        if sell_centi > 0:
            sell_weighted = sum(
                f.price_cents * f.quantity_centi
                for f in opener_fills if f.action == "sell"
            )
            opener.exit_price_cents = max(1, min(99, sell_weighted // sell_centi))

        await _recompute_bet_fees(session, bet=opener)

        if opener.remaining_quantity_centi == 0:
            opener.status = (
                BetStatus.WON if (opener.realized_pnl_cents or 0) > 0
                else BetStatus.LOST
            )
            opener.exit_type = ExitType.CLOSED_EARLY
            opener.settled_at = datetime.now(timezone.utc)
            opener.pnl_cents = opener.realized_pnl_cents

        opener.version += 1
        await session.flush()

        log.info(
            "bet_sell_chunk_applied",
            bet_id=opener.id,
            trade_id=fill.msg.trade_id,
            chunk_centi=chunk_centi,
            realized_pnl_cents=opener.realized_pnl_cents,
            remaining=opener.remaining_quantity,
            status=opener.status,
        )

        remaining_to_attribute_centi -= chunk_centi

    # If fills_sync had already populated the REST fee on the canonical row
    # before the WS sell arrived, pro-rate it across the row + any
    # cross-opener splits NOW — don't wait for the next fills_sync sweep
    # to heal the misallocation. Uses the same largest-remainder math as
    # fills_sync._ingest_rest_fill.
    if total_fee_to_split is not None and primary_bet_id is not None:
        all_rows = (await session.execute(
            select(BetFill).where(
                (BetFill.trade_id == fill.msg.trade_id)
                | (BetFill.trade_id.like(f"{fill.msg.trade_id}#%"))
            )
        )).scalars().all()
        total_centi = sum(r.quantity_centi for r in all_rows)
        if total_centi > 0 and len(all_rows) > 1:
            allocations: list[tuple[BetFill, int, int]] = []
            running = 0
            for r in all_rows:
                num = total_fee_to_split * r.quantity_centi
                base = num // total_centi
                allocations.append((r, base, num - base * total_centi))
                running += base
            leftover = total_fee_to_split - running
            allocations.sort(key=lambda x: x[2], reverse=True)
            touched: set[int] = set()
            for idx, (row, base, _rem) in enumerate(allocations):
                new_fee = base + (1 if idx < leftover else 0)
                if row.fee_cents != new_fee:
                    row.fee_cents = new_fee
                    if row.bet_id is not None:
                        touched.add(row.bet_id)
            await session.flush()
            for bid in touched:
                touched_bet = await session.get(Bet, bid)
                if touched_bet is not None:
                    await _recompute_bet_fees(session, bet=touched_bet)

    await session.flush()


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
        # The settlement price in the bet's own side-space. YES gets paid
        # `settlement_value_cents`; NO gets paid `100 - settlement_value_cents`.
        side_settle = (
            settlement_value_cents if bet.side == BetSide.YES
            else 100 - settlement_value_cents
        )
        # Settle only the remaining (unsold) centi. Earlier partial sells
        # already added their share of pnl to realized_pnl_cents. If
        # remaining_quantity_centi is 0 (already fully sold but somehow
        # still OPEN), held_centi is 0 — no double-count.
        held_centi = bet.remaining_quantity_centi
        settle_pnl = ((side_settle - bet.entry_price_cents) * held_centi) // 100
        bet.realized_pnl_cents = (bet.realized_pnl_cents or 0) + settle_pnl
        bet.remaining_quantity = 0
        bet.remaining_quantity_centi = 0

        # exit_price reflects how the bet ultimately resolved. If there
        # were earlier sells, use a centi-weighted blend; otherwise the
        # settlement price stands alone.
        sold_fills = (await session.execute(
            select(BetFill)
            .where(BetFill.bet_id == bet.id)
            .where(BetFill.action == "sell")
        )).scalars().all()
        sold_centi = sum(f.quantity_centi for f in sold_fills)
        if sold_centi > 0:
            sold_weighted_centi = sum(f.price_cents * f.quantity_centi for f in sold_fills)
            bet.exit_price_cents = (sold_weighted_centi + side_settle * held_centi) // (sold_centi + held_centi)
        elif held_centi > 0:
            bet.exit_price_cents = side_settle
        # else: bet was already fully closed via sells; exit_price set then.

        bet.pnl_cents = bet.realized_pnl_cents
        bet.status = BetStatus.WON if (bet.realized_pnl_cents or 0) > 0 else BetStatus.LOST
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
