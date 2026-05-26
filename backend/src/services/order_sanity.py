"""Order sanity guard.

Three-tier verdict on a proposed order, given the current orderbook:

  HARD_REFUSE   The order is malformed (bug-level inputs). Reject.
  LOUD_CONFIRM  The order looks objectively bad (paying way above ask
                or selling way below bid). Show a confirm dialog.
  SOFT_WARN     The order is borderline (price 1× spread off best, or
                count exceeds top-level depth). Show inline warning,
                proceed without dialog.
  OK            Looks fine. Send straight to Kalshi.

This is the policy layer for everything user-facing. RiskManager (a
separate module) only refuses bug-level inputs — it never enforces
position-sizing policy because the user manages sizing themselves.
See memory: feedback_sanity_guard_rules, feedback_no_hard_risk_limits.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal


class Verdict(StrEnum):
    OK = "ok"
    SOFT_WARN = "soft_warn"
    LOUD_CONFIRM = "loud_confirm"
    HARD_REFUSE = "hard_refuse"


# Thresholds. Tunable in one place — these are guesses to refine after
# real orders go through.
LOUD_SPREAD_MULTIPLE = 2
"""Loud-confirm fires if price is >max(spread*N, LOUD_MIN_OFFSET) past best."""

LOUD_MIN_OFFSET_CENTS = 10
"""Floor: even a 1¢-spread market requires a 10¢ gap to fire the loud guard."""

SOFT_SPREAD_MULTIPLE = 1
"""Soft warn fires if price is more than 1× spread past best."""


@dataclass
class SanityInput:
    """All the data the guard needs in one struct."""
    side: Literal["yes", "no"]
    action: Literal["buy", "sell"]
    price_cents: int
    count: int
    yes_best_bid: int | None
    yes_best_ask: int | None
    no_best_bid: int | None
    no_best_ask: int | None
    yes_top_qty: int | None = None
    """Quantity available at yes_best_ask (for partial-fill warning)."""
    no_top_qty: int | None = None


@dataclass
class SanityResult:
    """Verdict + human-readable reasons. Frontend renders the reasons."""
    verdict: Verdict
    reasons: list[str] = field(default_factory=list)
    """One reason per fired guard, in plain English."""

    # If a partial fill is likely, what fraction of the order would fill at
    # the quoted price? None when not relevant.
    fillable_at_quote: int | None = None


def _resolve_book(inp: SanityInput) -> tuple[int | None, int | None]:
    """Return (best_bid, best_ask) for the side being traded.

    Buying YES is taking the YES ask. Selling YES is hitting the YES bid.
    Symmetric for NO.
    """
    if inp.side == "yes":
        return inp.yes_best_bid, inp.yes_best_ask
    return inp.no_best_bid, inp.no_best_ask


def check_order(inp: SanityInput) -> SanityResult:
    """Run all guards and return the worst verdict + accumulated reasons."""
    reasons: list[str] = []
    worst = Verdict.OK

    # === HARD_REFUSE: bug-level inputs ===
    if inp.count <= 0:
        return SanityResult(Verdict.HARD_REFUSE, ["Count must be at least 1."])
    if inp.price_cents < 1 or inp.price_cents > 99:
        return SanityResult(Verdict.HARD_REFUSE, [
            f"Price {inp.price_cents}¢ is outside the 1–99¢ range.",
        ])

    best_bid, best_ask = _resolve_book(inp)

    # No book at all (illiquid market, market hasn't opened) — let it through.
    # The Kalshi REST call will reject if the market isn't accepting orders.
    if best_bid is None and best_ask is None:
        return SanityResult(
            Verdict.SOFT_WARN,
            ["No visible orderbook — your order may sit unfilled."],
        )

    # === Value guards: only meaningful for limit orders against a visible book ===
    spread: int | None = None
    if best_bid is not None and best_ask is not None and best_ask > best_bid:
        spread = best_ask - best_bid

    if inp.action == "buy" and best_ask is not None:
        # Paying above the ask is "crossing for size." Far above the ask is
        # almost always a fat-finger.
        excess = inp.price_cents - best_ask
        if excess > 0:
            soft_threshold = (spread or 1) * SOFT_SPREAD_MULTIPLE
            loud_threshold = max((spread or 1) * LOUD_SPREAD_MULTIPLE, LOUD_MIN_OFFSET_CENTS)
            if excess >= loud_threshold:
                reasons.append(
                    f"You are bidding {inp.price_cents}¢ but the ask is only {best_ask}¢ — "
                    f"paying {excess}¢ over market."
                )
                worst = Verdict.LOUD_CONFIRM
            elif excess > soft_threshold:
                reasons.append(
                    f"Your bid is {excess}¢ above the best ask ({best_ask}¢). "
                    f"Consider lowering."
                )
                worst = _max_verdict(worst, Verdict.SOFT_WARN)

    if inp.action == "sell" and best_bid is not None:
        excess = best_bid - inp.price_cents
        if excess > 0:
            soft_threshold = (spread or 1) * SOFT_SPREAD_MULTIPLE
            loud_threshold = max((spread or 1) * LOUD_SPREAD_MULTIPLE, LOUD_MIN_OFFSET_CENTS)
            if excess >= loud_threshold:
                reasons.append(
                    f"You are asking {inp.price_cents}¢ but the bid is {best_bid}¢ — "
                    f"selling {excess}¢ under market."
                )
                worst = Verdict.LOUD_CONFIRM
            elif excess > soft_threshold:
                reasons.append(
                    f"Your ask is {excess}¢ below the best bid ({best_bid}¢). "
                    f"Consider raising."
                )
                worst = _max_verdict(worst, Verdict.SOFT_WARN)

    # === Depth guard: warn if order eats through more than the top level ===
    if inp.action == "buy" and inp.yes_top_qty is not None and inp.side == "yes":
        if best_ask is not None and inp.price_cents >= best_ask and inp.count > inp.yes_top_qty:
            partial = inp.yes_top_qty
            reasons.append(
                f"Only {partial} contracts available at {best_ask}¢; the rest will "
                f"fill at worse prices."
            )
            worst = _max_verdict(worst, Verdict.SOFT_WARN)
    elif inp.action == "buy" and inp.no_top_qty is not None and inp.side == "no":
        if best_ask is not None and inp.price_cents >= best_ask and inp.count > inp.no_top_qty:
            partial = inp.no_top_qty
            reasons.append(
                f"Only {partial} contracts available at {best_ask}¢; the rest will "
                f"fill at worse prices."
            )
            worst = _max_verdict(worst, Verdict.SOFT_WARN)

    return SanityResult(verdict=worst, reasons=reasons)


_VERDICT_ORDER = {Verdict.OK: 0, Verdict.SOFT_WARN: 1, Verdict.LOUD_CONFIRM: 2, Verdict.HARD_REFUSE: 3}


def _max_verdict(a: Verdict, b: Verdict) -> Verdict:
    return a if _VERDICT_ORDER[a] >= _VERDICT_ORDER[b] else b
