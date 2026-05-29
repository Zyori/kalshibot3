"""Order placement API.

Three endpoints:
  POST /api/orders/preview   compute sanity verdict + total cost, no order
  POST /api/orders/place     run sanity + place via Kalshi REST + record BET
  DELETE /api/orders/{order_id}  cancel a resting order via Kalshi REST

The preview endpoint exists so the OrderPanel can render warnings live as
the user types, without burning a Kalshi rate-limit slot per keystroke.
Place runs the same sanity check server-side as defense-in-depth — the
frontend's check is convenience, the server's is policy.

Cross-market isolation: every endpoint checks is_soccer_ticker(ticker)
before doing anything. A request to place a politics order returns 400.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select

from src.core.db import get_session
from src.core.exceptions import KalshiError
from src.core.logging import get_logger
from src.kalshi.rest import KalshiRestClient, new_client_order_id
from src.kalshi.schemas import PlaceOrderRequest
from src.core.types import BetStatus, ExitType
from src.models import Market, Position
from src.services.bet_service import mark_bet_terminal_by_order_id, record_placed_order
from src.services.order_sanity import SanityInput, Verdict, check_order
from src.sports.soccer import is_soccer_ticker

router = APIRouter()
log = get_logger(__name__)


class OrderRequestBody(BaseModel):
    ticker: str
    side: Literal["yes", "no"]
    action: Literal["buy", "sell"]
    count: int = Field(ge=1)
    price_cents: int = Field(ge=1, le=99)
    """For YES orders this is yes_price; for NO orders this is no_price."""
    post_only: bool = False
    acknowledged_loud: bool = False
    """Set true by the frontend after the user confirmed a LOUD_CONFIRM dialog.
    If the server-side sanity check still returns LOUD_CONFIRM and this is
    false, /place refuses."""


class OrderPreviewResponse(BaseModel):
    verdict: Verdict
    reasons: list[str]
    total_cost_cents: int
    """count * price_cents. Fees aren't surfaced in the preview — they're
    populated after the fact from Kalshi's per-fill fee_cost (see
    fills_sync.py) since fee rates vary by maker/taker and price tier and
    we don't estimate."""


def _book_snapshot(request: Request, ticker: str) -> dict[str, int | None]:
    """Pull current top-of-book for the ticker from LiveState."""
    supervisor = getattr(request.app.state, "supervisor", None)
    if supervisor is None:
        return {"yes_best_bid": None, "yes_best_ask": None,
                "no_best_bid": None, "no_best_ask": None,
                "yes_top_qty": None, "no_top_qty": None}
    book = supervisor.live_state.books.get(ticker)
    if book is None:
        return {"yes_best_bid": None, "yes_best_ask": None,
                "no_best_bid": None, "no_best_ask": None,
                "yes_top_qty": None, "no_top_qty": None}
    yes_ask = book.yes_best_ask
    no_ask = book.no_best_ask
    return {
        "yes_best_bid": book.yes_best_bid,
        "yes_best_ask": yes_ask,
        "no_best_bid": book.no_best_bid,
        "no_best_ask": no_ask,
        # int_levels() — levels store exact floats; the sanity guard's qty
        # fields are int. Round on read at this boundary (not raw .levels).
        "yes_top_qty": book.yes.int_levels().get(yes_ask) if yes_ask is not None else None,
        "no_top_qty": book.no.int_levels().get(no_ask) if no_ask is not None else None,
    }


def _make_sanity_input(body: OrderRequestBody, snapshot: dict) -> SanityInput:
    return SanityInput(
        side=body.side,
        action=body.action,
        price_cents=body.price_cents,
        count=body.count,
        **snapshot,
    )


async def _open_position_qty(
    session: AsyncSession, *, ticker: str, side: str,
) -> int:
    """Returns the quantity we currently hold on (ticker, side), or 0.

    Source of truth is the Position table which position_sync keeps in sync
    with Kalshi every 60s + after every fill. Reading from the DB here
    avoids a Kalshi round-trip on every place_order call.
    """
    qty = await session.scalar(
        select(Position.quantity)
        .join(Market, Market.id == Position.market_id)
        .where(Market.kalshi_ticker == ticker, Position.side == side)
    )
    return int(qty) if qty is not None else 0


@router.post("/orders/preview")
async def preview_order(body: OrderRequestBody, request: Request) -> OrderPreviewResponse:
    """No side effects — compute the verdict + total cost for the UI."""
    if not is_soccer_ticker(body.ticker):
        raise HTTPException(status_code=400, detail=f"{body.ticker} is not a soccer market")

    snapshot = _book_snapshot(request, body.ticker)
    result = check_order(_make_sanity_input(body, snapshot))
    return OrderPreviewResponse(
        verdict=result.verdict,
        reasons=result.reasons,
        total_cost_cents=body.count * body.price_cents,
    )


@router.post("/orders/place")
async def place_order(
    body: OrderRequestBody,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Place a limit order. Returns the BET row + Kalshi order details.

    Sanity verdict gates the call:
      HARD_REFUSE     → 400 with reasons
      LOUD_CONFIRM    → 400 unless body.acknowledged_loud == True
      SOFT_WARN / OK  → proceeds; reasons returned for the UI to display
    """
    if not is_soccer_ticker(body.ticker):
        raise HTTPException(status_code=400, detail=f"{body.ticker} is not a soccer market")

    # Ghost-share guard: refuse sell orders against a side we don't hold.
    # Kalshi treats "sell YES" with no YES position as "open a short YES",
    # which is mathematically a "buy NO". Users on prior versions (and on
    # kalshi.com itself) have gotten confused into accidental opposite-side
    # exposure this way. This guard makes the rule explicit: if you want
    # NO exposure, buy NO — don't sell YES short. The frontend disables
    # the button too; this is defense in depth.
    if body.action == "sell":
        held = await _open_position_qty(session, ticker=body.ticker, side=body.side)
        if held < body.count:
            raise HTTPException(
                status_code=400,
                detail={
                    "reasons": [
                        f"Refusing to sell {body.count} {body.side.upper()}: "
                        f"current {body.side.upper()} position is {held}. "
                        f"To take the opposite side, buy {('no' if body.side == 'yes' else 'yes').upper()} instead."
                    ],
                },
            )

    snapshot = _book_snapshot(request, body.ticker)
    sanity = check_order(_make_sanity_input(body, snapshot))

    if sanity.verdict == Verdict.HARD_REFUSE:
        raise HTTPException(status_code=400, detail={"reasons": sanity.reasons})
    if sanity.verdict == Verdict.LOUD_CONFIRM and not body.acknowledged_loud:
        raise HTTPException(
            status_code=409,
            detail={"verdict": "loud_confirm", "reasons": sanity.reasons},
        )

    client_order_id = new_client_order_id()
    req = PlaceOrderRequest(
        ticker=body.ticker,
        side=body.side,
        action=body.action,
        count=body.count,
        yes_price=body.price_cents if body.side == "yes" else None,
        no_price=body.price_cents if body.side == "no" else None,
        client_order_id=client_order_id,
        post_only=body.post_only,
    )

    async with KalshiRestClient() as client:
        try:
            resp = await client.place_order(req)
        except KalshiError as e:
            log.warning("place_order_kalshi_error", ticker=body.ticker, error=str(e))
            raise HTTPException(status_code=502, detail=f"kalshi: {e}") from e

    bet = await record_placed_order(
        session,
        order=resp.order,
        client_order_id=client_order_id,
        requested_count=body.count,
        requested_price_cents=body.price_cents,
        action=body.action,
    )
    await session.commit()

    # Kick a position sync — Kalshi's REST may have filled the order
    # immediately, in which case our POSITION table needs to know now,
    # not 60s later when the next poll cycle hits.
    supervisor = getattr(request.app.state, "supervisor", None)
    if supervisor is not None:
        # Don't await — the caller doesn't need to wait for reconciliation
        # to get its response. Failures log themselves inside _tick.
        import asyncio as _asyncio
        _asyncio.create_task(supervisor.position_syncer.trigger())

    return {
        "bet_id": bet.id if bet is not None else None,
        "kalshi_order_id": resp.order.order_id,
        "client_order_id": client_order_id,
        "status": resp.order.status,
        "ticker": resp.order.ticker,
        "side": resp.order.side,
        "count": resp.order.count,
        "remaining_count": resp.order.remaining_count,
        "yes_price_cents": resp.order.yes_price,
        "no_price_cents": resp.order.no_price,
        "sanity_reasons": sanity.reasons,
    }


@router.get("/orders")
async def list_orders(
    status: str = "resting",
    ticker: str | None = None,
) -> dict[str, Any]:
    """List orders from Kalshi REST (source of truth) — soccer-only.

    Cross-market isolation: politics/crypto/etc. orders on the same Kalshi
    account are filtered out before serving. The user has $200+ notional
    in non-soccer markets that this app must never display or act on.

    Wire format is whatever Kalshi sends, with prices normalized to cents
    and counts as ints so the frontend doesn't have to know about the
    dollar-string / float-string conventions.
    """
    async with KalshiRestClient() as client:
        raw = await client.get_orders(status=status, ticker=ticker, limit=200)

    out: list[dict[str, Any]] = []
    for o in raw.get("orders", []) or []:
        t = o.get("ticker") or o.get("market_ticker") or ""
        if not is_soccer_ticker(t):
            continue
        yes_price_raw = o.get("yes_price_dollars") or o.get("yes_price")
        no_price_raw = o.get("no_price_dollars") or o.get("no_price")
        remaining_raw = o.get("remaining_count_fp") or o.get("remaining_count") or 0
        initial_raw = o.get("initial_count_fp") or o.get("initial_count") or 0
        out.append({
            "order_id": o.get("order_id"),
            "client_order_id": o.get("client_order_id") or None,
            "ticker": t,
            "side": o.get("side"),
            "action": o.get("action"),
            "status": o.get("status"),
            "yes_price_cents": _normalize_price_cents(yes_price_raw),
            "no_price_cents": _normalize_price_cents(no_price_raw),
            "remaining_count": int(float(remaining_raw)) if remaining_raw is not None else 0,
            "initial_count": int(float(initial_raw)) if initial_raw is not None else 0,
            "created_time": o.get("created_time"),
        })
    return {"orders": out}


def _normalize_price_cents(raw: Any) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        return int(round(float(raw) * 100))
    return int(raw)


@router.delete("/orders/{order_id}")
async def cancel_order(
    order_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Cancel a resting order. Returns the updated order state.

    No is_soccer_ticker check here because we don't know the ticker yet —
    Kalshi's response carries it. We log a warning + refuse to surface to
    the UI if the cancelled order isn't soccer (cross-market isolation:
    if a malicious caller somehow gets an order_id from another market,
    we don't leak its details back through this app).

    After Kalshi confirms the cancel, transition the matching BET row to
    CANCELLED status so the bankroll-deployed math doesn't keep counting
    its stake as in-flight. (The WS user_order handler does the same on
    its side; this is the synchronous path so the route caller sees the
    state change before the next /api/ledger/stats poll.)
    """
    async with KalshiRestClient() as client:
        try:
            resp = await client.cancel_order(order_id)
        except KalshiError as e:
            log.warning("cancel_order_kalshi_error", order_id=order_id, error=str(e))
            raise HTTPException(status_code=502, detail=f"kalshi: {e}") from e

    ticker = resp.order.ticker
    if not is_soccer_ticker(ticker):
        log.error(
            "cancel_order_returned_non_soccer_ticker",
            order_id=order_id, ticker=ticker,
        )
        raise HTTPException(status_code=400, detail="cross-market isolation violation")

    await mark_bet_terminal_by_order_id(
        session,
        order_id=order_id,
        status=BetStatus.CANCELLED,
    )
    await session.commit()

    return {
        "kalshi_order_id": resp.order.order_id,
        "ticker": resp.order.ticker,
        "status": resp.order.status,
        "reduced_by": resp.reduced_by,
    }
