/**
 * Bet-sizing in "units". One unit is a fixed dollar amount the user thinks in
 * (default $25); Kalshi only wants a contract count, so the unit buttons do
 * the contracts = unit$ / price math. Stored in localStorage — single-user,
 * single-device app, no backend persistence needed.
 */

const STORAGE_KEY = 'lutz.unit_size_cents'
const DEFAULT_UNIT_CENTS = 2500 // $25

/** Current unit size in cents. Falls back to the default on missing/garbage. */
export function getUnitSizeCents(): number {
  const raw = localStorage.getItem(STORAGE_KEY)
  if (raw === null) return DEFAULT_UNIT_CENTS
  const n = Number(raw)
  return Number.isFinite(n) && n > 0 ? Math.round(n) : DEFAULT_UNIT_CENTS
}

export function setUnitSizeCents(cents: number): void {
  if (Number.isFinite(cents) && cents > 0) {
    localStorage.setItem(STORAGE_KEY, String(Math.round(cents)))
  }
}

/**
 * Contracts for `units` units at `priceCents` per contract, rounded to the
 * nearest whole contract (min 1). Returns null when the price is unusable so
 * callers can disable the buttons rather than size against a bad number.
 *
 *   contractsForUnits(1, 57)   // $25 / 57¢ -> 43.86 -> 44
 *   contractsForUnits(0.5, 57) // $12.50 / 57¢ -> 21.9 -> 22
 */
export function contractsForUnits(units: number, priceCents: number | null): number | null {
  if (priceCents === null || priceCents < 1) return null
  const budgetCents = getUnitSizeCents() * units
  const contracts = Math.round(budgetCents / priceCents)
  return Math.max(1, contracts)
}
