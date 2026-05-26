"""Tests for src/kalshi/live_state.py.

LiveState is the runtime view of every market we're watching. A bug here
would mean the order panel shows wrong prices, the dashboard shows wrong
positions, the sanity guard misfires. High value to cover thoroughly.
"""

from __future__ import annotations

from src.kalshi.live_state import LiveState
from src.kalshi.ws_wire import (
    BookLevel,
    Fill,
    FillPayload,
    MarketLifecycle,
    MarketLifecyclePayload,
    OrderbookDelta,
    OrderbookDeltaPayload,
    OrderbookSnapshot,
    OrderbookSnapshotPayload,
    UserOrder,
    UserOrderPayload,
)


def _snapshot(ticker: str, yes_levels: list[tuple[int, int]], no_levels: list[tuple[int, int]]) -> OrderbookSnapshot:
    return OrderbookSnapshot(
        type="orderbook_snapshot", sid=1, seq=1,
        msg=OrderbookSnapshotPayload(
            market_ticker=ticker, market_id="1",
            yes=[BookLevel(price_cents=p, quantity=q) for p, q in yes_levels],
            no=[BookLevel(price_cents=p, quantity=q) for p, q in no_levels],
        ),
    )


def _delta(ticker: str, side: str, price_cents: int, delta: int) -> OrderbookDelta:
    return OrderbookDelta(
        type="orderbook_delta", sid=1, seq=2,
        msg=OrderbookDeltaPayload(
            market_ticker=ticker, market_id="1",
            price_cents=price_cents, delta=delta, side=side, ts=None,
        ),
    )


class TestOrderbookIngestion:
    def test_snapshot_creates_book(self) -> None:
        s = LiveState()
        s.apply_orderbook_snapshot(_snapshot("KX-1", [(42, 100), (41, 50)], [(58, 60)]))
        book = s.books["KX-1"]
        assert book.yes.levels == {42: 100, 41: 50}
        assert book.no.levels == {58: 60}
        assert book.last_update > 0

    def test_best_prices_after_snapshot(self) -> None:
        """Kalshi's `yes` and `no` arrays BOTH hold bids. The implied ask on
        one side is `100 - best_bid_on_other_side`."""
        s = LiveState()
        s.apply_orderbook_snapshot(_snapshot(
            "KX-1",
            yes_levels=[(42, 100), (41, 50), (40, 25)],  # YES bids
            no_levels=[(58, 60), (59, 30)],              # NO bids
        ))
        book = s.books["KX-1"]
        assert book.yes_best_bid == 42  # max(YES bids)
        # Best YES ask = 100 - max(NO bids) = 100 - 59 = 41
        # (someone bidding 59¢ for NO is selling YES at 41¢)
        assert book.yes_best_ask == 41
        assert book.no_best_bid == 59  # max(NO bids)
        # Best NO ask = 100 - max(YES bids) = 100 - 42 = 58
        assert book.no_best_ask == 58

    def test_delta_adds_liquidity(self) -> None:
        s = LiveState()
        s.apply_orderbook_snapshot(_snapshot("KX-1", [(42, 100)], []))
        s.apply_orderbook_delta(_delta("KX-1", "yes", 42, 25))
        assert s.books["KX-1"].yes.levels[42] == 125

    def test_delta_removes_level_when_quantity_hits_zero(self) -> None:
        s = LiveState()
        s.apply_orderbook_snapshot(_snapshot("KX-1", [(42, 100), (41, 50)], []))
        s.apply_orderbook_delta(_delta("KX-1", "yes", 42, -100))
        book = s.books["KX-1"]
        assert 42 not in book.yes.levels
        assert 41 in book.yes.levels
        assert book.yes_best_bid == 41

    def test_delta_creates_new_level(self) -> None:
        s = LiveState()
        s.apply_orderbook_snapshot(_snapshot("KX-1", [(42, 100)], []))
        s.apply_orderbook_delta(_delta("KX-1", "yes", 45, 30))
        assert s.books["KX-1"].yes.levels[45] == 30

    def test_delta_on_unknown_market_creates_book(self) -> None:
        """A delta arriving before the snapshot shouldn't crash — the next
        snapshot will overwrite."""
        s = LiveState()
        s.apply_orderbook_delta(_delta("KX-NEW", "yes", 50, 10))
        assert "KX-NEW" in s.books
        assert s.books["KX-NEW"].yes.levels == {50: 10}

    def test_empty_book_returns_none_for_best_prices(self) -> None:
        s = LiveState()
        book = s.get_or_create_book("KX-EMPTY")
        assert book.yes_best_bid is None
        assert book.no_best_ask is None


class TestUserOrderIngestion:
    def test_resting_order_stored(self) -> None:
        s = LiveState()
        s.apply_user_order(UserOrder(
            type="user_order", sid=1,
            msg=UserOrderPayload(
                order_id="o1", client_order_id="c1",
                ticker="KX-1", side="yes", status="resting",
                yes_price_cents=42, remaining_count=10,
            ),
        ))
        assert "o1" in s.open_orders
        assert s.open_orders["o1"].remaining_count == 10

    def test_executed_order_removed(self) -> None:
        s = LiveState()
        s.apply_user_order(UserOrder(
            type="user_order", sid=1,
            msg=UserOrderPayload(
                order_id="o1", ticker="X", side="yes",
                status="resting", yes_price_cents=42, remaining_count=5,
            ),
        ))
        assert "o1" in s.open_orders

        s.apply_user_order(UserOrder(
            type="user_order", sid=1,
            msg=UserOrderPayload(
                order_id="o1", ticker="X", side="yes",
                status="executed", yes_price_cents=42, remaining_count=0,
            ),
        ))
        assert "o1" not in s.open_orders

    def test_canceled_order_removed(self) -> None:
        s = LiveState()
        s.apply_user_order(UserOrder(
            type="user_order", sid=1,
            msg=UserOrderPayload(
                order_id="o1", ticker="X", side="yes",
                status="resting", yes_price_cents=42, remaining_count=5,
            ),
        ))
        s.apply_user_order(UserOrder(
            type="user_order", sid=1,
            msg=UserOrderPayload(
                order_id="o1", ticker="X", side="yes",
                status="canceled", yes_price_cents=42, remaining_count=5,
            ),
        ))
        assert "o1" not in s.open_orders


class TestMarketLifecycle:
    def test_status_updates(self) -> None:
        s = LiveState()
        s.apply_orderbook_snapshot(_snapshot("KX-1", [(42, 10)], []))
        assert s.books["KX-1"].status == "open"
        s.apply_market_lifecycle(MarketLifecycle(
            type="market_lifecycle", sid=1,
            msg=MarketLifecyclePayload(market_ticker="KX-1", status="settled", settlement_value=100),
        ))
        assert s.books["KX-1"].status == "settled"


class TestFillTouchesTimestamp:
    def test_fill_updates_ws_timestamp(self) -> None:
        """Fills are persisted by bet_service; LiveState just notes liveness."""
        s = LiveState()
        before = s.last_ws_message_at
        s.apply_fill(Fill(
            type="fill", sid=1,
            msg=FillPayload(
                trade_id="t", order_id="o", ticker="X", side="yes", action="buy",
                count=1, yes_price_cents=42, no_price_cents=58,
            ),
        ))
        assert s.last_ws_message_at > before
