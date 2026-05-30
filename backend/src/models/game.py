"""GAME — a sporting event we track for market context.

Score, minute, and period are extracted into proper columns (not buried in JSON)
because we query them for live-game UI sorting and strategy triggers.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from src.core.db import Base
from src.core.types import GameStatus, Sport


class Game(Base):
    __tablename__ = "game"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sport: Mapped[Sport] = mapped_column(String(16), nullable=False)
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    """ID from the data provider (API-Football match ID for soccer)."""

    home_team: Mapped[str] = mapped_column(String(128), nullable=False)
    away_team: Mapped[str] = mapped_column(String(128), nullable=False)
    kickoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[GameStatus] = mapped_column(String(16), nullable=False)

    # Extracted from live_state for indexable queries.
    home_score: Mapped[int | None] = mapped_column(Integer)
    away_score: Mapped[int | None] = mapped_column(Integer)
    minute: Mapped[int | None] = mapped_column(Integer)
    period: Mapped[str | None] = mapped_column(String(8))

    live_state: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    """Full event list and other volatile data from the provider. Frozen on FT."""

    meta: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON)
    """Venue, weather, lineups — context that doesn't change mid-match."""

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
        UniqueConstraint("external_id", name="uq_game_external_id"),
        CheckConstraint("sport IN ('soccer', 'nfl')", name="ck_game_sport"),
        Index("ix_game_sport_status_kickoff", "sport", "status", "kickoff"),
    )
