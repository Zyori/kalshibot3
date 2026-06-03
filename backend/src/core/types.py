"""Project-wide enums and type aliases.

Single source of truth for every controlled vocabulary the app uses. Strings stored
in the DB reference these enums — if you find yourself writing `"open"` in code,
import `BetStatus.OPEN` instead.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import NewType


def utc_iso(dt: datetime | None) -> str | None:
    """Serialize a datetime to an unambiguous UTC ISO string.

    SQLite's DateTime(timezone=True) is a lie: timezone metadata is
    stripped on write, so we read naive datetimes back even though we
    stored aware ones. Every API timestamp must be tz-aware UTC at the
    wire, otherwise JavaScript's `new Date()` parses it as local time
    and the user sees the wrong hour (which is exactly what happened
    on the Ledger page 2026-05-27).

    Rules:
      None -> None.
      Naive datetime -> assume UTC (matches every site that writes
        datetime.now(timezone.utc) into the DB).
      Aware datetime -> convert to UTC, serialize with 'Z' suffix for
        compactness.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    # 'Z' is shorter than '+00:00' and unambiguous.
    return dt.isoformat().replace("+00:00", "Z")

# === Branded scalars ===
# Python doesn't enforce NewType at runtime, but mypy does. Using these in signatures
# makes "this int is a count of contracts" vs "this int is a price in cents"
# visible to the type checker and self-documenting to humans.

Cents = NewType("Cents", int)
"""Monetary value in integer cents. Kalshi contract prices are 1-99."""

Contracts = NewType("Contracts", int)
"""Count of Kalshi contracts. Always a non-negative integer."""


def dollars_str_to_cents(s: str) -> int:
    """Kalshi wire dollar string ('0.6600', '0.42') → integer cents (66, 42).

    The single dollar→cents converter for every Kalshi wire boundary
    (schemas.py REST, ws_wire.py WS, the markets route). Money is integer
    cents everywhere past this point — this is the one place dollars exist."""
    return int(round(float(s) * 100))

BasisPoints = NewType("BasisPoints", int)
"""Hundredths of a percent. 10000 = 100%, 25 = 0.25%."""


# === Sport ===

class Sport(StrEnum):
    SOCCER = "soccer"
    NFL = "nfl"


# === Bet ===

class BetSide(StrEnum):
    """Direction of a Kalshi position. Matches Kalshi's own vocabulary."""

    YES = "yes"
    NO = "no"


class BetStatus(StrEnum):
    """Lifecycle status of a bet. Three terminal states only.

    Transitions: OPEN → (WON | LOST | CANCELLED). No transitions out of terminal.
    """

    OPEN = "open"
    WON = "won"
    LOST = "lost"
    CANCELLED = "cancelled"


class ExitType(StrEnum):
    """How a bet reached its terminal state. Set only when status is terminal."""

    HELD_TO_SETTLEMENT = "held_to_settlement"
    CLOSED_EARLY = "closed_early"
    HEDGED = "hedged"
    PARTIAL_CLOSE = "partial_close"


class SnapshotPhase(StrEnum):
    """Which fill moment a trade_snapshot froze. Iteration order is the
    canonical post-mortem order (entry, then the exit span), so callers can
    derive a sort key from member position instead of a parallel dict."""

    ENTRY = "entry"
    EXIT_OPEN = "exit_open"
    """First sell on a bet — when we started getting out."""
    EXIT_CLOSE = "exit_close"
    """The sell that drove remaining quantity to zero — when we were fully out."""
    FINAL = "final"
    """The game's own ending, stamped on every positioned bet when ESPN flips
    the match in->post — final score, final clock, and FT/AET/Penalties. Unlike
    the fill-driven phases this fires once per game, not per fill; it's the
    retro-analysis anchor for "how did the game I exited actually end." Last in
    iteration order so the post-mortem reads entry -> exit span -> final."""


class BetSource(StrEnum):
    """Who proposed the bet."""

    HUMAN = "human"
    AI = "ai"
    COLLABORATIVE = "collaborative"
    EXTERNAL = "external"
    """A bet placed directly on kalshi.com, reconciled into our ledger."""


class Strategy(StrEnum):
    """Strategic intent behind the bet.

    Retired values (kept for backwards-compat with historical rows, no longer
    offered in the UI or glossary — old bets must still validate):
      - LIVE_EVENT: superseded by SCALP + the TIMING.LIVE flag.
      - DRAW_VALUE: superseded by TIME_DECAY (the clock-driven-value framing
        that subsumes it). 5 historical bets carry it as of 2026-06-01.
    New bets should pick one of the active values below.
    """

    MEAN_REVERSION = "mean_reversion"
    MEAN_CONFIRMATION = "mean_confirmation"
    LOCK_PARLAY = "lock_parlay"
    UNDERDOG = "underdog"
    MOON_PARLAY = "moon_parlay"
    TIME_DECAY = "time_decay"
    PREDICTIVE = "predictive"
    SCALP = "scalp"
    HEDGE = "hedge"
    MANUAL = "manual"
    # Retired — kept so historical rows still validate. Not a UI/glossary choice.
    DRAW_VALUE = "draw_value"
    LIVE_EVENT = "live_event"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Timing(StrEnum):
    """When the bet was placed relative to the game."""

    PRE_MATCH = "pre_match"
    LIVE = "live"
    FUTURES = "futures"


# === Market ===

class MarketStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    SETTLED = "settled"


class MarketSettlement(StrEnum):
    YES = "yes"
    NO = "no"


# === Game ===

class GameStatus(StrEnum):
    SCHEDULED = "scheduled"
    LIVE = "live"
    FINISHED = "finished"


class GamePeriod(StrEnum):
    """Match period. Soccer-specific values; will be extended per sport."""

    FIRST_HALF = "1H"
    HALFTIME = "HT"
    SECOND_HALF = "2H"
    EXTRA_TIME = "ET"
    PENALTIES = "PEN"
    FULLTIME = "FT"


# === Suggestion ===

class SuggestionStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXPIRED = "expired"


class SuggestionKind(StrEnum):
    """What action a suggestion proposes. Orthogonal to `strategy` (which says
    *why*): an exit suggestion still carries a strategy like `hedge`. `kind`
    says *what* — open a new position or close a held one."""

    ENTRY = "entry"
    EXIT = "exit"


class Urgency(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# === Chat ===

class ChatRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
