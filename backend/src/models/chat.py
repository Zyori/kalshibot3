"""CHAT_MESSAGE — persistent chat with the AI partner, per sport.

The plan defers chat to Phase 4 but lands the schema now so migrations don't
churn later. `referenced_ids` holds the IDs of suggestions/bets/markets the
message mentions — used by the frontend to render entity chips.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from src.core.db import Base
from src.core.types import ChatRole, Sport


class ChatMessage(Base):
    __tablename__ = "chat_message"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sport: Mapped[Sport] = mapped_column(String(16), nullable=False)
    role: Mapped[ChatRole] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    referenced_ids: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    """{"suggestion_ids": [...], "bet_ids": [...], "market_ids": [...]}"""

    agent_initiated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("sport IN ('soccer', 'nfl')", name="ck_chat_sport"),
    )
