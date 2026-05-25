"""Application configuration loaded from environment variables.

All config flows through this single Settings object. Importing anywhere else creates a
second source of truth, so don't. Read `settings = get_settings()` once at the call site.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# .env lives at the repo root (../../.. from this file: src/config.py → backend/ → repo).
# Resolving relative to __file__ makes the lookup cwd-independent — running from
# backend/, the repo root, or via systemd all behave identically.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENV_FILE = _REPO_ROOT / ".env"


class Environment(StrEnum):
    """Which Kalshi API the app talks to. Switching requires a process restart."""

    DEMO = "demo"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """All runtime configuration. Loaded from .env (or process env)."""

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # === Runtime environment ===
    environment: Environment = Field(
        default=Environment.DEMO,
        description="demo or production. No UI toggle — restart to change.",
    )

    # === Kalshi ===
    kalshi_key_path: Path = Field(
        default=Path("/var/www/lutz-bot/secrets/kalshi-demo.pem"),
        description="Path to RSA private key. Permissions must be 0o600 or stricter.",
    )
    kalshi_key_id: str = Field(default="", description="Kalshi-issued key ID.")

    # === Sports data ===
    api_football_key: str = Field(default="", description="API-Football key.")
    odds_api_key: str = Field(default="", description="The Odds API key.")

    # === LLM (provider undecided until Phase 4) ===
    llm_provider: str = Field(default="", description="Set in Phase 4.")
    llm_api_key: str = Field(default="", description="Set in Phase 4.")

    # === Database ===
    database_path: Path = Field(
        default=Path("/var/www/lutz-bot/data/kalshibot.db"),
        description="SQLite file path.",
    )

    # === Bankroll ===
    bankroll_usd: int = Field(
        default=500,
        ge=0,
        description="Current bankroll in dollars. Used by Kelly sizing.",
    )

    @field_validator("kalshi_key_path", "database_path", mode="before")
    @classmethod
    def expand_path(cls, v: str | Path) -> Path:
        return Path(v).expanduser() if isinstance(v, str) else v

    @property
    def is_production(self) -> bool:
        return self.environment == Environment.PRODUCTION

    @property
    def kalshi_api_base(self) -> str:
        """Kalshi REST base URL for the current environment."""
        if self.is_production:
            return "https://api.elections.kalshi.com/trade-api/v2"
        return "https://demo-api.kalshi.co/trade-api/v2"

    @property
    def kalshi_ws_url(self) -> str:
        """Kalshi WebSocket URL for the current environment."""
        if self.is_production:
            return "wss://api.elections.kalshi.com/trade-api/ws/v2"
        return "wss://demo-api.kalshi.co/trade-api/ws/v2"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor. Tests can call `get_settings.cache_clear()`."""
    return Settings()
