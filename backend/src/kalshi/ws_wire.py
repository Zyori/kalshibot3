"""Pydantic models for Kalshi WebSocket wire format.

Kalshi's WS uses **dollar strings** (e.g. "0.42"), not the integer cents the
REST API uses. The conversion to cents happens HERE and only here — same
rule as `schemas.py`: dollar↔cents lives at the wire boundary, rest of the
codebase trusts cents.

V1 (Kalshi-Bot/src/api/schemas.ts) confirmed the channels we care about:
  - orderbook_snapshot   full book at subscribe time (per market)
  - orderbook_delta      one-price-level change (per market, after snapshot)
  - fill                 a personal fill executed (account-wide)
  - user_order           a personal order state change (account-wide)
  - market_lifecycle     market opened / closed / settled
  - subscribed           ACK for our subscribe command (informational)

Anything else from Kalshi is ignored.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator


# === Helpers ===

def _dollars_str_to_cents(s: str) -> int:
    """Kalshi WS sends "0.42" — convert to 42 (cents). Banker's rounding."""
    return int(round(float(s) * 100))


class WireBase(BaseModel):
    """Forward-compatible: unknown fields ignored so a Kalshi addition doesn't break us."""
    model_config = ConfigDict(extra="ignore")


# === Orderbook ===

class BookLevel(WireBase):
    """One (price, quantity) level. Kalshi WS sends these as a 2-tuple
    [price_dollars_string, count_int] which we normalize to integer cents."""
    price_cents: int = Field(ge=1, le=99)
    quantity: int = Field(ge=0)


class OrderbookSnapshotPayload(WireBase):
    market_ticker: str
    market_id: str
    yes: list[BookLevel] = Field(default_factory=list)
    no: list[BookLevel] = Field(default_factory=list)


class OrderbookSnapshot(WireBase):
    type: Literal["orderbook_snapshot"]
    sid: int
    seq: int
    msg: OrderbookSnapshotPayload


class OrderbookDeltaPayload(WireBase):
    market_ticker: str
    market_id: str
    price_cents: int = Field(ge=1, le=99)
    delta: int = Field(description="Signed: positive adds liquidity, negative removes.")
    side: Literal["yes", "no"]
    ts: datetime | None = None


class OrderbookDelta(WireBase):
    type: Literal["orderbook_delta"]
    sid: int
    seq: int
    msg: OrderbookDeltaPayload


# === Fills (personal) ===

class FillPayload(WireBase):
    trade_id: str
    order_id: str
    ticker: str
    side: Literal["yes", "no"]
    action: Literal["buy", "sell"]
    count: int = Field(ge=1)
    yes_price_cents: int = Field(ge=1, le=99)
    no_price_cents: int = Field(ge=1, le=99)
    is_taker: bool | None = None
    ts: datetime | None = None


class Fill(WireBase):
    type: Literal["fill"]
    sid: int
    msg: FillPayload


# === User orders (personal) ===

class UserOrderPayload(WireBase):
    order_id: str
    client_order_id: str | None = None
    ticker: str
    side: Literal["yes", "no"]
    status: Literal["resting", "canceled", "executed", "pending"]
    yes_price_cents: int | None = Field(default=None, ge=1, le=99)
    remaining_count: int = Field(ge=0)


class UserOrder(WireBase):
    type: Literal["user_order"]
    sid: int
    msg: UserOrderPayload


# === Market lifecycle ===

class MarketLifecyclePayload(WireBase):
    market_ticker: str
    status: str  # "open", "closed", "settled", "determined", etc.
    settlement_value: int | None = Field(default=None, ge=0, le=100)


class MarketLifecycle(WireBase):
    type: Literal["market_lifecycle"]
    sid: int
    msg: MarketLifecyclePayload


# === Subscribed ACK ===

class SubscribedPayload(WireBase):
    sid: int
    channel: str


class Subscribed(WireBase):
    type: Literal["subscribed"]
    id: int | None = None
    msg: SubscribedPayload


# === Unsubscribed ACK ===
# Wire shape (verified against live Kalshi 2026-05-26):
#   {"type": "unsubscribed", "id": <our request id>, "sid": <sid>, "seq": N}
# Sid is at the top level here, not nested under msg.


class Unsubscribed(WireBase):
    type: Literal["unsubscribed"]
    id: int | None = None
    sid: int


# === update_subscription ACK ===
# Wire shape (verified against live Kalshi 2026-05-26):
#   {"type": "ok", "id": <our request id>, "sid": <sid>, "seq": N,
#    "msg": {"market_tickers": [<full ticker set after the mutation>]}}
# Issued in response to update_subscription add_markets / delete_markets.


class OkPayload(WireBase):
    market_tickers: list[str] = []


class Ok(WireBase):
    type: Literal["ok"]
    id: int | None = None
    sid: int | None = None
    msg: OkPayload = OkPayload()


# === Discriminated union ===

KalshiWsMessage = Annotated[
    Union[OrderbookSnapshot, OrderbookDelta, Fill, UserOrder, MarketLifecycle, Subscribed, Unsubscribed, Ok],
    Field(discriminator="type"),
]

KNOWN_TYPES: frozenset[str] = frozenset(
    {"orderbook_snapshot", "orderbook_delta", "fill", "user_order", "market_lifecycle", "subscribed", "unsubscribed", "ok"}
)


# === Raw → typed parsing with dollar→cents conversion ===

def parse_book_level(raw: list) -> BookLevel:
    """Kalshi sends [price, quantity] for each book level.

    Observed wire-format quirks in production (2026-05-26):
      - price arrives as a dollar string ("0.42") on most channels
      - quantity arrives as a stringified float with trailing zeros
        ("319811.00") on orderbook_snapshot, but as a plain int elsewhere
    Both branches normalize to int.
    """
    price_raw, qty_raw = raw[0], raw[1]
    if isinstance(price_raw, str):
        price_cents = _dollars_str_to_cents(price_raw)
    else:
        price_cents = int(price_raw)
    # int(float(...)) handles both "319811.00" strings and bare ints/floats.
    qty = int(float(qty_raw))
    return BookLevel(price_cents=price_cents, quantity=qty)


def parse_kalshi_ws_message(raw: dict) -> KalshiWsMessage | None:
    """Parse one raw WS payload. Returns None for unknown types.

    Conversion responsibilities:
      - orderbook_snapshot: yes/no arrays of [price_dollar_str, qty] → list[BookLevel]
      - orderbook_delta:    price_dollars + delta_fp (string) → price_cents + delta (int)
      - fill / user_order:  yes_price_dollars / yes_price → cents
    """
    msg_type = raw.get("type")
    if msg_type not in KNOWN_TYPES:
        return None

    if msg_type == "orderbook_snapshot":
        msg = raw.get("msg", {})
        return OrderbookSnapshot(
            type="orderbook_snapshot",
            sid=raw["sid"],
            seq=raw["seq"],
            msg=OrderbookSnapshotPayload(
                market_ticker=msg["market_ticker"],
                market_id=str(msg["market_id"]),
                yes=[parse_book_level(b) for b in (msg.get("yes_dollars_fp") or msg.get("yes") or [])],
                no=[parse_book_level(b) for b in (msg.get("no_dollars_fp") or msg.get("no") or [])],
            ),
        )

    if msg_type == "orderbook_delta":
        msg = raw.get("msg", {})
        price_raw = msg.get("price_dollars") or msg.get("price")
        price_cents = _dollars_str_to_cents(price_raw) if isinstance(price_raw, str) else int(price_raw)
        delta_raw = msg.get("delta_fp") or msg.get("delta")
        delta = int(float(delta_raw)) if isinstance(delta_raw, str) else int(delta_raw)
        return OrderbookDelta(
            type="orderbook_delta",
            sid=raw["sid"],
            seq=raw["seq"],
            msg=OrderbookDeltaPayload(
                market_ticker=msg["market_ticker"],
                market_id=str(msg["market_id"]),
                price_cents=price_cents,
                delta=delta,
                side=msg["side"],
                ts=msg.get("ts"),
            ),
        )

    if msg_type == "fill":
        msg = raw.get("msg", {})
        # Kalshi sometimes sends prices as ints (cents) and sometimes as dollar strings.
        yes_raw = msg.get("yes_price_dollars") or msg.get("yes_price")
        no_raw = msg.get("no_price_dollars") or msg.get("no_price")
        yes_cents = _dollars_str_to_cents(yes_raw) if isinstance(yes_raw, str) else int(yes_raw)
        no_cents = _dollars_str_to_cents(no_raw) if isinstance(no_raw, str) else int(no_raw)
        return Fill(
            type="fill",
            sid=raw["sid"],
            msg=FillPayload(
                trade_id=msg["trade_id"],
                order_id=msg["order_id"],
                ticker=msg.get("ticker") or msg.get("market_ticker") or "",
                side=msg["side"],
                action=msg.get("action", "buy"),
                count=int(msg.get("count", 0)),
                yes_price_cents=yes_cents,
                no_price_cents=no_cents,
                is_taker=msg.get("is_taker"),
                ts=msg.get("ts"),
            ),
        )

    if msg_type == "user_order":
        msg = raw.get("msg", {})
        yes_raw = msg.get("yes_price_dollars") or msg.get("yes_price")
        yes_cents = (
            _dollars_str_to_cents(yes_raw) if isinstance(yes_raw, str)
            else (int(yes_raw) if yes_raw is not None else None)
        )
        remaining_raw = msg.get("remaining_count_fp") or msg.get("remaining_count")
        remaining = int(float(remaining_raw)) if isinstance(remaining_raw, str) else int(remaining_raw)
        return UserOrder(
            type="user_order",
            sid=raw["sid"],
            msg=UserOrderPayload(
                order_id=msg["order_id"],
                client_order_id=msg.get("client_order_id"),
                ticker=msg["ticker"],
                side=msg["side"],
                status=msg["status"],
                yes_price_cents=yes_cents,
                remaining_count=remaining,
            ),
        )

    if msg_type == "market_lifecycle":
        msg = raw.get("msg", {})
        return MarketLifecycle(
            type="market_lifecycle",
            sid=raw["sid"],
            msg=MarketLifecyclePayload(
                market_ticker=msg["market_ticker"],
                status=msg["status"],
                settlement_value=msg.get("settlement_value"),
            ),
        )

    if msg_type == "subscribed":
        msg = raw.get("msg", {})
        return Subscribed(
            type="subscribed",
            id=raw.get("id"),
            msg=SubscribedPayload(
                sid=msg.get("sid", 0),
                channel=msg.get("channel", ""),
            ),
        )

    if msg_type == "unsubscribed":
        return Unsubscribed(
            type="unsubscribed",
            id=raw.get("id"),
            sid=raw["sid"],
        )

    if msg_type == "ok":
        msg = raw.get("msg") or {}
        return Ok(
            type="ok",
            id=raw.get("id"),
            sid=raw.get("sid"),
            msg=OkPayload(market_tickers=list(msg.get("market_tickers") or [])),
        )

    return None
