"""In-memory mirror of Kalshi state, fed by the WS consumer.

Hot data lives here, not in the DB. Why:
  - WS pushes orderbook updates many times per second per market. Persisting
    every tick is wasted I/O and SQLite write contention.
  - Reads (orderbook for the order panel, position for the dashboard) want
    sub-millisecond latency; SQLite is fine but in-memory is free.
  - The DB still records bets, fills (BET rows), and POSITION reconciles
    every 60s — that's the durable layer. LiveState is the volatile mirror.

Single instance lives on app.state.live_state. Created in main.lifespan.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from src.kalshi.ws_wire import (
    BookLevel,
    Fill,
    MarketLifecycle,
    OrderbookDelta,
    OrderbookSnapshot,
    UserOrder,
)


@dataclass
class BookSide:
    """One side (yes or no) of a market's orderbook, keyed by price_cents."""
    levels: dict[int, int] = field(default_factory=dict)
    """price_cents -> aggregate quantity at that level"""

    def apply_snapshot(self, levels: list[BookLevel]) -> None:
        self.levels = {l.price_cents: l.quantity for l in levels if l.quantity > 0}

    def apply_delta(self, price_cents: int, delta: int) -> None:
        new_qty = self.levels.get(price_cents, 0) + delta
        if new_qty <= 0:
            self.levels.pop(price_cents, None)
        else:
            self.levels[price_cents] = new_qty

    def best_price(self, side: str) -> int | None:
        """Best bid (highest) or best ask (lowest) — caller specifies."""
        if not self.levels:
            return None
        return max(self.levels) if side == "bid" else min(self.levels)


@dataclass
class MarketBook:
    """Both sides of one market's book plus update timestamp."""
    ticker: str
    yes: BookSide = field(default_factory=BookSide)
    no: BookSide = field(default_factory=BookSide)
    last_update: float = 0.0
    status: str = "open"
    """Mirrors Kalshi market_lifecycle.status. settled markets reject orders."""

    # Kalshi's `yes` and `no` arrays both hold BIDS — people offering to BUY
    # that side at the listed price. The implied ASK on one side is derived
    # from the BIDS on the other side: if someone bids 17¢ for NO, the same
    # trade viewed from YES is offering to SELL YES at 83¢ (= 100 - 17).
    #
    # So:
    #   yes_best_bid = highest YES bid              (max(yes.levels))
    #   yes_best_ask = 100 - highest NO bid         (because someone bidding
    #                                                 17¢ for NO is selling
    #                                                 YES at 83¢)
    #   no_best_bid  = highest NO bid               (max(no.levels))
    #   no_best_ask  = 100 - highest YES bid        (symmetric)

    @property
    def yes_best_bid(self) -> int | None:
        """Highest price someone is willing to pay for YES."""
        return self.yes.best_price("bid")

    @property
    def yes_best_ask(self) -> int | None:
        """Lowest price someone would sell YES at — derived from NO bids."""
        best_no_bid = self.no.best_price("bid")
        return 100 - best_no_bid if best_no_bid is not None else None

    @property
    def no_best_bid(self) -> int | None:
        """Highest price someone is willing to pay for NO."""
        return self.no.best_price("bid")

    @property
    def no_best_ask(self) -> int | None:
        """Lowest price someone would sell NO at — derived from YES bids."""
        best_yes_bid = self.yes.best_price("bid")
        return 100 - best_yes_bid if best_yes_bid is not None else None


@dataclass
class OpenOrder:
    """One of our resting orders, as last reported by user_order WS events."""
    order_id: str
    client_order_id: str | None
    ticker: str
    side: str  # "yes" / "no"
    status: str  # "resting" / "canceled" / "executed" / "pending"
    yes_price_cents: int | None
    remaining_count: int


class LiveState:
    """The mutable hot-data store. All methods are sync — single asyncio loop.

    Use copy semantics when handing data to the browser-WS broadcaster so
    consumers can't accidentally mutate our internal state.
    """

    def __init__(self) -> None:
        self.books: dict[str, MarketBook] = {}
        """market_ticker -> MarketBook"""
        self.open_orders: dict[str, OpenOrder] = {}
        """order_id -> OpenOrder (resting + pending; terminal states get removed)"""
        self.connected: bool = False
        self.last_ws_message_at: float = 0.0

    def get_or_create_book(self, ticker: str) -> MarketBook:
        if ticker not in self.books:
            self.books[ticker] = MarketBook(ticker=ticker)
        return self.books[ticker]

    # === WS event ingestion ===

    def apply_orderbook_snapshot(self, m: OrderbookSnapshot) -> None:
        book = self.get_or_create_book(m.msg.market_ticker)
        book.yes.apply_snapshot(m.msg.yes)
        book.no.apply_snapshot(m.msg.no)
        book.last_update = time.monotonic()

    def apply_orderbook_delta(self, m: OrderbookDelta) -> None:
        book = self.get_or_create_book(m.msg.market_ticker)
        side = book.yes if m.msg.side == "yes" else book.no
        side.apply_delta(m.msg.price_cents, m.msg.delta)
        book.last_update = time.monotonic()

    def apply_market_lifecycle(self, m: MarketLifecycle) -> None:
        book = self.get_or_create_book(m.msg.market_ticker)
        book.status = m.msg.status

    def apply_user_order(self, m: UserOrder) -> OpenOrder | None:
        """Returns the OpenOrder if it's now resting/pending; None if terminal."""
        if m.msg.status in ("canceled", "executed"):
            return self.open_orders.pop(m.msg.order_id, None)

        order = OpenOrder(
            order_id=m.msg.order_id,
            client_order_id=m.msg.client_order_id,
            ticker=m.msg.ticker,
            side=m.msg.side,
            status=m.msg.status,
            yes_price_cents=m.msg.yes_price_cents,
            remaining_count=m.msg.remaining_count,
        )
        self.open_orders[m.msg.order_id] = order
        return order

    def apply_fill(self, m: Fill) -> None:
        """Fills are durable events — they're handled by bet_service (DB).
        This method exists for symmetry / future hooks; LiveState itself
        doesn't track fill history."""
        # Touch timestamp so dashboards know the WS is alive.
        self.last_ws_message_at = time.monotonic()
