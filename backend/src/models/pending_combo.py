"""PENDING_COMBO — a combo whose quote we accepted, awaiting its fill.

Combos fill via RFQ: we accept a maker's quote (a 204, no order_id), the maker
confirms, and the order fills ASYNC on the WS fill channel. The fill carries the
combo ticker + the real order_id but NOT the legs. So at accept time we stash
the legs + reflective metadata here, keyed by the combo ticker; when the
matching combo fill lands, record_fill creates the bet from this row (real
price/centi/order_id from the fill, legs from here) and deletes the row.

DB-backed (not in-memory) on purpose: a combo accepted right before a restart
must still record correctly when its fill arrives. A row with no fill within the
cleanup window (maker never confirmed / execution timer lapsed) is swept — no
phantom bet is ever created.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from src.core.db import Base


class PendingCombo(Base):
    __tablename__ = "pending_combo"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    combo_ticker: Mapped[str] = mapped_column(String(128), nullable=False)
    """The materialized combo market ticker the fill will reference. Unique:
    one pending accept per combo ticker at a time."""

    side: Mapped[str] = mapped_column(String(8), nullable=False)
    """The side we accepted ('yes'/'no')."""
    count: Mapped[int] = mapped_column(Integer, nullable=False)
    """Contracts requested. The fill's actual centi is authoritative for the
    recorded quantity; this is the intended size for reference."""

    legs_json: Mapped[list[dict[str, str | None]]] = mapped_column(JSON, nullable=False)
    """Serialized ComboLegInput list: [{leg_ticker, leg_event_ticker,
    leg_title, side}, …]. Written into combo_leg rows when the bet is created."""

    # Reflective metadata captured at accept, applied to the bet on creation.
    strategy: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[str] = mapped_column(String(8), nullable=False)
    timing: Mapped[str] = mapped_column(String(16), nullable=False)
    tags_json: Mapped[list[str] | None] = mapped_column(JSON)
    human_reasoning: Mapped[str | None] = mapped_column(String(2048))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("combo_ticker", name="uq_pending_combo_ticker"),
    )
