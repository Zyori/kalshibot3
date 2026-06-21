# Plan: Migrate the order path to Kalshi V2 endpoints

**Date:** 2026-06-21
**Trigger:** Live `HTTP 410 deprecated_v1_order_endpoint` on every order placement.
Kalshi retired `POST /portfolio/orders` (V1). Create/cancel/amend all move to the
`/portfolio/events/orders` V2 family.

## The contract change (verified against docs.kalshi.com, 2026-06-21)

V2 is a **single YES-book** model. The decisive doc quote:
> **BookSide:** "For event markets this refers to the YES leg only: `bid` means buy YES."

### Create — `POST /portfolio/events/orders` (flat, no path param)

Request body (V2):
| field | type | required | value |
|---|---|---|---|
| `ticker` | string | ✓ | unchanged |
| `side` | `bid`\|`ask` | ✓ | **bid = buy YES, ask = sell YES** |
| `count` | fixed-point string | ✓ | `"10.00"` (0–2 dp) |
| `price` | fixed-point dollars | ✓ | `"0.5600"` — **always the YES price** |
| `time_in_force` | enum | ✓ | `good_till_canceled` (was implicit GTC) / `immediate_or_cancel` |
| `self_trade_prevention_type` | enum | ✓ | `taker_at_cross` |
| `client_order_id` | string | — | our UUID (idempotency, rule 6) |
| `post_only` | bool | — | unchanged |
| `expiration_time` | int (epoch s) | — | was `expiration_ts` |

Response (V2) — **different shape, omits ticker/side/status/price**:
`order_id`, `client_order_id`, `fill_count`, `remaining_count`,
`average_fill_price` (if filled), `average_fee_paid` (if filled), `ts_ms`.

### Cancel — `DELETE /portfolio/events/orders/{order_id}`
Body: none. Response: `order_id`, `client_order_id`, `reduced_by`, `ts_ms`.

### Amend — `POST /portfolio/events/orders/{order_id}/amend`
Body: `ticker`, `side` (bid/ask), `price` (dollars), `count` (fp string),
`client_order_id`, `updated_client_order_id`. Response: `order_id`, `ts_ms`,
optional `remaining_count`/`fill_count`/`average_fill_price`.

## The load-bearing mapping (our `yes/no`+`buy/sell` → V2 `bid/ask`+YES price)

This is the money-critical translation. Our internal model is unchanged
(`side ∈ {yes,no}`, `action ∈ {buy,sell}`, `price_cents` in the held side's
frame). Convert ONLY at the wire boundary:

| our (side, action) | V2 side | V2 price (YES frame) |
|---|---|---|
| yes, buy  | `bid` | `price_cents / 100` |
| yes, sell | `ask` | `price_cents / 100` |
| no,  buy  | `ask` | `(100 − price_cents) / 100` |
| no,  sell | `bid` | `(100 − price_cents) / 100` |

Rule: **side flips to ask when (action=buy)≠(side=yes); price flips to the YES
complement whenever side=no.** Truth table:
- buy YES → bid, yes-price.    sell YES → ask, yes-price.
- buy NO  → ask, 100−price.    sell NO  → bid, 100−price.

A unit test will assert all four rows. Inverting this places the opposite side —
the single most dangerous failure here.

## Money-rule reconciliation (CLAUDE.md rule 1)

Rule 1: integer cents everywhere, dollars exist only at the wire boundary in
`schemas.py`. V2 wants dollar strings → add the **inverse** of the existing
`dollars_str_to_cents`:

```python
# core/types.py — beside dollars_str_to_cents
def cents_to_dollars_str(cents: int) -> str:
    """Integer cents (56) → Kalshi V2 fixed-point dollar string ('0.5600').
    The single cents→dollars converter; used only at the V2 order wire boundary."""
    return f"{cents / 100:.4f}"
```
Count → fp string: `f"{count}.00"` (count is already an int; integer contracts).
No floats kept anywhere — we format a string and discard it.

## Changes (smallest blast radius: wire boundary only)

### 1. `core/types.py`
Add `cents_to_dollars_str` (above). One function, mirrors the existing one.

### 2. `kalshi/schemas.py`
- `PlaceOrderRequest`: keep our `yes/no`+`buy/sell`+`yes_price/no_price` public
  surface UNCHANGED (callers untouched). Add a method
  `to_v2_wire() -> dict` that emits the V2 body using the mapping table +
  `cents_to_dollars_str`, `time_in_force="good_till_canceled"` (or
  `immediate_or_cancel` when we add marketable orders — out of scope now),
  `self_trade_prevention_type="taker_at_cross"`. The model's price validators
  (1–99) stay as the cents-domain guard.
- `AmendOrderRequest`: same — add `to_v2_wire()`.
- `Order` (response model): the V2 create/amend response has no side/price/
  ticker/status. Rather than parse it into `Order`, `place_order`/`amend_order`
  SYNTHESIZE an `Order` from the request we already hold + the response's
  `order_id` (and `remaining_count`/`fill_count`). This preserves the `Order`
  shape every downstream consumer (`record_placed_order` reads
  `order.side/action/yes_price/no_price/order_id`; the amend route reads
  `resp.order.order_id/status`) depends on — zero churn outside rest.py.
  - New small parser for the V2 fill counts: `fill_count`/`remaining_count` are
    fp strings → int via the existing float-string pattern.
  - status: a freshly-placed limit that didn't fully fill is `resting`; if
    `remaining_count == 0` and `fill_count > 0` it's `executed`. Synthesize from
    counts. Amend always leaves a resting order → `status="resting"`.

### 3. `kalshi/rest.py`
- `place_order`: POST `/portfolio/events/orders` with `req.to_v2_wire()`; build
  the synthesized `Order` from `req` + response. Keep the existing `log.info`.
- `cancel_order`: `DELETE /portfolio/events/orders/{order_id}`.
- `amend_order`: POST `/portfolio/events/orders/{order_id}/amend` with
  `req.to_v2_wire()`; synthesize the returned `Order` (new order_id, resting).
- Post-only rejection detection (`_classify_error`) is body-text based and
  unchanged — V2 still rejects post-only the same way (verify on demo).

### 4. Tests
- `cents_to_dollars_str`: 1→"0.0100", 56→"0.5600", 99→"0.9900", 100→"1.0000".
- `PlaceOrderRequest.to_v2_wire()` — all four mapping rows assert exact
  `side` + `price`. This is the test that guards against the inversion.
- `AmendOrderRequest.to_v2_wire()` — same four rows.
- `place_order`/`amend_order` synthesize an `Order` whose side/action/price
  round-trip back to what we sent (mock the HTTP layer).
- Existing order tests must still pass (public surface unchanged).

## Out of scope (flagged, not done)
- Batch create/cancel, decrease, reduce_only, order_group_id — not used today.
- `immediate_or_cancel`/marketable orders — we only place resting limits now.
- `subaccount`/`exchange_index` — default 0, omit.

## Verification (before production)
1. `pytest` + `mypy --strict` on changed files.
2. `KALSHI_ENV=demo`: place a real YES limit + a real NO limit on demo, confirm
   each rests on the correct side in the demo UI (proves the bid/ask mapping),
   then cancel + amend each. NO order is the one to watch — its side AND price
   both flip.
3. Only after demo proves the mapping: a single small live order.

## Risk register
- **[RISK] Inverted side/price on NO orders** — mitigated by the 4-row unit
  test + the mandatory demo NO-order check. Do not ship on unit tests alone.
- **[RISK] `time_in_force` default** — V1 GTC was implicit; V2 requires it.
  Wrong TIF (e.g. IOC) would cancel a resting limit instantly. We hardcode
  `good_till_canceled` for the limit path; asserted in the wire test.
- **[RISK] Response-shape consumers** — `record_placed_order` reads fields the
  V2 response drops. Mitigated by synthesizing `Order` from the request, so no
  consumer sees the V2 shape. Traced all consumers: bet_service:299–330 (place),
  orders.py:617/627/633 (amend).
- **[ASSUMPTION] post-only error text unchanged in V2** — verify on demo;
  low-stakes (surfaces as 422, no money lost if it regresses to 502).
