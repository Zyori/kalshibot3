"""MARKET — a Kalshi tradeable market.

Most markets attach to a Game (match outcomes, totals, props). Tournament futures and
similar markets have no Game; game_id is nullable for those.
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
from src.core.types import MarketSettlement, MarketStatus, Sport


class Market(Base):
    __tablename__ = "market"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sport: Mapped[Sport] = mapped_column(String(16), nullable=False)
    game_id: Mapped[int | None] = mapped_column(
        ForeignKey("game.id", ondelete="SET NULL")
    )
    """Nullable for futures and other non-game markets."""

    kalshi_ticker: Mapped[str] = mapped_column(String(64), nullable=False)
    market_type: Mapped[str] = mapped_column(String(32), nullable=False)
    """e.g. match_result, total_goals, futures, etc."""

    title: Mapped[str] = mapped_column(String(512), nullable=False)

    yes_price_cents: Mapped[int | None] = mapped_column(Integer)
    no_price_cents: Mapped[int | None] = mapped_column(Integer)
    volume: Mapped[int | None] = mapped_column(Integer)

    close_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[MarketStatus] = mapped_column(String(16), nullable=False)

    settlement: Mapped[MarketSettlement | None] = mapped_column(String(8))
    settlement_detected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

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
        UniqueConstraint("kalshi_ticker", name="uq_market_kalshi_ticker"),
        CheckConstraint("sport IN ('soccer', 'nfl', 'combo')", name="ck_market_sport"),
        # Kalshi binary contract prices are integer cents 1-99 when set.
        CheckConstraint(
            "yes_price_cents IS NULL OR (yes_price_cents >= 1 AND yes_price_cents <= 99)",
            name="ck_market_yes_price_range",
        ),
        CheckConstraint(
            "no_price_cents IS NULL OR (no_price_cents >= 1 AND no_price_cents <= 99)",
            name="ck_market_no_price_range",
        ),
    )
