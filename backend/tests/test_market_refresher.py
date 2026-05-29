"""MarketRefresher tests: scheduling, force-refresh, polling, schema mapping."""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.kalshi.live_state import LiveState
from src.kalshi.schemas import Orderbook, OrderbookLevel, OrderbookResponse
from src.services.market_refresher import MarketRefresher


def _fake_orderbook(yes: list[tuple[int, int]], no: list[tuple[int, int]]) -> Orderbook:
    return Orderbook(
        yes=[OrderbookLevel(price_cents=p, quantity=q) for p, q in yes],
        no=[OrderbookLevel(price_cents=p, quantity=q) for p, q in no],
    )


@pytest.mark.asyncio
async def test_set_far_tickers_enqueues_new_for_immediate_refresh():
    live = LiveState()
    refresher = MarketRefresher(live)

    refresher.set_far_tickers([("KX-1", 100_000.0), ("KX-2", None)])

    now = time.monotonic()
    assert refresher._next_refresh_at["KX-1"] <= now
    assert refresher._next_refresh_at["KX-2"] <= now
    assert refresher._kickoff["KX-1"] == 100_000.0
    assert refresher._kickoff["KX-2"] is None


@pytest.mark.asyncio
async def test_set_far_tickers_preserves_existing_deadline():
    """Re-publishing the same ticker should not reset its next-refresh time
    (otherwise discovery's 60s cadence would force a constant refresh)."""
    live = LiveState()
    refresher = MarketRefresher(live)
    future = time.monotonic() + 1_000.0
    refresher._next_refresh_at["KX-1"] = future
    refresher._kickoff["KX-1"] = 50_000.0

    refresher.set_far_tickers([("KX-1", 50_000.0)])

    assert refresher._next_refresh_at["KX-1"] == future


@pytest.mark.asyncio
async def test_set_far_tickers_drops_removed():
    live = LiveState()
    refresher = MarketRefresher(live)
    refresher.set_far_tickers([("KX-1", None), ("KX-2", None)])

    refresher.set_far_tickers([("KX-1", None)])  # KX-2 gone

    assert "KX-1" in refresher._next_refresh_at
    assert "KX-2" not in refresher._next_refresh_at
    assert "KX-2" not in refresher._kickoff


@pytest.mark.asyncio
async def test_drop_removes_ticker():
    live = LiveState()
    refresher = MarketRefresher(live)
    refresher.set_far_tickers([("KX-1", None)])

    refresher.drop("KX-1")

    assert "KX-1" not in refresher._next_refresh_at
    assert "KX-1" not in refresher._kickoff


@pytest.mark.asyncio
async def test_refresh_now_makes_ticker_due_immediately():
    live = LiveState()
    refresher = MarketRefresher(live)
    refresher._next_refresh_at["KX-1"] = time.monotonic() + 10_000.0
    refresher._kickoff["KX-1"] = None

    refresher.refresh_now("KX-1")

    assert refresher._next_refresh_at["KX-1"] <= time.monotonic()


@pytest.mark.asyncio
async def test_tick_once_polls_only_due_tickers():
    live = LiveState()
    refresher = MarketRefresher(live)
    now = time.monotonic()
    refresher._next_refresh_at = {
        "KX-DUE": now - 1.0,
        "KX-FUTURE": now + 1_000.0,
    }
    refresher._kickoff = {"KX-DUE": None, "KX-FUTURE": None}

    book = _fake_orderbook(yes=[(60, 100)], no=[(30, 200)])
    mock_client = AsyncMock()
    mock_client.get_orderbook.return_value = book
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("src.services.market_refresher.KalshiRestClient", return_value=mock_client):
        await refresher._tick_once()

    # Only KX-DUE was fetched.
    mock_client.get_orderbook.assert_called_once_with("KX-DUE", depth=32)
    # Book got written.
    assert live.books["KX-DUE"].yes.levels == {60: 100}
    assert live.books["KX-DUE"].no.levels == {30: 200}
    assert live.books["KX-DUE"].yes_best_bid == 60
    assert live.books["KX-DUE"].yes_best_ask == 70  # 100 - 30
    # KX-DUE got rescheduled with the FAR cadence (None kickoff → 6h).
    assert refresher._next_refresh_at["KX-DUE"] > time.monotonic() + 5 * 3600
    # KX-FUTURE was not touched.
    assert refresher._next_refresh_at["KX-FUTURE"] == now + 1_000.0


@pytest.mark.asyncio
async def test_failed_poll_reschedules_at_normal_cadence():
    """A poll failure shouldn't lose the ticker or trigger hot-loop retries."""
    live = LiveState()
    refresher = MarketRefresher(live)
    refresher._next_refresh_at["KX-BAD"] = time.monotonic() - 1.0
    refresher._kickoff["KX-BAD"] = None

    mock_client = AsyncMock()
    mock_client.get_orderbook.side_effect = RuntimeError("kalshi 500")
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("src.services.market_refresher.KalshiRestClient", return_value=mock_client):
        await refresher._tick_once()

    assert "KX-BAD" in refresher._next_refresh_at
    assert refresher._next_refresh_at["KX-BAD"] > time.monotonic() + 5 * 3600


@pytest.mark.asyncio
async def test_poll_one_drops_rest_data_for_ws_owned_book():
    """Once WS owns a book, a REST poll must not overwrite it — the REST
    snapshot has a different baseline and would desync the delta stream."""
    live = LiveState()
    refresher = MarketRefresher(live)
    book = live.get_or_create_book("KX-1")
    book.yes.levels = {60: 100.0}
    book.ws_owned = True

    mock_client = AsyncMock()
    mock_client.get_orderbook.return_value = _fake_orderbook(yes=[(55, 5)], no=[(40, 5)])

    await refresher._poll_one(mock_client, "KX-1")

    # REST data dropped — book unchanged.
    assert live.books["KX-1"].yes.levels == {60: 100.0}


@pytest.mark.asyncio
async def test_poll_one_writes_when_not_ws_owned():
    live = LiveState()
    refresher = MarketRefresher(live)
    refresher._kickoff["KX-1"] = None

    mock_client = AsyncMock()
    mock_client.get_orderbook.return_value = _fake_orderbook(yes=[(55, 5)], no=[(40, 5)])

    await refresher._poll_one(mock_client, "KX-1")

    assert live.books["KX-1"].yes.levels == {55: 5}


@pytest.mark.asyncio
async def test_refresh_now_await_does_not_enter_far_schedule():
    """One-shot seed must not register the ticker in the FAR poll schedule —
    it's WS-subscribed and the deltas own it."""
    live = LiveState()
    refresher = MarketRefresher(live)

    mock_client = AsyncMock()
    mock_client.get_orderbook.return_value = _fake_orderbook(yes=[(55, 5)], no=[(40, 5)])
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("src.services.market_refresher.KalshiRestClient", return_value=mock_client):
        await refresher.refresh_now_await("KX-1")

    assert live.books["KX-1"].yes.levels == {55: 5}
    assert "KX-1" not in refresher._next_refresh_at


@pytest.mark.asyncio
async def test_resync_locked_skips_ws_authoritative_ticker():
    """A locked book for a WS-subscribed ticker must NOT trigger a REST resync —
    WS is the source of truth; a REST repoll races the delta stream."""
    live = LiveState()
    refresher = MarketRefresher(live, is_ws_authoritative=lambda _t: True)
    # A crossed/locked book (yes_bid + no_bid >= 100).
    book = live.get_or_create_book("KX-1")
    book.yes.levels = {60: 10.0}
    book.no.levels = {50: 10.0}

    mock_client = AsyncMock()
    with patch("src.services.market_refresher.KalshiRestClient", return_value=mock_client):
        ran = await refresher.resync_locked("KX-1")

    assert ran is False
    mock_client.get_orderbook.assert_not_called()


def test_orderbook_response_parses_real_wire_format():
    """The OrderbookResponse model must handle Kalshi's `orderbook_fp` shape
    with dollar-string prices and notional-dollar quantities."""
    raw = {
        "orderbook_fp": {
            "yes_dollars": [["0.6600", "660.00"], ["0.6500", "32.50"]],
            "no_dollars": [["0.3300", "660.00"], ["0.3200", "320.00"]],
        }
    }
    parsed = OrderbookResponse.model_validate(raw)
    # $660 of contracts at 66¢ = 1000 contracts
    assert parsed.orderbook.yes[0].price_cents == 66
    assert parsed.orderbook.yes[0].quantity == 1000
    assert parsed.orderbook.yes[1].price_cents == 65
    assert parsed.orderbook.yes[1].quantity == 50
    assert parsed.orderbook.no[0].price_cents == 33
    assert parsed.orderbook.no[0].quantity == 2000


def test_orderbook_response_empty_book():
    """Markets with no liquidity return empty arrays — must not raise."""
    parsed = OrderbookResponse.model_validate({"orderbook_fp": {"yes_dollars": [], "no_dollars": []}})
    assert parsed.orderbook.yes == []
    assert parsed.orderbook.no == []


def test_orderbook_response_missing_fp_section():
    """Some markets return `orderbook_fp: null` — treat as empty book."""
    parsed = OrderbookResponse.model_validate({"orderbook_fp": None})
    assert parsed.orderbook.yes == []
    assert parsed.orderbook.no == []
