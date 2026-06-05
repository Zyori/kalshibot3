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

from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.db import get_session
from src.core.logging import get_logger
from src.core.types import BetSide, BetSource, Confidence, Strategy, Timing, utc_iso
from src.core.exceptions import KalshiError
from src.kalshi.rest import KalshiRestClient, new_client_order_id
from src.kalshi.schemas import (
    CreateMultivariateMarketResponse,
    PlaceOrderRequest,
    SelectedMarket,
)
from src.services.bet_service import (
    ComboLegInput,
    record_external_combo,
    record_placed_order,
)
from src.sports.combo import is_combo_ticker

router = APIRouter()
log = get_logger(__name__)


class LegInput(BaseModel):
    """One leg the builder selected: a single market + the side, with an
    optional human label for the ledger (the builder knows it from the market)."""
    market_ticker: str
    event_ticker: str
    side: Literal["yes", "no"]
    title: str | None = None


def _materialized_legs(
    legs: list[LegInput], materialized: CreateMultivariateMarketResponse
) -> list[ComboLegInput]:
    """Build combo_leg inputs from the builder's legs, preferring the human
    labels Kalshi echoes back in yes_sub_title (same order as the legs) over the
    builder-supplied titles."""
    titles: list[str] = []
    if materialized.market and materialized.market.yes_sub_title:
        titles = [
            seg.strip().removeprefix("yes ").removeprefix("no ").strip()
            for seg in materialized.market.yes_sub_title.split(",")
            if seg.strip()
        ]
    out: list[ComboLegInput] = []
    for i, leg in enumerate(legs):
        out.append(ComboLegInput(
            leg_ticker=leg.market_ticker,
            leg_event_ticker=leg.event_ticker,
            leg_title=(titles[i] if i < len(titles) else leg.title),
            side=leg.side,
        ))
    return out


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
    `mve_selected_legs`, human labels zipped from `yes_sub_title` (which lists
    the legs in the same order, e.g. "yes Canada,yes Georgia,...")."""
    raw_legs = market.get("mve_selected_legs") or []
    sub = market.get("yes_sub_title") or ""
    # "yes Canada,yes Georgia" -> ["Canada", "Georgia"]; drop the side prefix.
    titles = [
        seg.strip().removeprefix("yes ").removeprefix("no ").strip()
        for seg in sub.split(",")
        if seg.strip()
    ]
    legs: list[ComboLegInput] = []
    for i, leg in enumerate(raw_legs):
        legs.append(ComboLegInput(
            leg_ticker=leg.get("market_ticker"),
            leg_event_ticker=leg.get("event_ticker"),
            leg_title=titles[i] if i < len(titles) else None,
            side=leg.get("side"),
        ))
    return legs


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
        "yes_bid": m.yes_bid if m else None,
        "yes_ask": m.yes_ask if m else None,
        "no_bid": m.no_bid if m else None,
        "no_ask": m.no_ask if m else None,
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
            log.warning("combo_place_kalshi_error", ticker=mk.market_ticker, error=str(e))
            raise HTTPException(502, f"kalshi: {str(e)[:160]}") from e

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


async def _discover_collection(legs: list[LegInput]) -> str:
    """Find the multivariate collection that hosts these legs (by the first
    leg's event). Raises 422 if no sports collection contains it."""
    async with KalshiRestClient() as client:
        collection = await client.find_collection_for_event(legs[0].event_ticker)
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
