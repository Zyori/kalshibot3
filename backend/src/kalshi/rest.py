"""Kalshi REST client.

Read-only endpoints (markets, events, orderbook, balance, positions, fills)
follow V2's patterns. Order endpoints (place, cancel, amend) port V1's
TypeScript implementation. The error classifier and token bucket rate limiter
are both borrowed from prior projects.

Cents are the boundary. Inputs and outputs of this class are always integer
cents. Conversion lives in `kalshi/schemas.py`, not here.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

import httpx

from src.config import get_settings
from src.core.auth import KalshiAuth
from src.core.exceptions import (
    AlreadyExecuted,
    InsufficientBalance,
    KalshiError,
    MarketHalted,
    PostOnlyRejected,
    RateLimited,
)
from src.core.logging import get_logger
from src.kalshi.schemas import (
    BalanceResponse,
    CancelOrderResponse,
    EventsResponse,
    FillsResponse,
    MarketsResponse,
    Orderbook,
    OrderbookResponse,
    PlaceOrderRequest,
    PlaceOrderResponse,
    PositionsResponse,
)

log = get_logger(__name__)


class TokenBucket:
    """Async token bucket. Drains at `rate` tokens/sec, capped at `capacity`.

    Use one bucket per client. Call `await bucket.acquire()` before every
    outbound request — if the bucket is empty, the call awaits until a token
    is available rather than failing.

    Ported from V2 (ingestion/kalshi_rest.py).
    """

    def __init__(self, rate: float, capacity: int) -> None:
        self.rate = rate
        self.capacity = capacity
        self.tokens = float(capacity)
        self.last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self.tokens = min(
                    self.capacity, self.tokens + (now - self.last_refill) * self.rate
                )
                self.last_refill = now
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return
                wait = (1.0 - self.tokens) / self.rate
                await asyncio.sleep(wait)


def _classify_kalshi_error(status: int, body: str) -> KalshiError:
    """Map a Kalshi error response to a typed exception.

    Body inspection is heuristic — Kalshi's error envelope isn't fully
    documented and the message strings have shifted over time. Ported from
    V1 (api/kalshi-rest.ts classifyError) with the same string fragments.
    """
    lower = body.lower()
    if "post_only" in lower or "post only" in lower or "would cross" in lower:
        return PostOnlyRejected(f"post-only rejected: {body}", status=status)
    if "insufficient" in lower or "balance" in lower:
        return InsufficientBalance(f"insufficient balance: {body}", status=status)
    if "halted" in lower or "market is closed" in lower:
        return MarketHalted(f"market halted/closed: {body}", status=status)
    if "already" in lower and "executed" in lower:
        return AlreadyExecuted(f"already executed: {body}", status=status)
    return KalshiError(f"HTTP {status}: {body}", status=status)


def new_client_order_id() -> str:
    """Generate a fresh UUID for use as a Kalshi client_order_id (idempotency)."""
    return str(uuid.uuid4())


class KalshiRestClient:
    """Async HTTP client for Kalshi's REST API.

    Construct one per process. The httpx.AsyncClient is shared across all
    methods. Call `await client.aclose()` on shutdown.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self.base_url = settings.kalshi_api_base
        self.auth = KalshiAuth(settings.kalshi_key_id, settings.kalshi_key_path)
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            auth=self.auth,
            timeout=15.0,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        # 8 req/s sustained with bursts up to 10 — slightly under Kalshi's
        # documented limits to leave headroom for WS and unrelated callers.
        self.rate_limiter = TokenBucket(rate=8.0, capacity=10)

    async def aclose(self) -> None:
        await self.client.aclose()

    async def __aenter__(self) -> "KalshiRestClient":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send one request, handle 429 retry, raise typed errors otherwise."""
        await self.rate_limiter.acquire()
        try:
            resp = await self.client.request(method, path, params=params, json=json)
        except httpx.RequestError as e:
            log.error("kalshi_network_error", path=path, method=method, error=str(e))
            raise KalshiError(f"network error: {e}") from e

        if resp.status_code == 429:
            retry_after = float(resp.headers.get("Retry-After", "1"))
            log.warning("kalshi_rate_limited", path=path, retry_after_s=retry_after)
            raise RateLimited(f"rate limited: {path}", retry_after_s=retry_after)

        if not resp.is_success:
            log.error(
                "kalshi_api_error",
                path=path,
                method=method,
                status=resp.status_code,
                body=resp.text[:200],
            )
            raise _classify_kalshi_error(resp.status_code, resp.text)

        return resp.json()

    # === Portfolio / health ===

    async def get_balance(self) -> BalanceResponse:
        """Used at startup to verify auth works before serving traffic."""
        data = await self._request("GET", "/portfolio/balance")
        return BalanceResponse.model_validate(data)

    async def get_positions(self, cursor: str | None = None) -> PositionsResponse:
        params = {"cursor": cursor} if cursor else None
        data = await self._request("GET", "/portfolio/positions", params=params)
        return PositionsResponse.model_validate(data)

    async def get_fills(
        self, *, ticker: str | None = None, cursor: str | None = None
    ) -> FillsResponse:
        params: dict[str, Any] = {}
        if ticker:
            params["ticker"] = ticker
        if cursor:
            params["cursor"] = cursor
        data = await self._request("GET", "/portfolio/fills", params=params or None)
        return FillsResponse.model_validate(data)

    # === Markets ===

    async def get_markets(
        self,
        *,
        event_ticker: str | None = None,
        status: str | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> MarketsResponse:
        params: dict[str, Any] = {"limit": limit}
        if event_ticker:
            params["event_ticker"] = event_ticker
        if status:
            params["status"] = status
        if cursor:
            params["cursor"] = cursor
        data = await self._request("GET", "/markets", params=params)
        return MarketsResponse.model_validate(data)

    async def get_market(self, ticker: str) -> dict[str, Any]:
        return await self._request("GET", f"/markets/{ticker}")

    async def get_orderbook(self, ticker: str, depth: int = 5) -> Orderbook:
        data = await self._request(
            "GET", f"/markets/{ticker}/orderbook", params={"depth": depth}
        )
        return OrderbookResponse.model_validate(data).orderbook

    async def get_events(
        self,
        *,
        series_ticker: str,
        limit: int = 200,
        cursor: str | None = None,
        with_nested_markets: bool = True,
    ) -> EventsResponse:
        params: dict[str, Any] = {
            "series_ticker": series_ticker,
            "limit": limit,
            "with_nested_markets": str(with_nested_markets).lower(),
        }
        if cursor:
            params["cursor"] = cursor
        data = await self._request("GET", "/events", params=params)
        return EventsResponse.model_validate(data)

    # === Orders ===

    async def place_order(self, req: PlaceOrderRequest) -> PlaceOrderResponse:
        """Submit a new order. Caller must supply `req.client_order_id`.

        Use `new_client_order_id()` if you don't already have an idempotency
        key from an upstream caller — never make Kalshi mint one.
        """
        log.info(
            "place_order",
            ticker=req.ticker,
            side=req.side,
            action=req.action,
            count=req.count,
            yes_price=req.yes_price,
            no_price=req.no_price,
            client_order_id=req.client_order_id,
        )
        data = await self._request(
            "POST", "/portfolio/orders", json=req.model_dump(exclude_none=True)
        )
        return PlaceOrderResponse.model_validate(data)

    async def cancel_order(self, order_id: str) -> CancelOrderResponse:
        log.info("cancel_order", order_id=order_id)
        data = await self._request("DELETE", f"/portfolio/orders/{order_id}")
        return CancelOrderResponse.model_validate(data)
