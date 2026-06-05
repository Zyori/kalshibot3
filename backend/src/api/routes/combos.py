"""Combo (multivariate / parlay) logging + placement.

A combo is one Kalshi multivariate-event market that bundles several legs and
settles as one atomic binary contract.

Two flows:
  - LOG (POST /combos): record a combo placed on kalshi.com. Ticker-in /
    auto-hydrate — the server reads legs from the market's `mve_selected_legs`,
    labels from `yes_sub_title`, and entry/qty/fees from the user's fills.
    source=EXTERNAL, verified=False (see feedback_no_external_fill_reconciliation:
    the app never auto-imports, but it records what the user asks it to).
  - PLACE (POST /combos/place): build a combo from legs and place it on Kalshi
    through the same staged + human-confirm path as every order (no autonomous
    trading). The server discovers the collection, materializes the combo
    market (idempotent), then places a standard order and records the bet with
    its legs captured direct from the builder. source=HUMAN, verified=True.
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
from src.models import PendingCombo
from src.core.logging import get_logger
from src.core.types import BetSide, BetSource, Confidence, Strategy, Timing, utc_iso
from src.core.exceptions import KalshiError
from src.kalshi.rest import KalshiRestClient, new_client_order_id
from src.kalshi.schemas import (
    CreateMultivariateMarketResponse,
    CreateRfqRequest,
    PlaceOrderRequest,
    SelectedMarket,
)
from src.services.bet_service import (
    ComboLegInput,
    record_external_combo,
    record_placed_order,
)
from src.sports.combo import (
    is_combo_ticker,
    is_cross_category_ticker,
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


def _materialized_legs(
    legs: list[LegInput], materialized: CreateMultivariateMarketResponse
) -> list[ComboLegInput]:
    """Build combo_leg inputs from the builder's legs, with the human labels
    Kalshi echoes back in yes_sub_title (same order as the legs)."""
    sub = materialized.market.yes_sub_title if materialized.market else None
    titles = _subtitle_titles(sub, len(legs))
    return [
        ComboLegInput(
            leg_ticker=leg.market_ticker,
            leg_event_ticker=leg.event_ticker,
            leg_title=titles[i],
            side=leg.side,
        )
        for i, leg in enumerate(legs)
    ]


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


class MaterializeBody(BaseModel):
    legs: list[LegInput] = Field(min_length=2, max_length=8)


@router.post("/combos/materialize")
async def materialize_combo(body: MaterializeBody) -> dict[str, Any]:
    """Stage step: turn a set of legs into a real combo market and return its
    ticker + current book. Idempotent on Kalshi's side (same legs → same
    ticker, no extra creation consumed), so the builder can call it freely as
    the user assembles legs. Places NO order."""
    collection = await _discover_collection(body.legs)
    async with KalshiRestClient() as client:
        try:
            mk = await client.create_multivariate_market(
                collection_ticker=collection,
                legs=[SelectedMarket(
                    market_ticker=l.market_ticker,
                    event_ticker=l.event_ticker,
                    side=l.side,
                ) for l in body.legs],
            )
        except KalshiError as e:
            raise HTTPException(502, f"could not materialize combo: {str(e)[:160]}")
    m = mk.market
    return {
        "ticker": mk.market_ticker,
        "event_ticker": mk.event_ticker,
        "subtitle": m.yes_sub_title if m else None,
        # _cents suffix per the project-wide price-field convention.
        "yes_bid_cents": m.yes_bid if m else None,
        "yes_ask_cents": m.yes_ask if m else None,
        "no_bid_cents": m.no_bid if m else None,
        "no_ask_cents": m.no_ask if m else None,
        "leg_count": len(body.legs),
    }


class PlaceComboBody(BaseModel):
    legs: list[LegInput] = Field(min_length=2, max_length=8)
    side: Literal["yes", "no"] = "yes"
    price_cents: int = Field(ge=1, le=99)
    count: int = Field(ge=1)
    strategy: Strategy = Strategy.LOCK_PARLAY
    confidence: Confidence = Confidence.MEDIUM
    timing: Timing = Timing.PRE_MATCH
    tags: list[str] | None = None
    human_reasoning: str | None = None
    acknowledged: bool = False
    """The user confirmed the staged combo. Placement is human-confirmed — the
    frontend stages then the user presses confirm, mirroring every other order.
    Refused unless true."""


@router.post("/combos/place")
async def place_combo(
    body: PlaceComboBody,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Place a combo on Kalshi and record it. Materializes the combo market
    from the legs, then places a standard limit order at the user's price.
    Human-confirmed only (no autonomous trading): refuses unless acknowledged.

    A fresh combo is often illiquid (no book), so this is a deliberate LIMIT
    order at the user's price — we don't auto-derive a market price."""
    if not body.acknowledged:
        raise HTTPException(
            409, detail={"reasons": ["Combo placement must be confirmed by you."]}
        )

    collection = await _discover_collection(body.legs)
    client_order_id = new_client_order_id()
    async with KalshiRestClient() as client:
        try:
            mk = await client.create_multivariate_market(
                collection_ticker=collection,
                legs=[SelectedMarket(
                    market_ticker=l.market_ticker,
                    event_ticker=l.event_ticker,
                    side=l.side,
                ) for l in body.legs],
            )
        except KalshiError as e:
            raise HTTPException(502, f"could not materialize combo: {str(e)[:160]}")

        req = PlaceOrderRequest(
            ticker=mk.market_ticker,
            side=body.side,
            action="buy",
            count=body.count,
            yes_price=body.price_cents if body.side == "yes" else None,
            no_price=body.price_cents if body.side == "no" else None,
            client_order_id=client_order_id,
            post_only=False,
        )
        try:
            resp = await client.place_order(req)
        except KalshiError as e:
            # Include the materialized ticker so the user can recover (re-stage
            # or log it) — the combo market exists on Kalshi even though the
            # order didn't land. Materialize is idempotent, so retrying is safe.
            log.warning("combo_place_kalshi_error", ticker=mk.market_ticker, error=str(e))
            raise HTTPException(
                502,
                detail={"error": f"kalshi: {str(e)[:160]}", "combo_ticker": mk.market_ticker},
            ) from e

    bet = await record_placed_order(
        session,
        order=resp.order,
        client_order_id=client_order_id,
        requested_count=body.count,
        requested_price_cents=body.price_cents,
        action="buy",
        source=BetSource.HUMAN,
        strategy=body.strategy,
        confidence=body.confidence,
        timing=body.timing,
        human_reasoning=body.human_reasoning,
        combo_legs=_materialized_legs(body.legs, mk),
    )
    if bet is None:
        raise HTTPException(500, "combo placed but bet not recorded")
    if body.tags:
        bet.tags = body.tags
    await session.commit()
    return {
        "bet_id": bet.id,
        "ticker": mk.market_ticker,
        "side": bet.side,
        "entry_price_cents": bet.entry_price_cents,
        "quantity": bet.quantity,
        "stake_cents": bet.stake_cents,
        "leg_count": len(body.legs),
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
    if is_cross_category_ticker(body.ticker):
        # Cross-category combos can bundle a non-sports leg Kalshi tracks but the
        # client may omit from body.legs — validating body.legs wouldn't catch
        # it. Refuse the whole class on the money path (isolation).
        raise HTTPException(
            422, f"{body.ticker} is a cross-category combo — not supported "
                 "(may bundle non-sports legs). Build a sports-only combo.",
        )
    # Per-leg isolation on the money path — same guard as /rfq and /place.
    _validate_sports_legs(body.legs)

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

    # 2) Now accept on Kalshi. If it fails, the order never placed — delete the
    # stash so a later unrelated fill on this ticker can't bind to it.
    async with KalshiRestClient() as client:
        try:
            await client.accept_quote(body.quote_id)
        except KalshiError as e:
            log.warning("combo_accept_kalshi_error", quote_id=body.quote_id, error=str(e))
            stash = await session.scalar(
                select(PendingCombo).where(PendingCombo.combo_ticker == body.ticker)
            )
            if stash is not None:
                await session.delete(stash)
                await session.commit()
            raise HTTPException(502, f"kalshi: {str(e)[:160]}") from e

    return {
        "accepted": True,
        "ticker": body.ticker,
        "side": body.side,
        "count": body.count,
        "leg_count": len(body.legs),
        "note": "Quote accepted. The order fills once the maker confirms; it "
                "appears in your ledger when it fills.",
    }


def _validate_sports_legs(legs: list[LegInput]) -> None:
    """Per-leg cross-market isolation (defense in depth): refuse a combo whose
    legs aren't all sports markets app-side rather than trusting Kalshi — a
    crafted out-of-scope leg (politics/weather/crypto) must never reach a path
    that places an order. Used by every combo write path (rfq, place, accept).
    Raises 422 if any leg is out of scope."""
    for leg in legs:
        if not (is_soccer_ticker(leg.market_ticker)
                or is_sports_leg_ticker(leg.market_ticker)):
            raise HTTPException(
                422,
                f"leg {leg.market_ticker} is not a sports market — combos may "
                "only bundle sports legs (soccer, NBA, NFL, NHL, MLB, UFC).",
            )


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
