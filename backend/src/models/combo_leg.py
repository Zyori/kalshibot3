"""COMBO_LEG — one leg of a combo (multivariate / parlay) bet.

A combo bet is a single Kalshi market that bundles several single-market
"legs"; it settles as one atomic binary contract. The legs are descriptive
metadata for a readable ledger and post-mortems — they carry NO money. The
parent Bet remains the single source of truth for stake, fees, and P&L; a
combo settles as a whole, never per leg.

Legs come straight from Kalshi's `mve_selected_legs` on the combo market
(event_ticker, market_ticker, side per leg), plus a human label parsed from
the market's yes_sub_title. `result` is filled in later for post-mortems if we
ever resolve per-leg outcomes; it is not needed for settlement.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from src.core.db import Base


class ComboLeg(Base):
    __tablename__ = "combo_leg"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bet_id: Mapped[int] = mapped_column(
        ForeignKey("bet.id", ondelete="CASCADE"), nullable=False
    )
    leg_index: Mapped[int] = mapped_column(Integer, nullable=False)
    """Position of this leg within the combo (0-based), preserving Kalshi's
    leg order for stable display."""

    leg_ticker: Mapped[str | None] = mapped_column(String(128))
    """The single-market ticker this leg refers to (from mve_selected_legs)."""
    leg_event_ticker: Mapped[str | None] = mapped_column(String(128))
    leg_title: Mapped[str | None] = mapped_column(String(96))
    """Human label, e.g. "Canada" — parsed from the combo's yes_sub_title."""
    side: Mapped[str | None] = mapped_column(String(8))
    """The selected side for this leg ('yes'/'no'). Nullable: a leg we logged
    without resolving its side still renders by title."""
    result: Mapped[str | None] = mapped_column(String(8))
    """Per-leg outcome for post-mortems ('yes'/'no'/None=pending). Not used
    for settlement — the combo settles atomically on the parent Bet."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("bet_id", "leg_index", name="uq_combo_leg_bet_index"),
        CheckConstraint(
            "side IS NULL OR side IN ('yes', 'no')", name="ck_combo_leg_side"
        ),
        CheckConstraint(
            "result IS NULL OR result IN ('yes', 'no')", name="ck_combo_leg_result"
        ),
        Index("ix_combo_leg_bet_id", "bet_id"),
    )
