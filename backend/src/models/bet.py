"""BET — a single bet record. Central entity of the ledger.

A few invariants enforced at the DB level:
  - All monetary fields are integer cents (no floats, no Decimal).
  - kalshi_order_id, client_order_id are unique when set — idempotency keys
    during order placement. Manual / external bets leave them NULL.
  - Per-fill detail lives in bet_fill (keyed by trade_id); Bet aggregates
    are derived from it.
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
    client_order_id: Mapped[str | None] = mapped_column(String(64))

    # === Order details ===
    side: Mapped[BetSide] = mapped_column(String(8), nullable=False)
    entry_price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    exit_price_cents: Mapped[int | None] = mapped_column(Integer)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    remaining_quantity: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    """Whole contracts still held (display/UI use). Floor of
    remaining_quantity_centi / 100. Don't drive terminal state from this;
    use remaining_quantity_centi == 0 instead so sub-contract residuals
    from Kalshi fee-tier splits don't get silently dropped."""
    remaining_quantity_centi: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    """Centicontracts still held. Source of truth for "is the bet closed".
    Kalshi sometimes reports fractional count_fp (e.g. 0.97 + 0.03 spanning
    fee tiers); using whole contracts here floor-divides 3 centi to 0 and
    flips the bet terminal while a real exposure remains."""
    stake_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    pnl_cents: Mapped[int | None] = mapped_column(Integer)
    """Gross PnL at terminal status — kept for backwards compatibility.
    Mirrors realized_pnl_cents once the bet closes. Net PnL is computed
    as pnl_cents - entry_fees_cents - exit_fees_cents at the API layer."""
    realized_pnl_cents: Mapped[int | None] = mapped_column(Integer)
    """Running gross PnL as sell fills close shares. Equals pnl_cents at
    terminal status. NULL while still OPEN with no closes yet."""
    entry_fees_cents: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    """Sum of fee_cents over this bet's buy-side bet_fill rows. Populated
    by the fills-sync sweep (Kalshi's authoritative per-fill fee_cost)."""
    exit_fees_cents: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    """Sum of fee_cents over sell-side bet_fill rows that FIFO-matched to
    this bet. Net PnL = realized_pnl - entry_fees - exit_fees."""

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

    # === Market label (captured at placement, for readable ledger + analysis) ===
    # Codes (home/away/selection) come from the ticker and are always present
    # for a per-game market; full names come from the live ESPN feed and are
    # null when no match was resolved (futures, early pre-match). series maps to
    # a league display name. Nullable throughout — old rows and unparseable
    # tickers fall back to the raw ticker at display.
    event_series: Mapped[str | None] = mapped_column(String(48))
    home_code: Mapped[str | None] = mapped_column(String(8))
    away_code: Mapped[str | None] = mapped_column(String(8))
    home_name: Mapped[str | None] = mapped_column(String(64))
    away_name: Mapped[str | None] = mapped_column(String(64))
    selection_code: Mapped[str | None] = mapped_column(String(8))

    # === Flexible tags + audit fields ===
    tags: Mapped[list[str] | None] = mapped_column(JSON)
    metadata_edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """Set when reflective fields (strategy/source/timing/confidence/tags/
    human_reasoning) are edited after placement. NULL means never edited."""
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
        UniqueConstraint("client_order_id", name="uq_bet_client_order_id"),
        CheckConstraint("sport IN ('soccer', 'nfl')", name="ck_bet_sport"),
        CheckConstraint(
            "entry_price_cents >= 1 AND entry_price_cents <= 99",
            name="ck_bet_entry_price_range",
        ),
        CheckConstraint(
            "exit_price_cents IS NULL OR (exit_price_cents >= 0 AND exit_price_cents <= 100)",
            name="ck_bet_exit_price_range",
        ),
        CheckConstraint("quantity >= 1", name="ck_bet_quantity_positive"),
        CheckConstraint("stake_cents >= 0", name="ck_bet_stake_nonneg"),
        Index("ix_bet_sport_status", "sport", "status"),
        Index("ix_bet_market", "market_id"),
        Index("ix_bet_placed_at", "placed_at"),
    )
