"""BET — a single bet record. Central entity of the ledger.

A few invariants enforced at the DB level:
  - All monetary fields are integer cents (no floats, no Decimal).
  - kalshi_order_id, kalshi_fill_id, client_order_id are unique when set — these
    are the three idempotency / dedup keys during order placement and position
    reconciliation. Manual / external bets leave them NULL.
  - status has only three terminal values: WON, LOST, CANCELLED. No transitions
    out of terminal — enforced in services/bet_service.py.
  - exit_type is set when status transitions to terminal (except CANCELLED, where
    it stays NULL — there is no exit if the order never filled).

`parent_bet_id` links hedge bets to the parent they hedged.
`version` enables optimistic locking for the close-vs-auto-settle race.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from src.core.db import Base
from src.core.types import (
    BetSide,
    BetSource,
    BetStatus,
    Confidence,
    ExitType,
    Sport,
    Strategy,
    Timing,
)


class Bet(Base):
    __tablename__ = "bet"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sport: Mapped[Sport] = mapped_column(String(16), nullable=False)
    market_id: Mapped[int] = mapped_column(
        ForeignKey("market.id", ondelete="RESTRICT"), nullable=False
    )

    suggestion_id: Mapped[int | None] = mapped_column(
        ForeignKey("suggestion.id", ondelete="SET NULL")
    )
    """NULL for user-initiated bets that bypassed the suggestion flow."""

    parent_bet_id: Mapped[int | None] = mapped_column(
        ForeignKey("bet.id", ondelete="SET NULL")
    )
    """Hedge bets reference the bet they hedged."""

    # === Kalshi correlation IDs (idempotency) ===
    kalshi_order_id: Mapped[str | None] = mapped_column(String(64))
    kalshi_fill_id: Mapped[str | None] = mapped_column(String(64))
    client_order_id: Mapped[str | None] = mapped_column(String(64))

    # === Order details ===
    side: Mapped[BetSide] = mapped_column(String(8), nullable=False)
    entry_price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    exit_price_cents: Mapped[int | None] = mapped_column(Integer)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    stake_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    pnl_cents: Mapped[int | None] = mapped_column(Integer)

    status: Mapped[BetStatus] = mapped_column(String(16), nullable=False)
    exit_type: Mapped[ExitType | None] = mapped_column(String(24))

    # === Provenance + reasoning ===
    source: Mapped[BetSource] = mapped_column(String(16), nullable=False)
    strategy: Mapped[Strategy] = mapped_column(String(32), nullable=False)
    confidence: Mapped[Confidence] = mapped_column(String(8), nullable=False)
    kelly_fraction_bps: Mapped[int | None] = mapped_column(Integer)
    ai_probability_pct: Mapped[int | None] = mapped_column(Integer)
    human_override_sizing: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    human_override_direction: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    human_reasoning: Mapped[str | None] = mapped_column(Text)
    ai_reasoning: Mapped[str | None] = mapped_column(Text)

    # === Timing ===
    timing: Mapped[Timing] = mapped_column(String(16), nullable=False)
    game_period: Mapped[str | None] = mapped_column(String(8))
    game_clock: Mapped[str | None] = mapped_column(String(16))

    # === Flexible tags + audit fields ===
    tags: Mapped[dict | None] = mapped_column(JSON)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    """True for system-placed bets, False for user-entered historical bets."""
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    """Optimistic-locking counter. Incremented on every UPDATE in bet_service."""

    placed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        # Idempotency keys: unique when set, multiple NULLs allowed.
        UniqueConstraint("kalshi_order_id", name="uq_bet_kalshi_order_id"),
        UniqueConstraint("kalshi_fill_id", name="uq_bet_kalshi_fill_id"),
        UniqueConstraint("client_order_id", name="uq_bet_client_order_id"),
        CheckConstraint("sport IN ('soccer', 'nfl')", name="ck_bet_sport"),
        CheckConstraint(
            "entry_price_cents >= 1 AND entry_price_cents <= 99",
            name="ck_bet_entry_price_range",
        ),
        CheckConstraint(
            "exit_price_cents IS NULL OR (exit_price_cents >= 1 AND exit_price_cents <= 99)",
            name="ck_bet_exit_price_range",
        ),
        CheckConstraint("quantity >= 1", name="ck_bet_quantity_positive"),
        CheckConstraint("stake_cents >= 0", name="ck_bet_stake_nonneg"),
        Index("ix_bet_sport_status", "sport", "status"),
        Index("ix_bet_market", "market_id"),
        Index("ix_bet_placed_at", "placed_at"),
    )
