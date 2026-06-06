"""Shared concurrency helpers.

The supervisor owns a single `_ledger_write_lock` that serializes every write
to the ledger's money rows (bets, fills). The WS fill/cancel/settlement handlers
hold it directly; the background sweepers (settlement, fills) hold it via this
guard so all writers serialize against one another. Without that, a settlement
landing in the same window as a sell fill is a lost update on realized PnL.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


@asynccontextmanager
async def ledger_guard(lock: asyncio.Lock | None) -> AsyncIterator[None]:
    """Hold `lock` if one was passed, else no-op. The no-op path lets the sweep
    functions stay usable in tests and standalone runs that have no supervisor
    lock to share."""
    if lock is None:
        yield
        return
    async with lock:
        yield
