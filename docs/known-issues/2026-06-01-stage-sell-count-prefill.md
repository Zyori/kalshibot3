# Stage Sell does not pre-fill the contract count

**Found:** 2026-06-01, U9 partner dry run
**Status:** open — deferred
**Severity:** P2 (UX; the user can type the count by hand, and `/orders/place`
ghost-share + sanity guards still protect the sell — no money-safety risk)

## Symptom

Clicking **"Stage Sell"** on an exit suggestion card (inside the expanded
`MarketCard` for a held market) pre-fills the OrderPanel's **side** and **price**
but **not the contract count** — count stays at the panel default (1). Observed
live against a real exit card (`id=3`, AUTTUN-TIE, 1300¢ stake @ 40¢, 52 held;
expected count 32).

## What was already done

A fix was written and is committed-pending in the working tree (NOT yet
verified working):
- `MarketCard.tsx` exit `stage` handler: compute
  `count = floor(suggested_size_cents / suggested_price_cents)`, clamp to held
  quantity (`market.position?.quantity`), fall back to held if the stake math
  yields nothing.
- `SportPortal.tsx` + `MarketCard.tsx` entry deep-link: thread `stage_size`
  through the URL so the entry path can derive count the same way (no clamp —
  no held position on entry).

The fix typechecks (`tsc --noEmit` clean) and IS present in the built+served
bundle (`stage_size` and `suggested_size_cents` confirmed in
`dist/assets/index-BZJZDTxQ.js`, which `index.html` points at).

## Why it's still open

Despite the fix being deployed, the user still saw count stay at 1. Every
static link was verified correct end-to-end:
- frontend `Suggestion` type declares `suggested_size_cents` (types.ts:141)
- backend `_suggestion_to_dict` serializes it (partner.py:255)
- the exit `stage` handler computes 32 in both branches (held>0 and held==0)
- OrderPanel prefill effect sets count via `setCount` when
  `prefill.count !== undefined` (OrderPanel.tsx:130), and the Count NumberField
  is bound to that state (OrderPanel.tsx:298)
- price DOES change on click, which proves the prefill effect fires

The two unverified hypotheses (could not test — no eyes on the user's browser):
1. **Stale cached bundle** — browser may still be running the prior bundle
   (`index-Cx6sPuxC.js`), whose original `stage` handler omits count entirely.
   This explains the symptom perfectly. Most likely cause.
2. New bundle loaded but `prefill.count` arrives `undefined` at runtime (would
   mean the computed value is NaN/undefined despite the static analysis), or a
   later render stomps `count` back to 1.

## How to resume

1. Confirm which bundle hash the browser actually loads (devtools Network,
   "Disable cache", hard-reload → is it `BZJZDTxQ` or older?). If older →
   cache; the fix likely already works.
2. If the new bundle is loaded and count still 1: add a temporary
   `console.log` in the exit `stage` handler printing `held`, `fromStake`,
   `count`, click Stage Sell, read the console. That isolates handler-computes-
   wrong vs effect-doesn't-apply vs later-stomp in one shot.

## Files

- `dashboard/src/components/event/MarketCard.tsx` (exit `stage` handler + entry
  URL effect)
- `dashboard/src/pages/SportPortal.tsx` (entry `stage` — adds `stage_size`)
- `dashboard/src/components/trading/OrderPanel.tsx` (prefill effect, Count field)
