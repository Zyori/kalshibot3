"""Combo (multivariate / parlay) logging.

A combo is one Kalshi multivariate-event market that bundles several legs and
settles as one atomic binary contract. The user places combos on kalshi.com;
this endpoint logs them into the ledger as a deliberate record (source=EXTERNAL,
verified=False — see feedback_no_external_fill_reconciliation: the app never
auto-imports, but it records what the user asks it to).

Design: ticker-in, auto-hydrate. The client posts just the full combo ticker
(plus optional reflective metadata); the server reads everything else from
Kalshi — legs from the market's `mve_selected_legs`, human labels from
`yes_sub_title`, and entry price / quantity / fees from the fills endpoint.
This keeps a money record from depending on hand-typed numbers.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.db import get_session
from src.core.logging import get_logger
from src.core.types import BetSide, Confidence, Strategy, Timing, utc_iso
from src.kalshi.rest import KalshiRestClient
from src.services.bet_service import ComboLegInput, record_external_combo
from src.sports.combo import is_combo_ticker

router = APIRouter()
log = get_logger(__name__)


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
