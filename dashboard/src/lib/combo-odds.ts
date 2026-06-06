/**
 * Client-side combo odds ESTIMATE.
 *
 * A Kalshi market's YES price in cents ≈ the implied probability of that
 * outcome (a 45¢ YES ≈ 45% to hit). A parlay hits only if every leg hits, so
 * its combined probability is the product of the legs — and the combo's fair
 * price ≈ that product, in cents.
 *
 * This is an ESTIMATE. The real Kalshi combo price (correlation, their margin,
 * thin liquidity) only exists once the combo is materialized on Stage. The UI
 * labels it as such and replaces it with the staged price.
 */

/** Estimated combo price in cents from the per-leg YES prices (cents). Returns
 *  null if any leg price is missing (can't estimate without all legs priced).
 *  Floored to ≥1 — a combo is never free. */
export function estimateComboPriceCents(legPricesCents: (number | null)[]): number | null {
  if (legPricesCents.length === 0) return null
  let product = 1
  for (const p of legPricesCents) {
    if (p === null || p <= 0) return null // unpriced leg → no estimate
    product *= p / 100
  }
  return Math.max(1, Math.round(product * 100))
}
