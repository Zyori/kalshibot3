"""Project-wide exception hierarchy.

Catch by category at the API boundary; bubble specifics for logging and metrics.

  BotError
    ├── ConfigurationError      (bad/missing config, refuses to start)
    ├── AuthenticationError     (PEM perms, RSA load, Kalshi auth handshake)
    ├── KalshiError             (HTTP errors from Kalshi)
    │     ├── PostOnlyRejected
    │     ├── InsufficientBalance
    │     ├── MarketHalted
    │     ├── RateLimited
    │     └── AlreadyExecuted
    ├── IngestionError          (API-Football, Odds API, generic network)
    ├── StrategyError           (LLM output failed schema validation, etc.)
    └── RiskLimitExceeded       (RiskManager rejected an order)
"""

from __future__ import annotations


class BotError(Exception):
    """Base for every domain exception in the app."""


class ConfigurationError(BotError):
    """Required config is missing or invalid. App refuses to start."""


class AuthenticationError(BotError):
    """Key loading, file permissions, or auth handshake failed."""


class KalshiError(BotError):
    """Generic Kalshi HTTP/API failure. Specific subclasses below."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class PostOnlyRejected(KalshiError):
    """post_only limit order would have crossed the spread."""


class InsufficientBalance(KalshiError):
    """Order rejected due to insufficient balance."""


class MarketHalted(KalshiError):
    """Market is halted/closed and won't accept orders."""


class RateLimited(KalshiError):
    """Kalshi returned 429. retry_after_s carries the suggested wait."""

    def __init__(self, message: str, retry_after_s: float = 1.0) -> None:
        super().__init__(message, status=429)
        self.retry_after_s = retry_after_s


class AlreadyExecuted(KalshiError):
    """Tried to cancel/amend an order that already filled."""


class IngestionError(BotError):
    """Non-Kalshi upstream data fetch failure (API-Football, Odds API, etc.)."""


class StrategyError(BotError):
    """LLM/strategy module produced output we can't act on."""


class RiskLimitExceeded(BotError):
    """RiskManager refused to let an order through."""
