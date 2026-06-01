"""SUGGESTION — a recommended bet (from AI or future "human-proposed" flow).

The plan deliberately uses a single SUGGESTION entity for both AI and high-urgency
"alert" cases — `urgency` is a field, not a separate ALERT table. Sound + visual
pulse trigger off `urgency = HIGH`.

`suggestion_group_id` is reserved for multi-leg parlay suggestions (Phase 4+).
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
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from src.core.db import Base
from src.core.types import (
    BetSide,
    Confidence,
    Sport,
    Strategy,
    SuggestionKind,
    SuggestionStatus,
    Urgency,
)


class Suggestion(Base):
    __tablename__ = "suggestion"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sport: Mapped[Sport] = mapped_column(String(16), nullable=False)
    market_id: Mapped[int] = mapped_column(
        ForeignKey("market.id", ondelete="CASCADE"), nullable=False
    )

    kind: Mapped[SuggestionKind] = mapped_column(String(8), nullable=False)
    """entry = open a position; exit = close a held one. The frontend renders
    entry cards in the feed and exit cards on the held market."""

    suggestion_group_id: Mapped[int | None] = mapped_column(Integer)
    """Multi-leg parlays share a group_id. Single-leg suggestions leave this null."""

    side: Mapped[BetSide] = mapped_column(String(8), nullable=False)
    suggested_price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    suggested_size_cents: Mapped[int] = mapped_column(Integer, nullable=False)

    kelly_fraction_bps: Mapped[int | None] = mapped_column(Integer)
    estimated_edge_bps: Mapped[int | None] = mapped_column(Integer)
    ai_probability_pct: Mapped[int | None] = mapped_column(Integer)
    market_probability_pct: Mapped[int | None] = mapped_column(Integer)

    strategy: Mapped[Strategy] = mapped_column(String(32), nullable=False)
    justification: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[Confidence] = mapped_column(String(8), nullable=False)
    urgency: Mapped[Urgency] = mapped_column(String(8), nullable=False)

    status: Mapped[SuggestionStatus] = mapped_column(String(16), nullable=False)
    rejection_reason: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    acted_on_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("sport IN ('soccer', 'nfl')", name="ck_suggestion_sport"),
        CheckConstraint("kind IN ('entry', 'exit')", name="ck_suggestion_kind"),
        CheckConstraint(
            "suggested_price_cents >= 1 AND suggested_price_cents <= 99",
            name="ck_suggestion_price_range",
        ),
        CheckConstraint("suggested_size_cents >= 0", name="ck_suggestion_size_nonneg"),
        Index("ix_suggestion_sport_status", "sport", "status"),
        Index("ix_suggestion_group", "suggestion_group_id"),
    )
