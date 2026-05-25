"""Pydantic models for the Kalshi wire format.

This file is the **boundary**. Two rules:

  1. Dollar→cents and cents→dollar conversion happens ONLY here. The rest of
     the codebase assumes integer cents everywhere.
  2. Field validators reject malformed input as early as possible — a bad
     response from Kalshi should raise here, not silently corrupt the DB later.

Kalshi quotes binary-contract prices as integer cents 1–99 over the API
(`yes_price`, `no_price`, `yes_bid`, etc. all come back as cents already), so
in practice the cents↔dollar gymnastics only apply to the `notional_value`
fields on portfolio responses (balance, positions). Those arrive as cents too
in current API versions, but we still gate them through this layer so a future
change to the upstream format affects exactly one file.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# === Conversion helpers — single source of truth ===

def dollars_to_cents(amount: float | int) -> int:
    """Convert a dollar amount to integer cents. Banker's rounding."""
    return int(round(amount * 100))


def cents_to_dollars(cents: int) -> float:
    """Convert integer cents back to dollars (for display only)."""
    return cents / 100.0


# === Common base ===

class WireModel(BaseModel):
    """Base class for every wire-format schema. Forbids extra fields so an
    unexpected field in Kalshi's response surfaces immediately rather than
    being silently swallowed."""

    model_config = ConfigDict(extra="forbid")


# Allow Kalshi to add fields without breaking us. For models where forward-
# compatibility matters more than strictness (most read-only response models),
# subclass from this.
class WireModelLoose(BaseModel):
    model_config = ConfigDict(extra="ignore")


# === Auth / health ===

class BalanceResponse(WireModelLoose):
    """`GET /portfolio/balance` — balance in integer cents."""

    balance: int = Field(description="Available balance in cents.")


# === Markets / events ===

class Market(WireModelLoose):
    """A single Kalshi binary-contract market.

    Prices arrive as integer cents in the 1–99 range when set, or None for
    illiquid markets with no bid/ask.
    """

    ticker: str
    event_ticker: str | None = None
    title: str
    status: Literal["initialized", "active", "closed", "settled", "determined"]
    yes_sub_title: str | None = None
    no_sub_title: str | None = None

    yes_bid: int | None = Field(default=None, ge=0, le=100)
    yes_ask: int | None = Field(default=None, ge=0, le=100)
    no_bid: int | None = Field(default=None, ge=0, le=100)
    no_ask: int | None = Field(default=None, ge=0, le=100)
    last_price: int | None = Field(default=None, ge=0, le=100)
    volume: int | None = None
    open_interest: int | None = None

    close_time: datetime | None = None
    expiration_time: datetime | None = None
    expected_expiration_time: datetime | None = None

    settlement_value: int | None = Field(default=None, ge=0, le=100)
    result: Literal["yes", "no", ""] | None = None


class MarketsResponse(WireModelLoose):
    """Paginated `GET /markets` response."""

    markets: list[Market] = Field(default_factory=list)
    cursor: str | None = None


class OrderbookLevel(WireModelLoose):
    """One side's depth at one price. Tuple-style in the wire format."""

    price: int = Field(ge=1, le=99)
    quantity: int = Field(ge=0)


class Orderbook(WireModelLoose):
    """`GET /markets/{ticker}/orderbook` — bid/ask depth in cents."""

    yes: list[OrderbookLevel] = Field(default_factory=list)
    no: list[OrderbookLevel] = Field(default_factory=list)


class OrderbookResponse(WireModelLoose):
    orderbook: Orderbook


# === Portfolio: positions, fills, orders ===

class PortfolioPosition(WireModelLoose):
    """`GET /portfolio/positions` row."""

    ticker: str
    position: int = Field(description="Signed: positive = YES exposure, negative = NO.")
    market_exposure: int = Field(description="Exposure in cents.")
    realized_pnl: int = Field(default=0, description="Realized PnL in cents.")
    fees_paid: int = Field(default=0, description="Fees in cents.")
    total_traded: int = Field(default=0)
    resting_orders_count: int = Field(default=0)
    last_updated_ts: datetime | None = None


class PositionsResponse(WireModelLoose):
    market_positions: list[PortfolioPosition] = Field(default_factory=list)
    cursor: str | None = None


class Fill(WireModelLoose):
    """`GET /portfolio/fills` row — one execution at one price."""

    trade_id: str
    order_id: str
    ticker: str
    side: Literal["yes", "no"]
    action: Literal["buy", "sell"]
    count: int = Field(ge=1)
    yes_price: int = Field(ge=1, le=99)
    no_price: int = Field(ge=1, le=99)
    is_taker: bool
    created_time: datetime


class FillsResponse(WireModelLoose):
    fills: list[Fill] = Field(default_factory=list)
    cursor: str | None = None


# === Orders: place / response ===

class PlaceOrderRequest(WireModel):
    """`POST /portfolio/orders` body. All prices in integer cents.

    `client_order_id` is the idempotency key — supply a UUID and Kalshi will
    reject duplicate submissions with the same key. CLAUDE.md hard rule 6.
    """

    ticker: str
    side: Literal["yes", "no"]
    action: Literal["buy", "sell"]
    type: Literal["limit", "market"] = "limit"
    count: int = Field(ge=1)
    yes_price: int | None = Field(default=None, ge=1, le=99)
    no_price: int | None = Field(default=None, ge=1, le=99)
    client_order_id: str
    post_only: bool = False
    expiration_ts: int | None = None  # epoch seconds; None = good-til-cancel


class Order(WireModelLoose):
    """A Kalshi order as returned in responses."""

    order_id: str
    client_order_id: str
    ticker: str
    side: Literal["yes", "no"]
    action: Literal["buy", "sell"]
    type: Literal["limit", "market"]
    status: Literal["resting", "canceled", "executed", "pending"]
    yes_price: int | None = Field(default=None, ge=1, le=99)
    no_price: int | None = Field(default=None, ge=1, le=99)
    count: int = Field(ge=0)
    remaining_count: int = Field(default=0, ge=0)
    created_time: datetime | None = None


class PlaceOrderResponse(WireModelLoose):
    order: Order


class CancelOrderResponse(WireModelLoose):
    order: Order
    reduced_by: int | None = None


# === Events (Kalshi groups markets into events) ===

class Event(WireModelLoose):
    event_ticker: str
    series_ticker: str | None = None
    title: str
    sub_title: str | None = None
    category: str | None = None
    mutually_exclusive: bool = False
    markets: list[Market] = Field(default_factory=list)


class EventsResponse(WireModelLoose):
    events: list[Event] = Field(default_factory=list)
    cursor: str | None = None
