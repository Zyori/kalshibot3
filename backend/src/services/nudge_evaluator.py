"""Threshold nudge evaluator — LLM-free, edge-triggered.

A nudge is a passive "worth a look — ask the partner?" chip the site shows at
the strategy doc's trigger moments. It is NOT advice and never stages or places
anything (there's no LLM behind it). It's a reminder to open the terminal.

Three triggers, all off data the app already has:
  - a position's unrealized return crosses >= +50%        (position-sync)
  - a live game's clock crosses 75'                        (ESPN observer)
  - a red card appears in a live game                      (ESPN observer)

Edge-triggered: each (subject, trigger) fires once per crossing, never every
tick. Fired keys live in memory and are reset when the subject goes away (a
position closes, a game ends). In-memory is deliberate — a nudge is a reminder,
not money or an action; the worst case after a mid-game restart is one
redundant chip, which isn't worth a table + write path + cleanup to avoid.

The evaluator is pure-ish: it owns only the fired-key set. The supervisor feeds
it current state and broadcasts whatever nudges it returns.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

PROFIT_NUDGE_PCT = 50.0
CLOCK_NUDGE_MINUTE = 75

# ESPN clock is "MM:SS" or "MM+E:SS" (stoppage). We only need the minute.
_CLOCK_RE = re.compile(r"^(\d+)(?:\+(\d+))?:")


@dataclass(frozen=True)
class Nudge:
    """One nudge to broadcast. `subject` identifies what it's about (a ticker
    or an event_ticker); `trigger` is the rule that fired; `label` is the chip
    text the site renders."""

    subject: str
    trigger: str  # 'profit_50' | 'clock_75' | 'red_card'
    label: str


def clock_to_minute(clock: str | None) -> int | None:
    """Parse ESPN's displayClock to a whole minute. '67:42' -> 67,
    '45+2:00' -> 47 (base + stoppage). None when unparseable / pre-match."""
    if not clock:
        return None
    m = _CLOCK_RE.match(clock)
    if m is None:
        return None
    base = int(m.group(1))
    stoppage = int(m.group(2)) if m.group(2) else 0
    return base + stoppage


class NudgeEvaluator:
    """Holds edge-trigger state. One instance on the supervisor."""

    def __init__(self) -> None:
        # Keys we've already fired, as (subject, trigger). Reset per subject
        # when it disappears from the live set.
        self._fired: set[tuple[str, str]] = set()

    def _emit_once(self, subject: str, trigger: str, label: str) -> Nudge | None:
        key = (subject, trigger)
        if key in self._fired:
            return None
        self._fired.add(key)
        return Nudge(subject=subject, trigger=trigger, label=label)

    def evaluate_profit(
        self, positions: list[tuple[str, float | None]]
    ) -> list[Nudge]:
        """positions: (ticker, unrealized_return_pct). Fires profit_50 once when
        a position is at/above +50%. Resets any ticker no longer present (closed)
        so a re-entry can nudge again.

        Edge-trigger note: a position first seen already above +50% (app start
        mid-game) fires once, then is suppressed — that's the desired behavior,
        the user still gets one heads-up."""
        live_tickers = {t for t, _ in positions}
        self._reset_absent(live_tickers, trigger="profit_50")

        out: list[Nudge] = []
        for ticker, pct in positions:
            if pct is None or pct < PROFIT_NUDGE_PCT:
                continue
            n = self._emit_once(
                ticker, "profit_50", f"{ticker} +{pct:.0f}% — ask the partner?"
            )
            if n is not None:
                out.append(n)
        return out

    def evaluate_live_games(
        self,
        games: list[tuple[str, str | None, int]],
    ) -> list[Nudge]:
        """games: (event_ticker, espn_clock, red_card_count). Fires clock_75 when
        the clock crosses 75' and red_card when the red-card count goes above the
        last seen value. Resets games no longer live.

        red_card de-dup is by count: we store the last count in the fired-key set
        encoding so a *second* red card in the same game fires again. Simpler:
        we key red_card per (event, count) — each distinct count fires once."""
        live_events = {ev for ev, _, _ in games}
        self._reset_absent(live_events, trigger="clock_75")
        self._reset_absent_prefix(live_events, prefix="red_card")

        out: list[Nudge] = []
        for event_ticker, clock, reds in games:
            minute = clock_to_minute(clock)
            if minute is not None and minute >= CLOCK_NUDGE_MINUTE:
                n = self._emit_once(
                    event_ticker, "clock_75", f"{event_ticker} 75' — ask the partner?"
                )
                if n is not None:
                    out.append(n)
            if reds > 0:
                # Key per (event, count) so each new red card fires once.
                key = f"red_card:{reds}"
                n = self._emit_once(
                    event_ticker, key, f"{event_ticker} red card — ask the partner?"
                )
                if n is not None:
                    out.append(Nudge(subject=event_ticker, trigger="red_card", label=n.label))
        return out

    def _reset_absent(self, present: set[str], *, trigger: str) -> None:
        self._fired = {
            (s, t) for (s, t) in self._fired if not (t == trigger and s not in present)
        }

    def _reset_absent_prefix(self, present: set[str], *, prefix: str) -> None:
        self._fired = {
            (s, t)
            for (s, t) in self._fired
            if not (t.startswith(prefix) and s not in present)
        }
