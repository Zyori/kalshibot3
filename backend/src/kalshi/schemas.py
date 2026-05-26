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
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    status: Literal["initialized", "active", "closed", "settled", "determined", "finalized"]
    """`finalized` appears on real production markets — observed 2026-05-26
    on KXMLSGAME, KXCANPLGAME, and other settled domestic-league games."""
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
    result: Literal["yes", "no", "", "scalar"] | None = None
    """`scalar` appears on all markets in finalized 3-way moneyline events
    (observed 2026-05-26 on KXMLSGAME, KXSERIEBGAME, KXEFLCHAMPIONSHIPGAME,
    KXDIMAYORGAME, KXBOLPDIVGAME). It does not encode a winner — Kalshi
    just doesn't populate this field for these events. Settled-position
    outcomes come from the settlements endpoint, not from this field."""


class MarketsResponse(WireModelLoose):
    """Paginated `GET /markets` response."""

    markets: list[Market] = Field(default_factory=list)
    cursor: str | None = None


class OrderbookLevel(WireModelLoose):
    """One side's depth at one price. Cents + integer quantity, app-internal."""

    price_cents: int = Field(ge=1, le=99)
    quantity: int = Field(ge=0)


class Orderbook(WireModelLoose):
    """Parsed orderbook: bids on each side, integer cents + integer quantity.

    Kalshi's REST orderbook wire format is `orderbook_fp` containing
    `yes_dollars` and `no_dollars`, each a list of `[price_str, qty_str]`
    where prices are dollar-decimal strings ("0.0100" = 1¢, "0.6600" = 66¢)
    and quantities are dollar-amount strings of total notional at that level.

    We translate that into `OrderbookLevel(price_cents, quantity)` rows in
    the model validator below so the rest of the codebase only sees cents
    and integer counts — matching the LiveState shape that WS deltas feed.
    """

    yes: list[OrderbookLevel] = Field(default_factory=list)
    no: list[OrderbookLevel] = Field(default_factory=list)


def _dollar_str_to_cents(s: str) -> int:
    """'0.6600' → 66. 'orderbook_fp' uses 4-decimal dollar strings."""
    return int(round(float(s) * 100))


def _level_count_from_notional(price_cents: int, notional_dollars_str: str) -> int:
    """Kalshi reports level depth as the dollar notional sitting at that price
    (e.g. "5487.50" at "0.0200" = $5,487.50 worth of contracts at 2¢).
    Contracts are $1-notional, so the count is notional / price-in-dollars.
    Round to nearest contract — fractional contracts don't exist on Kalshi."""
    return int(round(float(notional_dollars_str) / (price_cents / 100.0)))


class OrderbookResponse(WireModelLoose):
    """`GET /markets/{ticker}/orderbook` envelope. The wire payload nests under
    `orderbook_fp`; we flatten to a typed `Orderbook` for the rest of the app."""

    orderbook: Orderbook

    @classmethod
    def model_validate(cls, obj: Any, **kwargs: Any) -> "OrderbookResponse":
        if isinstance(obj, dict) and "orderbook_fp" in obj and "orderbook" not in obj:
            fp = obj["orderbook_fp"] or {}
            yes_levels = []
            for price_str, qty_str in (fp.get("yes_dollars") or []):
                pc = _dollar_str_to_cents(price_str)
                yes_levels.append({"price_cents": pc, "quantity": _level_count_from_notional(pc, qty_str)})
            no_levels = []
            for price_str, qty_str in (fp.get("no_dollars") or []):
                pc = _dollar_str_to_cents(price_str)
                no_levels.append({"price_cents": pc, "quantity": _level_count_from_notional(pc, qty_str)})
            obj = {"orderbook": {"yes": yes_levels, "no": no_levels}}
        return super().model_validate(obj, **kwargs)


# === Portfolio: positions, fills, orders ===

class PortfolioPosition(WireModelLoose):
    """`GET /portfolio/positions` row.

    Real production wire format (verified 2026-05-26):
      position_fp              "783.90"       float-string, signed
      market_exposure_dollars  "39.195000"    dollar-string
      realized_pnl_dollars     "4.552000"
      fees_paid_dollars        "3.807000"
      total_traded_dollars     "1250.743000"

    The docs show int-cents fields; production uses dollar-strings with _fp /
    _dollars suffixes. Validators normalize both shapes into integer cents
    so the rest of the codebase keeps its cents invariant.
    """

    ticker: str
    position: int = Field(
        default=0,
        description="Signed: positive = YES exposure, negative = NO. Truncated to int.",
    )
    market_exposure: int = Field(default=0, description="Exposure in cents.")
    realized_pnl: int = Field(default=0, description="Realized PnL in cents.")
    fees_paid: int = Field(default=0, description="Fees in cents.")
    total_traded: int = Field(default=0)
    resting_orders_count: int = Field(default=0)
    last_updated_ts: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_wire_format(cls, data: object) -> object:
        """Accept Kalshi's dollar-string + _fp suffix wire format.

        Looks for `position_fp`, `market_exposure_dollars`, etc. and rewrites
        them into the int-cents fields the rest of this class expects. Passes
        through unchanged if the caller already gave us the canonical shape.
        """
        if not isinstance(data, dict):
            return data
        out = dict(data)  # don't mutate the caller's dict

        if "position" not in out and "position_fp" in out:
            raw = out["position_fp"]
            try:
                out["position"] = int(float(raw))
            except (TypeError, ValueError):
                out["position"] = 0

        for cents_key, dollar_key in [
            ("market_exposure", "market_exposure_dollars"),
            ("realized_pnl", "realized_pnl_dollars"),
            ("fees_paid", "fees_paid_dollars"),
            ("total_traded", "total_traded_dollars"),
        ]:
            if cents_key not in out and dollar_key in out:
                raw = out[dollar_key]
                try:
                    out[cents_key] = int(round(float(raw) * 100))
                except (TypeError, ValueError):
                    out[cents_key] = 0

        return out


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
