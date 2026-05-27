"""BET_FILL — one Kalshi fill, captured verbatim.

Source of truth for every individual execution. A bet (one buy decision)
may have many bet_fill rows: the buy itself can fragment into multiple
fills, and the eventual exits can be multiple sells. Aggregates on Bet
(entry_fees_cents, exit_fees_cents, entry_price_cents avg, exit_price_cents
avg, realized_pnl_cents) are derived from sums over this table.

Why this exists:
  - Kalshi's /portfolio/fills returns a per-fill `fee_cost`. That is the
    authoritative fee value. Storing each fill lets us reconcile fees to
    the cent with no estimation formula (V1's mistake — see fees.ts in
    /var/www/_reference/Kalshi-Bot/).
  - Sells executed in chunks ("sold 40, then 60") all attach to the same
    opener bet, decrementing remaining_quantity until 0. The chunks are
    visible here for audit/drill-down.

bet_id may be NULL: external fills (placed directly on kalshi.com) are
recorded for audit but never bound to a bet. See memory
feedback_no_external_fill_reconciliation.

fee_cents may be NULL until the periodic fills_sync sweep populates it
from REST — the WS fill event doesn't carry fees.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
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


class BetFill(Base):
    __tablename__ = "bet_fill"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bet_id: Mapped[int | None] = mapped_column(
        ForeignKey("bet.id", ondelete="SET NULL")
    )
    trade_id: Mapped[str] = mapped_column(String(64), nullable=False)
    order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    ticker: Mapped[str] = mapped_column(String(128), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    action: Mapped[str] = mapped_column(String(8), nullable=False)
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity_centi: Mapped[int] = mapped_column(Integer, nullable=False)
    """Hundredths of a contract. Kalshi reports fills in fractional
    contracts via `count_fp` (e.g. 0.03 + 0.97 = one logical 1-contract
    trade split across fee tiers). Storing as int * 100 preserves Kalshi's
    granularity without floats. To get contracts: quantity_centi / 100."""
    fee_cents: Mapped[int | None] = mapped_column(Integer)
    is_taker: Mapped[bool | None] = mapped_column(Boolean)
    fee_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("trade_id", name="uq_bet_fill_trade_id"),
        CheckConstraint("action IN ('buy', 'sell')", name="ck_bet_fill_action"),
        CheckConstraint("side IN ('yes', 'no')", name="ck_bet_fill_side"),
        CheckConstraint(
            "price_cents >= 1 AND price_cents <= 99",
            name="ck_bet_fill_price_range",
        ),
        CheckConstraint("quantity_centi >= 1", name="ck_bet_fill_quantity_positive"),
        Index("ix_bet_fill_bet_id", "bet_id"),
        Index("ix_bet_fill_order_id", "order_id"),
        Index("ix_bet_fill_ticker", "ticker"),
        Index("ix_bet_fill_fee_synced_at", "fee_synced_at"),
    )
