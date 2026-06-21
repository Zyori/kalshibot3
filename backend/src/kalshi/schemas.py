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

from src.core.types import (
    cents_to_dollars_str as _cents_to_dollars_str,
    dollars_str_to_cents as _dollar_str_to_cents,
)


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
    status: str
    """Kalshi market status. Known values: initialized, active, closed,
    settled, determined, finalized, inactive. Deliberately `str`, not a
    Literal: Kalshi adds status values over time and this payload validates a
    whole series at once, so one market with an unmodeled status would fail
    validation for the *entire* series and drop every event from the feed.
    Observed the hard way — `finalized` (2026-05-26, settled domestic leagues)
    and `inactive` (2026-06-01, not-yet-open KXINTLFRIENDLYGAME markets) each
    surfaced this way. market_discovery._classify treats anything that isn't
    `active` or a terminal status as non-tradeable (dropped from the feed), so
    an unknown value degrades safely rather than going live."""
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
    occurrence_datetime: datetime | None = None
    """The real event start time (kickoff for sports markets). Verified
    present on KXWCGAME, KXLIGUE1GAME, KXINTLFRIENDLYGAME, KXMLSGAME — the
    /events?with_nested_markets payload carries this on every market.
    Use this as the kickoff source; the ticker date is a noon-UTC midday
    proxy and is unreliable for evening kickoffs."""

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
    """`GET /portfolio/fills` row — one execution at one price.

    Kalshi's wire format ships dollar strings (`yes_price_dollars`,
    `fee_cost`); we normalize to int cents in the validator. `fee_cost` is
    the authoritative per-fill fee — the only source of truth for fees in
    this codebase. Never estimate from a formula.
    """

    trade_id: str
    order_id: str
    ticker: str
    side: Literal["yes", "no"]
    action: Literal["buy", "sell"]
    count_centi: int = Field(ge=1)
    """Hundredths of a contract. Kalshi's `count_fp` is a fractional-contract
    string (e.g. "0.97", "67.06") because one logical fill can be split
    across fee tiers. Centi keeps Kalshi's granularity exactly."""
    yes_price: int = Field(ge=1, le=99)
    no_price: int = Field(ge=1, le=99)
    is_taker: bool
    fee_cents: int = Field(default=0, ge=0)
    created_time: datetime

    @property
    def count(self) -> int:
        """Whole contracts (rounded). For display only — bet bookkeeping
        uses count_centi to preserve Kalshi's fractional reporting."""
        return self.count_centi // 100

    @model_validator(mode="before")
    @classmethod
    def _coerce_wire_format(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        out = dict(data)

        if "yes_price" not in out and "yes_price_dollars" in out:
            out["yes_price"] = _dollar_str_to_cents(out["yes_price_dollars"])
        if "no_price" not in out and "no_price_dollars" in out:
            out["no_price"] = _dollar_str_to_cents(out["no_price_dollars"])
        if "count_centi" not in out:
            if "count_fp" in out:
                try:
                    out["count_centi"] = int(round(float(out["count_fp"]) * 100))
                except (TypeError, ValueError):
                    out["count_centi"] = 0
            elif "count" in out:
                out["count_centi"] = int(out["count"]) * 100
        if "fee_cents" not in out:
            for key in ("fee_cost_dollars", "fee_cost", "fees_paid_dollars"):
                if key in out and out[key] is not None:
                    try:
                        out["fee_cents"] = int(round(float(out[key]) * 100))
                    except (TypeError, ValueError):
                        out["fee_cents"] = 0
                    break
        return out


class FillsResponse(WireModelLoose):
    fills: list[Fill] = Field(default_factory=list)
    cursor: str | None = None


# === Settlements ===

class Settlement(WireModelLoose):
    """`GET /portfolio/settlements` row — one resolved position payout.

    Authoritative source of "what did this market pay me?" Used when the
    WS market_lifecycle event was missed (subscription dropped before
    settlement, reconnect gap, etc.).

    Wire format (verified against Kalshi docs):
      ticker                str
      market_result         "yes" | "no" | "scalar" | ""   YES-side winner
      yes_count             int                  contracts you held YES-side
      no_count              int                  contracts you held NO-side
      revenue               int cents            payout received
      settled_time          ISO timestamp

    settlement_value_cents is derived: 100 if market_result == "yes", 0 if
    "no". Both the markets this app handles — soccer per-game/total markets
    AND combo (MVE) markets — settle binary yes/no (verified across the full
    settlements history: every settled KXMVE combo and every soccer market
    resolved yes/no, zero scalar). So combos settle through the sweeper with
    no special handling.
    """

    ticker: str
    market_result: Literal["yes", "no", "scalar", ""] = ""
    """`scalar` is accepted but rare and not produced by any market this app
    trades. Kalshi emits it for some exotic props (observed once on a Super
    Bowl performance market — not soccer, not a combo). The field MUST accept
    it anyway: one scalar row used to fail validation for the WHOLE settlements
    page, killing the sweep for every ticker in that batch. settlement_value_cents
    maps scalar to None and the sweeper skips it; that's correct, because a
    scalar row is never one of ours (isolation drops non-tradeable tickers
    upstream). If a tradeable market ever settles scalar, the bet would hang
    OPEN — surfaced by the position-to-zero divergence log in position_sync, not
    silently lost."""
    yes_count: int = Field(default=0, ge=0)
    no_count: int = Field(default=0, ge=0)
    revenue: int = Field(default=0)
    settled_time: datetime | None = None

    @property
    def settlement_value_cents(self) -> int | None:
        """YES-side payoff in cents. None if Kalshi hasn't determined yet."""
        if self.market_result == "yes":
            return 100
        if self.market_result == "no":
            return 0
        return None


class SettlementsResponse(WireModelLoose):
    settlements: list[Settlement] = Field(default_factory=list)
    cursor: str | None = None


# === Orders: place / response ===
#
# V2 wire (POST /portfolio/events/orders) speaks a single YES-book: side is
# bid/ask (bid = buy YES) and price is ALWAYS the YES-leg price as a fixed-point
# dollar string. Our internal model stays the human yes/no + buy/sell + integer
# cents (in the held side's frame). _to_v2_book() is the single translation
# between the two — the money-critical mapping, exhaustively unit-tested.

# V2 self-trade prevention: cancel the INCOMING order if it would cross our own
# resting order. This app shares the live Kalshi account, so the user may have a
# resting exit/entry on the same market — taker_at_cross never silently kills it
# (vs `maker`, which would cancel the resting order). Project decision 2026-06-21.
_V2_SELF_TRADE_PREVENTION: Literal["taker_at_cross"] = "taker_at_cross"


def _to_v2_book(
    side: Literal["yes", "no"],
    action: Literal["buy", "sell"],
    price_cents: int,
) -> tuple[Literal["bid", "ask"], str]:
    """Translate our (held side, action, held-frame cents) into the V2 single
    YES-book (side, YES-price dollar string).

      buy  YES → bid, yes price          sell YES → ask, yes price
      buy  NO  → ask, 100−price (YES)    sell NO  → bid, 100−price (YES)

    The book side is `bid` when we end up buying YES, `ask` when selling YES;
    buying NO is selling YES and vice-versa. The price is always the YES-leg
    price, so a NO order's held-frame cents become their complement. Inverting
    this places the OPPOSITE side of the intended trade — the single most
    dangerous bug in the order path, so it lives in one tested function."""
    if side == "yes":
        book_side: Literal["bid", "ask"] = "bid" if action == "buy" else "ask"
        yes_cents = price_cents
    else:
        book_side = "ask" if action == "buy" else "bid"
        yes_cents = 100 - price_cents
    return book_side, _cents_to_dollars_str(yes_cents)


def _count_fp(count: int) -> str:
    """Integer contract count → V2 fixed-point count string ('10.00'). Counts are
    whole contracts here, so the two trailing zeros are always exact."""
    return f"{count}.00"


def _held_price_cents(
    side: Literal["yes", "no"], yes_price: int | None, no_price: int | None
) -> int:
    """The order price in the held side's frame — the field matching `side`.
    Exactly one of yes_price/no_price is set by the caller for that side. Shared
    by both order request models so the held-frame selection lives in one place."""
    price = yes_price if side == "yes" else no_price
    if price is None:
        raise ValueError(f"{side} order missing {side}_price")
    return price


class PlaceOrderRequest(WireModel):
    """A new order in our internal frame: human yes/no side + buy/sell action +
    integer-cent price (in the held side's frame). `.to_v2_wire()` renders the
    V2 `POST /portfolio/events/orders` body.

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

    def held_price_cents(self) -> int:
        """The order price in the held side's frame. Public so rest.py can seed
        the synthesized Order's entry price without re-deriving the mapping."""
        return _held_price_cents(self.side, self.yes_price, self.no_price)

    def to_v2_wire(self) -> dict[str, Any]:
        """The V2 `POST /portfolio/events/orders` request body.

        time_in_force is good_till_canceled — we only place resting limits today
        (V1's implicit GTC). A marketable/IOC path would set this differently;
        out of scope until we add it."""
        book_side, price = _to_v2_book(self.side, self.action, self.held_price_cents())
        body: dict[str, Any] = {
            "ticker": self.ticker,
            "side": book_side,
            "count": _count_fp(self.count),
            "price": price,
            "time_in_force": "good_till_canceled",
            "self_trade_prevention_type": _V2_SELF_TRADE_PREVENTION,
            "client_order_id": self.client_order_id,
            "post_only": self.post_only,
        }
        if self.expiration_ts is not None:
            body["expiration_time"] = self.expiration_ts
        return body


class Order(WireModelLoose):
    """A Kalshi order as returned in responses.

    Wire-format quirks (verified 2026-05-27 against live Kalshi):
      - Prices come as dollar strings (`yes_price_dollars: "0.0200"`) on
        responses, not as int cents the way `Market` returns them. We
        translate to int cents in the model validator below.
      - Counts come as float strings (`initial_count_fp: "1.00"`,
        `remaining_count_fp: "1.00"`). Translated to int.
      - The legacy int-cent fields (`yes_price`, `count`, `remaining_count`)
        are still present on some endpoints, so we accept both shapes.
    """

    order_id: str
    client_order_id: str | None = ""
    """Some Kalshi responses (e.g. /portfolio/orders without our placement)
    omit client_order_id entirely; defaulting keeps the parser happy."""
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

    @model_validator(mode="before")
    @classmethod
    def _normalize_kalshi_wire(cls, obj: Any) -> Any:
        # Runs on every validation path, including nested (PlaceOrderResponse,
        # CancelOrderResponse). Overriding model_validate doesn't, because
        # the parent's validator drives nested children through its own path.
        if not isinstance(obj, dict):
            return obj
        out = dict(obj)
        # Prices: dollar strings → int cents.
        if "yes_price" not in out and "yes_price_dollars" in out:
            out["yes_price"] = _dollar_str_to_cents(out["yes_price_dollars"])
        if "no_price" not in out and "no_price_dollars" in out:
            out["no_price"] = _dollar_str_to_cents(out["no_price_dollars"])
        # Counts: float strings → int.
        if "count" not in out and "initial_count_fp" in out:
            out["count"] = int(float(out["initial_count_fp"]))
        if "remaining_count" not in out and "remaining_count_fp" in out:
            out["remaining_count"] = int(float(out["remaining_count_fp"]))
        out.setdefault("type", "limit")
        out.setdefault("action", "buy")
        return out


class V2OrderAck(WireModelLoose):
    """The V2 create/amend response (`POST /portfolio/events/orders[...]`).

    Deliberately thin: V2 echoes only the order_id, the post-placement fill /
    remaining counts (fixed-point strings), and the client_order_id — NOT the
    side, price, ticker, or status. Those we already know from the request we
    sent, so we synthesize the full Order from request + this ack rather than
    parse a shape that no longer carries them (see synthesize_order_from_ack)."""

    order_id: str
    client_order_id: str | None = ""
    fill_count: str | None = None
    remaining_count: str | None = None
    average_fill_price: str | None = None
    average_fee_paid: str | None = None
    ts_ms: int | None = None

    def fill_count_int(self) -> int:
        return int(float(self.fill_count)) if self.fill_count is not None else 0

    def remaining_count_int(self, total: int) -> int:
        """Resting contracts after placement. V2 may omit remaining_count when
        the order rests untouched (no immediate fill); fall back to total−filled
        so the synthesized Order's count math stays exact."""
        if self.remaining_count is not None:
            return int(float(self.remaining_count))
        return max(0, total - self.fill_count_int())


def synthesize_order_from_ack(
    *,
    ack: V2OrderAck,
    ticker: str,
    side: Literal["yes", "no"],
    action: Literal["buy", "sell"],
    held_price_cents: int,
    count: int,
    client_order_id: str,
) -> Order:
    """Build our canonical Order from a V2 ack + the request fields we sent.

    The V2 response dropped side/price/ticker/status, but every downstream
    consumer (record_placed_order, the amend route) still reads them off Order.
    We hold all of them from the request, so reconstruct rather than re-shape
    the consumers. status is derived from the counts:
      * filled, nothing left            → executed
      * nothing filled, nothing left    → canceled (self_trade_prevention killed
                                          the order before any fill — taker_at_cross
                                          cancels an order that would cross our own
                                          resting one; the ack still 201s with both
                                          counts zero). record_placed_order must NOT
                                          create an OPEN bet for this — there's no
                                          position.
      * anything still resting          → resting"""
    filled = ack.fill_count_int()
    remaining = ack.remaining_count_int(count)
    status: Literal["resting", "canceled", "executed", "pending"]
    if remaining == 0:
        status = "executed" if filled > 0 else "canceled"
    else:
        status = "resting"
    return Order(
        order_id=ack.order_id,
        client_order_id=client_order_id,
        ticker=ticker,
        side=side,
        action=action,
        type="limit",
        status=status,
        yes_price=held_price_cents if side == "yes" else None,
        no_price=held_price_cents if side == "no" else None,
        count=count,
        remaining_count=remaining,
    )


class PlaceOrderResponse(WireModelLoose):
    order: Order


class AmendOrderRequest(WireModel):
    """Amend a resting order's price and/or count, in our internal frame.
    `.to_v2_wire()` renders the V2 `POST /portfolio/events/orders/{id}/amend`
    body. Kalshi retires the old order and issues a NEW order_id (which the
    caller must re-track), re-queued at the new price level. Side and action are
    unchanged — those would be a different order, not an amend.

    `updated_client_order_id` is the idempotency key for the amend, same role as
    client_order_id on a place (CLAUDE.md rule 6). All prices integer cents.
    """

    ticker: str
    side: Literal["yes", "no"]
    action: Literal["buy", "sell"]
    yes_price: int | None = Field(default=None, ge=1, le=99)
    no_price: int | None = Field(default=None, ge=1, le=99)
    count: int = Field(ge=1)
    updated_client_order_id: str

    def held_price_cents(self) -> int:
        """The amend price in the held side's frame. Public so rest.py can seed
        the synthesized Order without re-deriving the mapping."""
        return _held_price_cents(self.side, self.yes_price, self.no_price)

    def to_v2_wire(self) -> dict[str, Any]:
        """The V2 amend body. Same single-YES-book mapping as a place; the V2
        amend takes the absolute new side/price/count (not a delta)."""
        book_side, price = _to_v2_book(self.side, self.action, self.held_price_cents())
        return {
            "ticker": self.ticker,
            "side": book_side,
            "price": price,
            "count": _count_fp(self.count),
            "updated_client_order_id": self.updated_client_order_id,
        }


class AmendOrderResponse(WireModelLoose):
    """`order` is the synthesized NEW order the caller must now track (the V2
    amend issues a fresh order_id; we rebuild the Order from the request + ack).
    V2 doesn't echo the retired order, and nothing downstream needs it — the
    amend route already holds the old order_id in its own scope."""
    order: Order


class CancelOrderResponse(WireModelLoose):
    """V2 cancel ack (`DELETE /portfolio/events/orders/{id}`). Carries only the
    order_id, how many contracts the cancel removed (`reduced_by`, a fixed-point
    string), and a timestamp — NOT the order's ticker/side/status. The cancel
    route already holds the ticker (it looked the order up first) and the status
    is definitionally canceled, so we don't reconstruct a full Order here."""

    order_id: str
    client_order_id: str | None = ""
    reduced_by: int | None = None
    ts_ms: int | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_kalshi_wire(cls, obj: Any) -> Any:
        if not isinstance(obj, dict):
            return obj
        out = dict(obj)
        # reduced_by arrives as a fixed-point string ("10.00"); legacy int also
        # accepted. Either way land an int contract count.
        raw = out.get("reduced_by")
        if isinstance(raw, str):
            out["reduced_by"] = int(float(raw))
        elif "reduced_by" not in out and "reduced_by_fp" in out:
            out["reduced_by"] = int(float(out["reduced_by_fp"]))
        return out


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


# === Multivariate event collections (combos / parlays) ===

class SelectedMarket(WireModel):
    """One leg of a combo: a single market + the side you're backing.
    Kalshi calls this a TickerPair."""
    market_ticker: str
    event_ticker: str
    side: Literal["yes", "no"]


class CreateMultivariateMarketRequest(WireModel):
    """Body for POST /multivariate_event_collections/{collection_ticker} —
    materializes the combo market for a set of leg selections."""
    selected_markets: list[SelectedMarket]
    with_market_payload: bool = True


class CreateMultivariateMarketResponse(WireModelLoose):
    """The materialized combo. `market_ticker` is a normal ticker you then
    trade through the standard CreateOrder endpoint. Idempotent: the same
    selected_markets return the same market_ticker without consuming another
    of the weekly creation allowance."""
    event_ticker: str
    market_ticker: str
    market: Market | None = None


# === RFQ (Request For Quote) — how combos actually fill ===
#
# A standing limit order on a combo's (empty) book never fills. Combos fill via
# RFQ: request a quote on the combo, market makers respond with quotes (pushed
# over the WS `communications` channel and readable via GET /communications/
# quotes), then the requester accepts the best quote (accept -> maker confirms
# -> a short execution timer -> fill on the normal WS fill channel).


class CreateRfqRequest(WireModel):
    """Body for POST /communications/rfqs. For a combo, carry the materialized
    market_ticker plus the collection + legs so makers can price it. Size is
    either `contracts` or a `target_cost_dollars` budget — exactly one."""
    market_ticker: str
    mve_collection_ticker: str
    mve_selected_legs: list[SelectedMarket]
    target_cost_dollars: str | None = None
    contracts: int | None = None

    @model_validator(mode="after")
    def _exactly_one_size(self) -> "CreateRfqRequest":
        if (self.contracts is None) == (self.target_cost_dollars is None):
            raise ValueError("provide exactly one of contracts or target_cost_dollars")
        return self


class CreateRfqResponse(WireModelLoose):
    """The created RFQ. `id` is the rfq_id quotes will reference."""
    id: str


class Quote(WireModelLoose):
    """A market maker's quote on one of our RFQs. One side's bid is the price
    (the other is 0). yes_bid is the maker's price per YES contract; accepting
    the NO side means you take YES at 100 - no_bid. Dollar strings -> int cents
    at the boundary, like every other wire model.

    Fields verified against live quotes on the account."""
    id: str
    rfq_id: str
    market_ticker: str
    status: str
    creator_id: str = ""
    yes_bid_cents: int = 0
    no_bid_cents: int = 0
    yes_contracts: int = 0
    no_contracts: int = 0
    created_ts: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_wire(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        out = dict(data)

        def _bid(key: str) -> int:
            # A maker may quote only one side; the other dollar field can be
            # absent OR null. Guard like Fill._coerce_wire — never crash the
            # whole quotes page on a one-sided quote (the normal illiquid case).
            v = out.get(key)
            if v is None:
                return 0
            try:
                return _dollar_str_to_cents(v)
            except (TypeError, ValueError):
                return 0

        def _ct(key: str) -> int:
            v = out.get(key)
            if v is None:
                return 0
            try:
                return int(float(v))
            except (TypeError, ValueError):
                return 0

        if "yes_bid_cents" not in out:
            out["yes_bid_cents"] = _bid("yes_bid_dollars")
        if "no_bid_cents" not in out:
            out["no_bid_cents"] = _bid("no_bid_dollars")
        # *_contracts_fp are fractional-contract strings ("71.00"); floor to whole.
        if "yes_contracts" not in out:
            out["yes_contracts"] = _ct("yes_contracts_fp")
        if "no_contracts" not in out:
            out["no_contracts"] = _ct("no_contracts_fp")
        return out


class QuotesResponse(WireModelLoose):
    quotes: list[Quote] = Field(default_factory=list)
    cursor: str | None = None
