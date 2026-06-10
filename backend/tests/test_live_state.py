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


def _delta(ticker: str, side: str, price_cents: int, delta: float) -> OrderbookDelta:
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


class TestFractionalDeltaAccumulation:
    """Kalshi's delta_fp is fixed-point and individual deltas can be fractional
    (e.g. 330.96); only the running per-level sum is integral. The book must
    accumulate them exactly and never truncate — truncating each delta was the
    root cause of phantom levels / crossed books. See the 2026-05-29 fix."""

    def test_fractional_deltas_sum_exactly(self) -> None:
        s = LiveState()
        s.apply_orderbook_snapshot(_snapshot("KX-1", [], []))
        # 330.96 + 1655.00 + 13.04 == 1999.0 exactly; truncating each (330, 1655,
        # 13) would give 1998 and drift the level forever.
        for d in (330.96, 1655.00, 13.04):
            s.apply_orderbook_delta(_delta("KX-1", "yes", 42, d))
        assert s.books["KX-1"].yes.int_levels()[42] == 1999

    def test_fractional_deltas_summing_to_zero_drop_level(self) -> None:
        """A level Kalshi drives to exactly 0 (via fractional steps) must be
        removed — the bug was a residual keeping it alive as a phantom."""
        s = LiveState()
        s.apply_orderbook_snapshot(_snapshot("KX-1", [(42, 100), (41, 50)], []))
        # -0.96 then -99.04 sums to -100, emptying the level.
        s.apply_orderbook_delta(_delta("KX-1", "yes", 42, -0.96))
        s.apply_orderbook_delta(_delta("KX-1", "yes", 42, -99.04))
        book = s.books["KX-1"]
        assert 42 not in book.yes.levels
        assert book.yes_best_bid == 41

    def test_transient_sub_one_residual_does_not_strand_level(self) -> None:
        """A fractional partial that transiently lands a level below 1 contract,
        then a restoring delta, must reconcile to the true integer — not drop
        and rebuild at the fractional remainder."""
        s = LiveState()
        s.apply_orderbook_snapshot(_snapshot("KX-1", [(42, 1)], []))
        # Net zero change across two fractional deltas; level stays at 1.
        s.apply_orderbook_delta(_delta("KX-1", "yes", 42, -0.4))
        s.apply_orderbook_delta(_delta("KX-1", "yes", 42, 0.4))
        assert s.books["KX-1"].yes.int_levels()[42] == 1

    def test_long_run_of_fractional_deltas_does_not_drift(self) -> None:
        """Exact accumulation over many fractional deltas stays on the integer
        — the failure mode was slow drift across thousands of updates."""
        s = LiveState()
        s.apply_orderbook_snapshot(_snapshot("KX-1", [(42, 1000)], []))
        # 1000 deltas of +0.1 and -0.1 alternating net to 0 exactly.
        for i in range(1000):
            s.apply_orderbook_delta(_delta("KX-1", "yes", 42, 0.1 if i % 2 == 0 else -0.1))
        assert s.books["KX-1"].yes.int_levels()[42] == 1000


class TestPresenceMatchesDisplay:
    """A stored level must never render or be selected as qty 0 — presence and
    display rounding are derived from the same round()."""

    def test_int_levels_rounds_to_whole_contracts(self) -> None:
        s = LiveState()
        s.apply_orderbook_snapshot(_snapshot("KX-1", [], []))
        s.apply_orderbook_delta(_delta("KX-1", "yes", 42, 99.6))
        assert s.books["KX-1"].yes.int_levels() == {42: 100}

    def test_level_below_half_contract_is_dropped(self) -> None:
        s = LiveState()
        s.apply_orderbook_snapshot(_snapshot("KX-1", [(42, 1)], []))
        s.apply_orderbook_delta(_delta("KX-1", "yes", 42, -0.6))  # -> 0.4, rounds to 0
        assert 42 not in s.books["KX-1"].yes.levels

    def test_kept_level_never_renders_zero(self) -> None:
        """Every present level rounds to >= 1; best_price never points at a
        level int_levels shows as 0."""
        s = LiveState()
        s.apply_orderbook_snapshot(_snapshot("KX-1", [(42, 1)], []))
        s.apply_orderbook_delta(_delta("KX-1", "yes", 42, -0.4))  # -> 0.6, rounds to 1
        book = s.books["KX-1"]
        assert book.yes_best_bid == 42
        assert book.yes.int_levels()[42] == 1


class TestWsOwnership:
    def test_snapshot_sets_ws_owned(self) -> None:
        s = LiveState()
        assert s.get_or_create_book("KX-1").ws_owned is False
        s.apply_orderbook_snapshot(_snapshot("KX-1", [(42, 10)], []))
        assert s.books["KX-1"].ws_owned is True

    def test_release_clears_ws_owned(self) -> None:
        s = LiveState()
        s.apply_orderbook_snapshot(_snapshot("KX-1", [(42, 10)], []))
        s.release_ws_ownership("KX-1")
        assert s.books["KX-1"].ws_owned is False

    def test_release_on_unknown_ticker_is_noop(self) -> None:
        s = LiveState()
        s.release_ws_ownership("KX-NONE")  # must not raise


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
                count_centi=100, yes_price_cents=42, no_price_cents=58,
            ),
        ))
        assert s.last_ws_message_at > before


class TestToWire:
    """MarketBook.to_wire is the single canonical browser-bound book shape —
    REST (/api/markets) and the WS `book` event both serialize through it, so
    the browser sees one identical shape on cold load and live update."""

    def test_wire_shape_levels_and_derived_prices(self) -> None:
        s = LiveState()
        s.apply_orderbook_snapshot(_snapshot(
            "KX-1",
            yes_levels=[(40, 25), (42, 100), (41, 50)],  # deliberately unsorted
            no_levels=[(58, 60), (59, 30)],
        ))
        wire = s.books["KX-1"].to_wire()
        # Levels sorted highest-price-first, as whole contracts.
        assert wire["yes"] == [
            {"price": 42, "qty": 100},
            {"price": 41, "qty": 50},
            {"price": 40, "qty": 25},
        ]
        assert wire["no"] == [{"price": 59, "qty": 30}, {"price": 58, "qty": 60}]
        # Derived top-of-book matches the MarketBook properties.
        assert wire["yes_bid_cents"] == 42
        assert wire["yes_ask_cents"] == 41  # 100 - max(NO bid 59)
        assert wire["no_bid_cents"] == 59
        assert wire["no_ask_cents"] == 58  # 100 - max(YES bid 42)
        assert wire["ticker"] == "KX-1"

    def test_empty_side_yields_null_prices(self) -> None:
        s = LiveState()
        s.apply_orderbook_snapshot(_snapshot("KX-1", [(95, 10)], []))  # NO side empty
        wire = s.books["KX-1"].to_wire()
        assert wire["yes"] == [{"price": 95, "qty": 10}]
        assert wire["no"] == []
        assert wire["yes_bid_cents"] == 95
        assert wire["yes_ask_cents"] is None   # no NO bids to derive from
        assert wire["no_bid_cents"] is None
        assert wire["no_ask_cents"] == 5       # 100 - 95


class TestBroadcastSerialize:
    """The browser must receive a full `book` event built from the authoritative
    LiveState book, never a raw delta it has to reassemble."""

    def test_orderbook_messages_serialize_to_full_book(self) -> None:
        from src.core.ws_manager import _serialize

        s = LiveState()
        s.apply_orderbook_snapshot(_snapshot("KX-1", [(95, 10)], [(4, 7)]))
        # A delta arrives and is applied to LiveState first (as in ws.listen).
        s.apply_orderbook_delta(_delta("KX-1", "yes", 95, -10))  # drains the 95 level

        # Serializing EITHER the snapshot or the delta yields the current full
        # book (post-delta) read from LiveState — not the raw message payload.
        out = _serialize(_delta("KX-1", "yes", 95, -10), s)
        assert out is not None
        assert out["type"] == "book"
        assert out["yes"] == []                 # 95 level drained
        assert out["no"] == [{"price": 4, "qty": 7}]
        assert out["yes_bid_cents"] is None
        assert out["no_bid_cents"] == 4

    def test_unknown_ticker_serializes_to_none(self) -> None:
        from src.core.ws_manager import _serialize

        s = LiveState()
        # A book message for a ticker LiveState never saw → nothing to send.
        assert _serialize(_delta("KX-GONE", "yes", 50, 5), s) is None
