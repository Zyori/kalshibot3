# Order-execution bugs — investigation findings (2026-06-01)

User-reported during a trading session. Investigated, root-caused, and FIXED
(commit pending). Severity is money-path weighted.

**Resolution status:**
- #1 Overpaying — RESOLVED (not a bug; real Kalshi fee, confirmed against fill
  record bet 22: 15@31¢, 22¢ fee = 1.47¢/contract → 32.4¢ all-in). Display now
  labels it "all-in" with a fill+fee tooltip so it doesn't read as a bad fill.
- #2 Post-only error — FIXED. Was a 502 with raw Kalshi text; now a clean 422
  ("would cross the spread, post-only is maker-only…"). It was working as
  intended (refusing a crossing order) — just surfaced as a scary error.
- #3 DUMP oversell — FIXED. Added a "DUMP all N" button (exact held count);
  manual sell already gated by canSell. Ghost-share guard remains the backstop.
- #4 CANCEL "sometimes" — FIXED. Now cancels any soccer resting order (resolved
  from live WS book, not BET row) so kalshi.com-placed orders cancel too; added
  onError so a refused cancel is visible instead of silent.
- #5 Edit resting orders — still deferred (verify Kalshi amend wiring first).

Key insight from the fill data: every fee paid was on a TAKER fill; every MAKER
(post-only-style) fill paid ZERO. #1 and #2 are the same story — taker fees are
being paid because the zero-fee maker path (post-only) was unusable. With #2
fixed, resting maker bids are now usable.

---

ORIGINAL INVESTIGATION (pre-fix) below.

---

---

## #1 "Overpaying on bids" — NOT A BUG (display is misleading)

**Report:** Placed a manual limit order at 31¢, average came back 32.4¢.

**Finding: the order filled at exactly 31¢. The 32.4¢ is the fee-inclusive
average entry, and that display is intentional.**

Trace:
- Frontend sends a manual limit at the typed price: `effectivePrice = price`
  (OrderPanel.tsx:153; `price_override` is only set by the quick buttons).
- Backend builds a GTC limit at exactly that price (`orders.py:188-189`,
  `PlaceOrderRequest.type` defaults `"limit"`, `expiration_ts=None`). A limit
  order **cannot** fill above its price at Kalshi.
- `positions.py` deliberately shows fee-inclusive avg entry to match kalshi.com:
  `round((cost_basis_cents + fees_paid_cents) / quantity, 2)`.
- Math: Kalshi's trading fee at 31¢ ≈ `0.07 × 0.31 × 0.69 × 100` ≈ **1.5¢/contract**.
  `(31 + 1.5)` ≈ **32.5¢** — matches the observed 32.4¢ (rounding/contract count).

**So:** not a money leak. You paid 31¢/contract + ~1.5¢ Kalshi fee, and the UI
blends them into one "average" that reads like a bad fill.

**Disposition: UX fix, not a money fix.** Show fill price and fee separately, or
label the blended number "incl. fees," so a fee never looks like an overpay.
Low severity now that it's understood, but it actively made the user distrust
their own fills — worth fixing the presentation.

---

## #2 Post-only checkbox errors when checked — NEEDS ONE MORE PROBE

**Report:** Checking post-only throws an error.

**Partial finding:** the backend has a typed `PostOnlyRejected` mapped from
Kalshi's error body (`rest.py:100-101`). Two possible causes, not yet
distinguished:
- **(a) Legit rejection surfacing as a scary error:** post-only's whole job is
  to refuse an order that *would cross the spread*. If the user's price crosses,
  Kalshi rejects and we raise `PostOnlyRejected` — correct behavior, bad
  presentation (looks like a failure, is actually the guard working).
- **(b) Malformed request when `post_only=true`:** a wire-format issue sending
  the flag, which would error regardless of price.

**To resolve:** check how `PostOnlyRejected` maps to the HTTP status + what the
OrderPanel renders, and whether `post_only=true` is sent correctly on the wire.
If (a): make the message read "post-only would cross — not placed" instead of a
raw error. If (b): fix the request shape.

**Severity: medium.** Post-only is the tool that would let the user rest a
maker-only bid (no fee, no crossing). It being unusable means every entry pays
the taker fee (see #1).

---

## #3 DUMP / sell-all should be EXACT held count, never more — CONFIRMED

**Report:** Sell-all (DUMP) should sell exactly the held quantity and not let
the user try to sell more than they hold.

**Finding:** the backend ghost-share guard already refuses overselling
(`orders.py:157-169`: `held < body.count` → 400). So overselling can't actually
execute — this is a **UI guardrail gap**, not a money hole: the DUMP button
should pre-fill exactly the held count and the count field shouldn't allow
exceeding it. Same family as the Stage-Sell count bug
(`2026-06-01-stage-sell-count-prefill.md`) — both are "the sell UI doesn't
clamp to held quantity."

**Disposition:** clamp the DUMP/sell count to held quantity at the UI; pre-fill
the exact held number. Likely a small OrderPanel change. **Fix alongside the
Stage-Sell count bug — same root area.**

---

## #4 Resting-order CANCEL doesn't work (sometimes) — CONFIRMED root cause

**Report:** the Cancel button on a resting order sometimes does nothing.

**Finding — two compounding causes:**

1. **Source mismatch.** The resting-orders list is driven by the WS
   `user_order` stream (OpenOrdersCard.tsx:8), so it shows every resting order
   Kalshi reports. But the backend cancel requires a **BET row** with a matching
   `kalshi_order_id` (`orders.py:309-314`) — its cross-market-isolation guard.
   Any resting order WITHOUT a local BET row (placed on kalshi.com, placed
   before this app recorded it, or a placement whose BET write failed/raced)
   → backend returns **404 "no such order in this ledger."** The button is shown
   for orders the backend will refuse to cancel. This is the "sometimes."

2. **Silent failure.** The cancel mutation (OpenOrdersCard.tsx:14-32) has
   **only `onSuccess`, no `onError`.** A 404 or 502 throws into the void — no
   toast, no inline error, button just re-enables. So when cause #1 fires, the
   user sees "nothing happened."

**Disposition (two parts):**
- **Always** add an `onError` to the cancel mutation so a failed cancel is
  visible (surfaces the 404/502). This alone turns "silently broken" into
  "tells you why."
- Decide the policy for cancelling a resting order with no BET row: either
  (a) allow cancel of any *soccer* resting order Kalshi reports (verify the
  ticker is soccer on the order object, not via BET row) — more permissive but
  still cross-market-safe; or (b) keep the BET-row requirement and just make the
  refusal visible. (a) is probably what the user wants ("let me cancel my
  resting orders"), but it changes the isolation model — needs a deliberate call.

**Severity: high.** Not being able to pull a resting order is a real money risk
if the market moves against a stuck quote.

---

## #5 (also reported) Easier to edit resting orders — UX, deferred

**Report:** would like to edit a resting order more easily (currently
cancel + re-place).

Kalshi REST has an `amend`/`amend_order` (the rest.py header mentions "place,
cancel, amend" porting from V1). If `amend` is wired, an inline edit-price/size
on the resting-order row is feasible. **Verify amend is actually implemented
before scoping.** Lower priority than #1–#4.

---

## Suggested fix order (when we work this)

1. **#4 onError** (tiny, makes the worst bug visible immediately) + decide the
   cancel-policy question.
2. **#3 DUMP clamp** + the Stage-Sell count bug together (same UI area).
3. **#2 post-only** — probe (a) vs (b), then fix message or request shape.
4. **#1 fee display** — separate fee from fill price in the UI.
5. **#5 amend** — verify amend exists, then scope inline edit. Defer.

(Out of scope here, separately requested: World Cup futures tab, total-goals
markets, `time_decay` strategy enum — market-coverage + taxonomy, not bugs.)
