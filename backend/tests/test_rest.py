"""Tests for src/kalshi/rest.py — the parts that don't need the network."""

from __future__ import annotations

import time

import pytest

from src.core.exceptions import (
    AlreadyExecuted,
    InsufficientBalance,
    KalshiError,
    MarketHalted,
    PostOnlyRejected,
)
from src.kalshi.rest import TokenBucket, _classify_kalshi_error, new_client_order_id


class TestClassifyError:
    """The error classifier is the only thing between Kalshi's prose errors and
    our typed exception hierarchy. If it misroutes, the wrong recovery path
    runs — at best a worse log, at worst a retry on a permanent failure."""

    @pytest.mark.parametrize(
        "body",
        [
            "order would cross the spread",
            "post_only rejected",
            "post only flag set",
        ],
    )
    def test_post_only(self, body: str) -> None:
        assert isinstance(_classify_kalshi_error(400, body), PostOnlyRejected)

    @pytest.mark.parametrize(
        "body",
        ["insufficient funds", "Account balance is too low"],
    )
    def test_insufficient_balance(self, body: str) -> None:
        assert isinstance(_classify_kalshi_error(400, body), InsufficientBalance)

    @pytest.mark.parametrize(
        "body",
        ["market halted for review", "the market is closed"],
    )
    def test_market_halted(self, body: str) -> None:
        assert isinstance(_classify_kalshi_error(400, body), MarketHalted)

    def test_already_executed(self) -> None:
        assert isinstance(
            _classify_kalshi_error(400, "order already executed"),
            AlreadyExecuted,
        )

    def test_unknown_falls_through(self) -> None:
        err = _classify_kalshi_error(500, "completely opaque error")
        assert isinstance(err, KalshiError)
        assert not isinstance(err, (PostOnlyRejected, InsufficientBalance))
        assert err.status == 500


class TestTokenBucket:
    """The bucket gates every outbound request. If it's wrong we either burn
    rate limit (Kalshi 429s us out) or starve the client (no traffic)."""

    @pytest.mark.asyncio
    async def test_bucket_with_full_capacity_grants_immediately(self) -> None:
        bucket = TokenBucket(rate=10, capacity=5)
        start = time.monotonic()
        for _ in range(5):
            await bucket.acquire()
        # 5 free tokens, should drain in well under a refill interval.
        assert time.monotonic() - start < 0.05

    @pytest.mark.asyncio
    async def test_bucket_waits_when_empty(self) -> None:
        """Drain it, then the next acquire should wait ~1/rate seconds."""
        bucket = TokenBucket(rate=20, capacity=1)
        await bucket.acquire()  # drain
        start = time.monotonic()
        await bucket.acquire()  # must wait for refill
        elapsed = time.monotonic() - start
        # rate=20 -> 50ms per token. Allow generous bounds for scheduler jitter.
        assert 0.03 < elapsed < 0.2


def test_new_client_order_id_is_unique_uuid() -> None:
    a = new_client_order_id()
    b = new_client_order_id()
    assert a != b
    # UUIDv4 with hyphens.
    assert len(a) == 36
    assert a.count("-") == 4
