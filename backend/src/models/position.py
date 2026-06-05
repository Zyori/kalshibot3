"""POSITION — current Kalshi position.

This mirrors Kalshi's authoritative position state. We sync from Kalshi on startup
and every 60s; UPSERT on (market_id, side) so we never accumulate duplicates from
overlapping sync cycles.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from src.core.db import Base
from src.core.types import BetSide, Sport


class Position(Base):
    __tablename__ = "position"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sport: Mapped[Sport] = mapped_column(String(16), nullable=False)
    kalshi_ticker: Mapped[str] = mapped_column(String(64), nullable=False)
    market_id: Mapped[int] = mapped_column(
        ForeignKey("market.id", ondelete="RESTRICT"), nullable=False
    )

    side: Mapped[BetSide] = mapped_column(String(8), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    avg_entry_price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    """Clamped whole-cent average (1–99) for the CHECK constraint and legacy
    callers. For display, prefer the exact price derived from
    cost_basis_cents / quantity — this floored value loses sub-cent precision
    (a 57.71¢ VWAP reads 57 here)."""
    cost_basis_cents: Mapped[int | None] = mapped_column(Integer)
    """Exact total cost from Kalshi's market_exposure (integer cents, no
    flooring). Divided by quantity at the display boundary gives the true
    fractional avg entry price."""
    current_price_cents: Mapped[int | None] = mapped_column(Integer)
    unrealized_pnl_cents: Mapped[int | None] = mapped_column(Integer)
    realized_pnl_cents: Mapped[int | None] = mapped_column(Integer)
    """Kalshi's authoritative realized PnL for this position (fee-inclusive).
    Source of truth — mirrored, not reconstructed."""
    fees_paid_cents: Mapped[int | None] = mapped_column(Integer)
    """Kalshi's authoritative fees paid on this position."""

    last_synced: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
        # Single canonical row per (market, side) — UPSERT against this constraint.
        UniqueConstraint("market_id", "side", name="uq_position_market_side"),
        CheckConstraint("sport IN ('soccer', 'nfl', 'combo')", name="ck_position_sport"),
        CheckConstraint("quantity >= 0", name="ck_position_quantity_nonneg"),
        CheckConstraint(
            "avg_entry_price_cents >= 1 AND avg_entry_price_cents <= 99",
            name="ck_position_avg_entry_range",
        ),
    )
