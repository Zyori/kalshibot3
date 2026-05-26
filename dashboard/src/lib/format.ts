/**
 * Display formatters for the dashboard.
 *
 * Convention: storage and APIs use UTC + integer cents. Conversion to
 * Eastern time and dollar strings happens here, at the display boundary.
 */

const ET_TZ = 'America/New_York'

/**
 * Human label for a market's outcome side based on Kalshi's yes_sub_title.
 *
 * Kalshi gives each soccer-result market a tiny outcome label —
 * "Nigeria" / "Zimbabwe" / "Tie" — that's the only thing distinguishing
 * one row of a 3-way moneyline from another (the `market_title` is
 * literally identical across all three: "Nigeria vs Zimbabwe Winner?").
 *
 * We render it as a verb phrase so the row reads naturally:
 *   "Nigeria"  -> "Nigeria Wins"
 *   "Tie"      -> "Draw"
 *   null/empty -> "" (caller decides what to show instead)
 */
export function outcomeLabel(yes_sub_title: string | null | undefined): string {
  if (!yes_sub_title) return ''
  const t = yes_sub_title.trim()
  if (t.toLowerCase() === 'tie' || t.toLowerCase() === 'draw') return 'Draw'
  return `${t} Wins`
}

/**
 * Format a UTC ISO timestamp as Eastern time.
 *
 *   formatET("2026-05-26T21:30:00Z")        -> "May 26, 5:30 PM ET"
 *   formatET(null)                          -> ""
 *
 * Pass { dateOnly: true } for "May 26" without the time.
 */
export function formatET(
  iso: string | null | undefined,
  opts: { dateOnly?: boolean } = {},
): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (opts.dateOnly) {
    return new Intl.DateTimeFormat('en-US', {
      timeZone: ET_TZ,
      month: 'short',
      day: 'numeric',
    }).format(d)
  }
  const datePart = new Intl.DateTimeFormat('en-US', {
    timeZone: ET_TZ,
    month: 'short',
    day: 'numeric',
  }).format(d)
  const timePart = new Intl.DateTimeFormat('en-US', {
    timeZone: ET_TZ,
    hour: 'numeric',
    minute: '2-digit',
  }).format(d)
  return `${datePart}, ${timePart} ET`
}
