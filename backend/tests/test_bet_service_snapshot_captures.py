"""record_fill returns the trade-snapshot capture events a fill produced.

These lock the phase decisions that drive trade-snapshot capture — the part
with real logic (scale-out exits). The actual snapshot I/O lives in the
supervisor and is mechanical; what matters here is that record_fill emits the
right (bet_id, phase) tuples so the post-mortem sees both ends of an exit.

phases: buy -> entry; first sell on a bet -> exit_open; the sell that drives
remaining to zero -> exit_close. A clean single sell emits both; a scale-out
(sell part at 75', rest at 90') emits exit_open on the first and exit_close on
the last, so the held-too-long tail is visible instead of a misleadingly-early
single exit.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.services.bet_service import record_fill

# Reuse the in-memory fill/bet builders + session-engine setup from the sibling
# suite (single source for the fixture scaffolding). The `session` fixture is
# re-exported below rather than imported by name — importing a fixture trips
# F811 at every test that takes it as a param.
from tests.test_bet_service_fills import _make_fill, _open_bet
from tests.test_bet_service_fills import session as _session_fixture

session = _session_fixture


@pytest.mark.asyncio
async def test_buy_fill_emits_entry(session: AsyncSession) -> None:
    bet = await _open_bet(session, order_id="ord-1", qty=100, price=30)
    captures = await record_fill(session, _make_fill(
        trade_id="b1", order_id="ord-1", side="yes", action="buy",
        price_cents=30, qty=100,
    ))
    assert captures == [(bet.id, "entry")]


@pytest.mark.asyncio
async def test_clean_single_sell_emits_both_exit_phases(session: AsyncSession) -> None:
    """A sell that takes the whole position flat in one shot opens AND closes
    the exit — both phases, same fill (a clean exit, exit_open == exit_close)."""
    bet = await _open_bet(session, order_id="ord-1", qty=100, price=30)
    await record_fill(session, _make_fill(
        trade_id="b1", order_id="ord-1", side="yes", action="buy",
        price_cents=30, qty=100,
    ))
    captures = await record_fill(session, _make_fill(
        trade_id="s1", order_id="ord-2", side="yes", action="sell",
        price_cents=45, qty=100,
    ))
    assert captures == [(bet.id, "exit_open"), (bet.id, "exit_close")]


@pytest.mark.asyncio
async def test_scale_out_splits_exit_open_and_close(session: AsyncSession) -> None:
    """The case the three-phase design exists for: sell part (75'), hold the
    rest (90'). First sell emits exit_open only; the sell that finally zeroes
    the position emits exit_close only. The post-mortem sees both minutes."""
    bet = await _open_bet(session, order_id="ord-1", qty=100, price=30)
    await record_fill(session, _make_fill(
        trade_id="b1", order_id="ord-1", side="yes", action="buy",
        price_cents=30, qty=100,
    ))

    first = await record_fill(session, _make_fill(
        trade_id="s1", order_id="ord-2", side="yes", action="sell",
        price_cents=50, qty=40,
    ))
    assert first == [(bet.id, "exit_open")]

    second = await record_fill(session, _make_fill(
        trade_id="s2", order_id="ord-3", side="yes", action="sell",
        price_cents=42, qty=60,
    ))
    assert second == [(bet.id, "exit_close")]


@pytest.mark.asyncio
async def test_cross_opener_sell_emits_per_opener(session: AsyncSession) -> None:
    """One sell spanning two FIFO openers, closing both, emits exit_open +
    exit_close for each — every touched bet gets its own pair of phases."""
    b1 = await _open_bet(session, order_id="ord-1", qty=40, price=30)
    await record_fill(session, _make_fill(
        trade_id="buy1", order_id="ord-1", side="yes", action="buy",
        price_cents=30, qty=40,
    ))
    b2 = await _open_bet(session, order_id="ord-2", qty=60, price=32)
    await record_fill(session, _make_fill(
        trade_id="buy2", order_id="ord-2", side="yes", action="buy",
        price_cents=32, qty=60,
    ))

    # Single 100-contract sell sweeps both openers flat (FIFO: 40 then 60).
    captures = await record_fill(session, _make_fill(
        trade_id="s1", order_id="ord-3", side="yes", action="sell",
        price_cents=50, qty=100,
    ))
    assert captures == [
        (b1.id, "exit_open"), (b1.id, "exit_close"),
        (b2.id, "exit_open"), (b2.id, "exit_close"),
    ]


@pytest.mark.asyncio
async def test_sell_with_no_opener_emits_nothing(session: AsyncSession) -> None:
    """A sell with no matching OPEN bet (external / settle race) is dropped —
    no bet to attach a snapshot to, so no capture events."""
    captures = await record_fill(session, _make_fill(
        trade_id="s1", order_id="ord-x", side="yes", action="sell",
        price_cents=45, qty=20,
    ))
    assert captures == []


@pytest.mark.asyncio
async def test_replayed_fill_emits_nothing(session: AsyncSession) -> None:
    """An already-recorded fill (WS replay/reconnect) produces no new captures —
    the unique (bet_id, phase) constraint would dedupe anyway, but record_fill
    short-circuits before re-deciding phases."""
    await _open_bet(session, order_id="ord-1", qty=100, price=30)
    f = _make_fill(
        trade_id="b1", order_id="ord-1", side="yes", action="buy",
        price_cents=30, qty=100,
    )
    await record_fill(session, f)
    await session.commit()
    replay = await record_fill(session, f)
    assert replay == []
