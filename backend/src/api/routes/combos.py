"""Combo (multivariate / parlay) logging + placement.

A combo is one Kalshi multivariate-event market that bundles several legs and
settles as one atomic binary contract.

Two flows:
  - LOG (POST /combos): record a combo placed on kalshi.com. Ticker-in /
    auto-hydrate — the server reads legs from the market's `mve_selected_legs`,
    labels from `yes_sub_title`, and entry/qty/fees from the user's fills.
    source=EXTERNAL, verified=False (see feedback_no_external_fill_reconciliation:
    the app never auto-imports, but it records what the user asks it to).
  - PLACE via RFQ (POST /combos/rfq → quotes → POST /combos/accept): combos fill
    through request-for-quote, not the order book. /rfq materializes the combo
    market and requests a quote; the user picks the best maker offer and
    confirms; /accept takes it. No bet is recorded at accept — the async fill
    creates it (source=HUMAN, verified=True) keyed to the real order_id. Every
    write path enforces cross-market isolation per-leg via
    _assert_sports_leg_tickers; placement additionally requires the
    is_placeable_sports_combo allowlist.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.db import get_session
from src.core.exceptions import KalshiError
from src.core.logging import get_logger
from src.core.types import BetSide, Confidence, Strategy, Timing, utc_iso
from src.kalshi.rest import KalshiRestClient
from src.kalshi.schemas import (
    CreateRfqRequest,
    SelectedMarket,
)
from src.models import PendingCombo
from src.services.bet_service import (
    ComboLegInput,
    record_external_combo,
)
from src.sports.combo import (
    is_combo_ticker,
    is_placeable_sports_combo,
    is_sports_leg_ticker,
)
from src.sports.soccer import is_soccer_ticker

router = APIRouter()
log = get_logger(__name__)


class LegInput(BaseModel):
    """One leg the builder selected: a single market + the side."""
    market_ticker: str
    event_ticker: str
    side: Literal["yes", "no"]


def _subtitle_titles(yes_sub_title: str | None, expected_count: int) -> list[str | None]:
    """Decode a combo's yes_sub_title ("yes Canada,yes Georgia,…") into per-leg
    human labels, in leg order.

    Kalshi joins the labels with commas, so a label that itself contains a comma
    would split into too many segments and misalign every following leg. We
    guard against that: if the segment count doesn't match the leg count, we
    return all-None rather than mislabel — the leg_ticker is always correct, so
    a missing title degrades to showing the ticker, never the WRONG team.
    """
    if not yes_sub_title:
        return [None] * expected_count
    segs = [
        seg.strip().removeprefix("yes ").removeprefix("no ").strip()
        for seg in yes_sub_title.split(",")
        if seg.strip()
    ]
    if len(segs) != expected_count:
        return [None] * expected_count
    return list(segs)


class LogComboBody(BaseModel):
    ticker: str
    side: BetSide = BetSide.YES
    """Which side of the combo was bought. Combos are almost always bought YES
    (you back the parlay hitting); NO is allowed for completeness."""
    strategy: Strategy = Strategy.LOCK_PARLAY
    confidence: Confidence = Confidence.MEDIUM
    timing: Timing = Timing.PRE_MATCH
    tags: list[str] | None = None
    human_reasoning: str | None = None
    # Escape hatches: if the fills hydrate can't find the trade (rare), the
    # client may supply entry/qty directly rather than block the log.
    entry_price_cents: int | None = Field(default=None, ge=1, le=99)
    quantity: int | None = Field(default=None, ge=1)


def _parse_legs(market: dict[str, Any]) -> list[ComboLegInput]:
    """Build leg inputs from a combo market payload: structured leg refs from
    `mve_selected_legs`, human labels from `yes_sub_title` (same leg order)."""
    raw_legs = market.get("mve_selected_legs") or []
    titles = _subtitle_titles(market.get("yes_sub_title"), len(raw_legs))
    return [
        ComboLegInput(
            leg_ticker=leg.get("market_ticker"),
            leg_event_ticker=leg.get("event_ticker"),
            leg_title=titles[i],
            side=leg.get("side"),
        )
        for i, leg in enumerate(raw_legs)
    ]


@router.post("/combos")
async def log_combo(
    body: LogComboBody,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Log a combo placed on kalshi.com into the ledger, hydrating legs and
    entry from Kalshi by ticker."""
    if not is_combo_ticker(body.ticker):
        raise HTTPException(400, f"{body.ticker} is not a combo (multivariate) ticker")

    async with KalshiRestClient() as client:
        try:
            raw = await client.get_market(body.ticker)
        except Exception as e:  # noqa: BLE001 — surface Kalshi lookup failure to the user
            raise HTTPException(404, f"could not fetch combo market: {str(e)[:120]}")
        market = raw.get("market", raw)
        legs = _parse_legs(market)
        # Cross-market isolation: even though logging places no order, recording
        # a non-sports combo (one with a politics/crypto leg) would pull that
        # out-of-scope position into the ledger — the firewall says the app never
        # reflects non-sports positions. Gate on the same per-leg guard the
        # placement paths use. (is_combo_ticker above only confirms it's an MVE
        # market, not that its legs are in scope.) Refuse a legless market too:
        # with no legs we can't prove isolation, so don't record it blind.
        if not legs:
            raise HTTPException(
                422,
                f"{body.ticker} has no readable legs (mve_selected_legs) — "
                "cannot verify it's an all-sports combo, refusing to log.",
            )
        _assert_sports_leg_tickers([leg.leg_ticker for leg in legs])

        # Entry price + quantity + order_id from the user's own fills on this
        # ticker. The order_id lets fills_sync back-link the external bet_fill
        # (carrying Kalshi's real fee) to this bet.
        entry, qty, order_id = await _hydrate_entry_from_fills(
            client, body.ticker, body.side
        )
        entry_price_cents = (
            body.entry_price_cents if body.entry_price_cents is not None else entry
        )
        quantity = body.quantity if body.quantity is not None else qty

    if entry_price_cents is None or quantity is None:
        raise HTTPException(
            422,
            "no fills found for this combo on your account; pass entry_price_cents "
            "and quantity explicitly to log it anyway",
        )
    # A sub-contract fill (centi < 100) floor-divides to quantity 0; the Bet
    # CHECK requires >= 1. Refuse with a clean 422 instead of letting the
    # IntegrityError bubble up as a 500.
    if quantity < 1:
        raise HTTPException(
            422,
            f"combo resolved to {quantity} whole contracts (sub-contract fill); "
            "pass quantity explicitly to log it.",
        )

    bet = await record_external_combo(
        session,
        ticker=body.ticker,
        side=body.side,
        entry_price_cents=entry_price_cents,
        quantity=quantity,
        legs=legs,
        placed_at=datetime.now(timezone.utc),
        order_id=order_id,
        strategy=body.strategy,
        confidence=body.confidence,
        timing=body.timing,
        human_reasoning=body.human_reasoning,
        tags=body.tags,
    )
    await session.commit()
    return {
        "bet_id": bet.id,
        "ticker": body.ticker,
        "side": bet.side,
        "entry_price_cents": bet.entry_price_cents,
        "quantity": bet.quantity,
        "stake_cents": bet.stake_cents,
        "leg_count": len(legs),
        "legs": [
            {"title": leg.leg_title, "ticker": leg.leg_ticker, "side": leg.side}
            for leg in legs
        ],
        "placed_at": utc_iso(bet.placed_at),
    }


class RequestQuoteBody(BaseModel):
    legs: list[LegInput] = Field(min_length=2, max_length=8)
    contracts: int = Field(ge=1)
    """How many combo contracts to request a quote for."""


@router.post("/combos/rfq")
async def request_quote(body: RequestQuoteBody) -> dict[str, Any]:
    """Request a quote on a combo. Combos fill via RFQ, not a resting order:
    this materializes the combo, sends an RFQ, and returns the rfq_id. Market
    makers respond with quotes the UI then reads via /combos/rfq/{id}/quotes
    (and, later, a WS push). No money moves — quotes are just offers until the
    user accepts one."""
    collection = await _discover_collection(body.legs)
    selected = [
        SelectedMarket(
            market_ticker=l.market_ticker, event_ticker=l.event_ticker, side=l.side,
        ) for l in body.legs
    ]
    async with KalshiRestClient() as client:
        try:
            mk = await client.create_multivariate_market(
                collection_ticker=collection, legs=selected,
            )
            rfq = await client.create_rfq(CreateRfqRequest(
                market_ticker=mk.market_ticker,
                mve_collection_ticker=collection,
                mve_selected_legs=selected,
                contracts=body.contracts,
            ))
        except KalshiError as e:
            raise HTTPException(502, f"could not request quote: {str(e)[:160]}")
    return {
        "rfq_id": rfq.id,
        "ticker": mk.market_ticker,
        "leg_count": len(body.legs),
        "subtitle": mk.market.yes_sub_title if mk.market else None,
    }


@router.get("/combos/rfq/{rfq_id}/quotes")
async def get_quotes(rfq_id: str) -> dict[str, Any]:
    """Live quotes makers have offered on this RFQ. The UI polls this (until WS
    push lands) and shows the user the best one to accept."""
    async with KalshiRestClient() as client:
        try:
            uid = await client.get_account_user_id()
            resp = await client.get_my_quotes(user_id=uid)
        except KalshiError as e:
            raise HTTPException(502, f"could not fetch quotes: {str(e)[:160]}")
    quotes = [
        {
            "quote_id": q.id,
            "rfq_id": q.rfq_id,
            "status": q.status,
            "yes_bid_cents": q.yes_bid_cents,
            "no_bid_cents": q.no_bid_cents,
            "yes_contracts": q.yes_contracts,
            "no_contracts": q.no_contracts,
        }
        for q in resp.quotes
        if q.rfq_id == rfq_id and q.status == "open"
    ]
    return {"rfq_id": rfq_id, "quotes": quotes}


@router.delete("/combos/rfq/{rfq_id}")
async def cancel_rfq(rfq_id: str) -> dict[str, Any]:
    """Cancel an open RFQ the user abandoned (changed legs, navigated away).
    Keeps us under Kalshi's open-RFQ cap and gives makers a clean close. Best
    effort — a Kalshi error here isn't fatal (the RFQ expires on its own)."""
    # Validate shape before forwarding to Kalshi (rfq ids are UUIDs) — rejects a
    # malformed/crafted path segment and keeps it out of the structured logs.
    try:
        uuid.UUID(rfq_id)
    except ValueError:
        raise HTTPException(400, "invalid rfq_id")
    async with KalshiRestClient() as client:
        try:
            await client.delete_rfq(rfq_id)
        except KalshiError as e:
            log.info("combo_rfq_cancel_failed", rfq_id=rfq_id, error=str(e)[:120])
            return {"cancelled": False}
    return {"cancelled": True}


class AcceptQuoteBody(BaseModel):
    quote_id: str
    side: Literal["yes", "no"]
    """Which side of the quote to take (a maker may quote both)."""
    price_cents: int = Field(ge=1, le=99)
    """The quote's bid for the chosen side, echoed back for the ledger record."""
    count: int = Field(ge=1)
    legs: list[LegInput] = Field(min_length=2, max_length=8)
    ticker: str
    """The combo market_ticker from the RFQ."""
    strategy: Strategy = Strategy.LOCK_PARLAY
    confidence: Confidence = Confidence.MEDIUM
    timing: Timing = Timing.PRE_MATCH
    tags: list[str] | None = None
    human_reasoning: str | None = None
    acknowledged: bool = False
    """Accepting a quote IS placing the order — human-confirmed. Refused unless
    the user explicitly confirmed (clicked Accept)."""


@router.post("/combos/accept")
async def accept_quote(
    body: AcceptQuoteBody,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Accept a maker's quote — the human-confirmed order (refused unless
    acknowledged). We call accept ONLY: the MAKER confirms next, then Kalshi's
    execution timer fills the order async on the WS fill channel.

    No bet is recorded here (accept returns no order_id, and the fill may never
    happen if the maker doesn't confirm). Instead we stash the legs + metadata
    in pending_combo; when the real fill lands, record_fill creates the combo
    bet keyed to the fill's order_id with the real price/fees.

    Ordering is deliberate: the stash is committed BEFORE the Kalshi accept, so
    a fill that lands the instant the maker confirms always finds it (the fill
    handler runs in a separate session/task — if we accepted first, the fill
    could race ahead of the commit and be dropped, losing a real order). If the
    accept then fails on Kalshi, we delete the stash (no order, no record)."""
    if not body.acknowledged:
        raise HTTPException(
            409, detail={"reasons": ["Accepting a quote must be confirmed by you."]}
        )
    if not is_combo_ticker(body.ticker):
        raise HTTPException(400, f"{body.ticker} is not a combo ticker")
    if not is_placeable_sports_combo(body.ticker):
        # Allowlist: only the sports multi-game parlay series is placeable. Any
        # other MVE family (cross-category, or a new series) can bundle a
        # non-sports leg the client may omit from body.legs — validating
        # body.legs wouldn't catch it. Refuse on the money path (isolation).
        raise HTTPException(
            422, f"{body.ticker} is not a sports parlay — only "
                 "KXMVESPORTSMULTIGAMEEXTENDED combos can be placed here.",
        )
    # Per-leg isolation on the money path — same guard as /rfq and /place.
    _validate_sports_legs(body.legs)

    # 0) Verify the quote actually belongs to the combo the user staked. The
    # guards above only checked body.ticker/body.legs (what the client CLAIMS);
    # accept_quote moves money on whatever market quote_id points at. A stale or
    # buggy client could send a quote_id for a different (even out-of-scope)
    # market while passing sports-only legs — without this the order lands on the
    # wrong market and the ledger records the wrong legs. Done BEFORE the stash
    # so a refusal leaves nothing behind.
    async with KalshiRestClient() as client:
        try:
            uid = await client.get_account_user_id()
            my_quotes = await client.get_my_quotes(user_id=uid)
        except KalshiError as e:
            raise HTTPException(502, f"kalshi: could not verify quote: {str(e)[:160]}") from e
    quote = next((q for q in my_quotes.quotes if q.id == body.quote_id), None)
    if quote is None:
        raise HTTPException(404, f"quote {body.quote_id} not found among your open quotes")
    if quote.market_ticker != body.ticker:
        log.warning(
            "combo_accept_ticker_mismatch",
            quote_id=body.quote_id, quote_ticker=quote.market_ticker,
            body_ticker=body.ticker,
        )
        raise HTTPException(
            409,
            f"quote {body.quote_id} is for {quote.market_ticker}, "
            f"not {body.ticker} — refusing to accept.",
        )

    # 1) Stash the legs FIRST and commit, so the async fill can always find it.
    # Upsert on the unique combo_ticker; refresh created_at so the TTL sweep
    # measures from THIS accept, not a stale earlier one.
    legs_json = [
        {"leg_ticker": l.market_ticker, "leg_event_ticker": l.event_ticker,
         "leg_title": None, "side": l.side}
        for l in body.legs
    ]
    existing = await session.scalar(
        select(PendingCombo).where(PendingCombo.combo_ticker == body.ticker)
    )
    if existing is not None:
        existing.side = body.side
        existing.count = body.count
        existing.legs_json = legs_json
        existing.strategy = body.strategy.value
        existing.confidence = body.confidence.value
        existing.timing = body.timing.value
        existing.tags_json = body.tags
        existing.human_reasoning = body.human_reasoning
        existing.created_at = datetime.now(timezone.utc)
    else:
        session.add(PendingCombo(
            combo_ticker=body.ticker,
            side=body.side,
            count=body.count,
            legs_json=legs_json,
            strategy=body.strategy.value,
            confidence=body.confidence.value,
            timing=body.timing.value,
            tags_json=body.tags,
            human_reasoning=body.human_reasoning,
        ))
    await session.commit()

    # 2) Now accept on Kalshi. On failure we deliberately LEAVE the stash, not
    # delete it: a failure here is ambiguous — the client wraps a network
    # timeout as KalshiError, and Kalshi may have accepted the quote even though
    # we didn't get the response. Deleting the stash in that case would drop the
    # real fill from the ledger (the exact loss we're guarding against). Leaving
    # it is safe: if the order truly didn't place, the TTL sweep removes the
    # stash within the window (no fill ever binds); if it did place, the fill
    # binds correctly. The only cost is a stale stash lingering up to the TTL.
    # body.side is the position the user wants to HOLD. Kalshi's accepted_side
    # is which BID you take, and taking the yes-bid makes you the NO holder (a
    # yes bid at X == a no ask at 100-X). So accepted_side is the OPPOSITE of the
    # side you want to hold.
    accepted_side = "no" if body.side == "yes" else "yes"
    async with KalshiRestClient() as client:
        try:
            await client.accept_quote(body.quote_id, side=accepted_side)
        except KalshiError as e:
            log.warning(
                "combo_accept_kalshi_error",
                quote_id=body.quote_id, ticker=body.ticker, error=str(e),
            )
            raise HTTPException(502, f"kalshi: {str(e)[:160]}") from e
        except Exception:  # noqa: BLE001 — surface the real exception, not a bare 500
            log.exception("combo_accept_unexpected", quote_id=body.quote_id)
            raise
        # Confirm starts the execution timer, but accept often fills immediately
        # on a crossing quote — in which case confirm 4xxs (already executed).
        # That's NOT a failure: the order is placed. Best-effort, never fail the
        # request on it (a false 500 here makes the user think nothing happened
        # and retry → double order).
        try:
            await client.confirm_quote(body.quote_id)
        except Exception as e:  # noqa: BLE001 — confirm failing never fails the order
            log.info(
                "combo_confirm_skipped",
                quote_id=body.quote_id, reason=str(e)[:120],
            )

    return {
        "accepted": True,
        "ticker": body.ticker,
        "side": body.side,
        "count": body.count,
        "leg_count": len(body.legs),
        "note": "Quote accepted. The order fills once the maker confirms; it "
                "appears in your ledger when it fills.",
    }


def _assert_sports_leg_tickers(tickers: list[str | None]) -> None:
    """Per-leg cross-market isolation (defense in depth): refuse a combo whose
    legs aren't all sports markets app-side rather than trusting Kalshi — a
    crafted or stale out-of-scope leg (politics/weather/crypto) must never reach
    a write path. The single source of truth for the per-leg guard; every combo
    write path (rfq, place, accept, log) funnels through it. Raises 422 on the
    first out-of-scope (or missing) leg ticker."""
    for t in tickers:
        if not t or not (is_soccer_ticker(t) or is_sports_leg_ticker(t)):
            raise HTTPException(
                422,
                f"leg {t} is not a sports market — combos may only bundle "
                "sports legs (soccer, NBA, NFL, NHL, MLB, UFC).",
            )


def _validate_sports_legs(legs: list[LegInput]) -> None:
    """Per-leg isolation for the placement-body leg shape (rfq/accept)."""
    _assert_sports_leg_tickers([leg.market_ticker for leg in legs])


async def _discover_collection(legs: list[LegInput]) -> str:
    """Validate every leg is an in-scope sports market, then find the
    multivariate collection that hosts them (by the first leg's event). Raises
    422 if a leg is out of scope or no sports collection contains the first leg.
    """
    _validate_sports_legs(legs)
    async with KalshiRestClient() as client:
        try:
            collection = await client.find_collection_for_event(legs[0].event_ticker)
        except KalshiError as e:
            # Surface as 502 like the rest of the route, not an unhandled 500.
            raise HTTPException(502, f"collection lookup failed: {str(e)[:160]}") from e
    if collection is None:
        raise HTTPException(
            422,
            f"no combo collection found for {legs[0].event_ticker} — "
            "is it a sports event Kalshi offers in combos?",
        )
    return collection


async def _hydrate_entry_from_fills(
    client: KalshiRestClient, ticker: str, side: BetSide
) -> tuple[int | None, int | None, str | None]:
    """Find the user's buy on this combo and return
    (entry_price_cents, qty, order_id).

    A combo buy is a single execution; we sum buy-side centi on the chosen side
    and take the centi-weighted average price, mirroring the bet aggregate math.
    order_id is the buy's Kalshi order id (used to back-link the fill's real
    fee). Returns (None, None, None) if no matching fill is found.
    """
    total_centi = 0
    weighted = 0
    order_id: str | None = None
    cursor: str | None = None
    while True:
        resp = await client.get_fills(ticker=ticker, cursor=cursor)
        for f in resp.fills:
            if f.action != "buy" or f.side != side.value:
                continue
            price = f.yes_price if f.side == "yes" else f.no_price
            total_centi += f.count_centi
            weighted += price * f.count_centi
            if order_id is None:
                order_id = f.order_id
        cursor = resp.cursor or None
        if not cursor:
            break
    if total_centi <= 0:
        return None, None, None
    avg_price = max(1, min(99, weighted // total_centi))
    return avg_price, total_centi // 100, order_id
