"""TRADE_SNAPSHOT — frozen run-of-play at a fill moment, stapled to a bet.

The app's edge is selling the swing, not riding to settlement. To learn
whether we actually do that, a closed trade needs the game state at the
moments we got in and got out. That state lives only in app memory while a
game is live in the feed; the second the game ends it's gone. This table
freezes it at fill time so finished trades can be reviewed for exit-timing.

Three phases per bet, each captured exactly once (unique on bet_id+phase):
  - entry      first buy fill — when we got in
  - exit_open  first sell fill — when we STARTED getting out
  - exit_close the sell that took remaining_quantity to 0 — when we were OUT

A clean single-sell exit emits exit_open and exit_close from the same fill
(same minute); a scale-out (sell at 75', hold to 90') emits them at the two
distinct minutes, so the post-mortem sees the held-too-long tail instead of
a single misleadingly-early exit. A bet ridden to settlement never sells, so
it has no exit_* rows — that absence IS the finding.

Bounded by construction: at most three rows per bet, and ondelete=CASCADE
makes the FK the entire lifecycle — delete the bet, the snapshots go. No
TTL, no eviction job.

Nullable game-state columns: a pre-match fill (no live game in the feed)
captures only the market mid; run_of_play_json / game_clock / scores are
null. That's a valid snapshot, not an error — capture what's available,
never block the fill.

Retrospective only: this is read when the user asks "how have my exits
been," never wired into LUTZ's live reads. See the spec at
docs/plans/2026-06-02-001-feat-trade-snapshot-postmortem-plan.md.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
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
from src.core.types import SnapshotPhase

_PHASE_IN_SQL = ", ".join(f"'{p.value}'" for p in SnapshotPhase)
"""`'entry', 'exit_open', 'exit_close'` — derived from the enum so the CHECK
can't drift from SnapshotPhase. The frozen migration carries the same literal."""


class TradeSnapshot(Base):
    __tablename__ = "trade_snapshot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bet_id: Mapped[int] = mapped_column(
        ForeignKey("bet.id", ondelete="CASCADE"), nullable=False
    )
    phase: Mapped[str] = mapped_column(String(16), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    """The fill moment this snapshot froze — the WS fill's timestamp."""

    game_clock: Mapped[str | None] = mapped_column(String(16))
    """ESPN clock verbatim ('67:42', '45+2:00'). Stored as the source string,
    not parsed to an int — '45+2' and penalties make int lossy, and the
    post-mortem reads the raw clock fine. Null when no live game state."""
    score_home: Mapped[int | None] = mapped_column(Integer)
    score_away: Mapped[int | None] = mapped_column(Integer)
    run_of_play_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    """The frozen live run-of-play, serialized by the SAME serializer the
    events route uses (live_payload) so it's byte-identical to what the site
    and LUTZ saw live. Null for a pre-match fill with no live game."""

    market_mid_cents: Mapped[int | None] = mapped_column(Integer)
    """Top-of-book YES mid at capture, via the same _mark_price_cents helper
    position marks and the price tape use. Null if the book had no mid."""
    price_history_json: Mapped[list[dict[str, int]] | None] = mapped_column(JSON)
    """Recent mid trajectory around the fill, [{"mid_cents": int}, …] oldest
    first — same shape /partner/context serves. The monotonic timestamp is
    dropped (process-relative, meaningless once frozen); order carries it."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            f"phase IN ({_PHASE_IN_SQL})",
            name="ck_trade_snapshot_phase",
        ),
        UniqueConstraint("bet_id", "phase", name="uq_trade_snapshot_bet_phase"),
        Index("ix_trade_snapshot_bet_id", "bet_id"),
    )
