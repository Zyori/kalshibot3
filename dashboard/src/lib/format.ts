/**
 * Display formatters for the dashboard.
 *
 * Convention: storage and APIs use UTC + integer cents. Conversion to
 * Eastern time and dollar strings happens here, at the display boundary.
 */

/**
 * Cents → dollar string. `$1.23` for 123. Unsigned by default.
 *   formatDollars(123)             -> "$1.23"
 *   formatDollars(-45)             -> "-$0.45"
 *   formatDollars(0)               -> "$0.00"
 */
export function formatDollars(cents: number): string {
  const abs = Math.abs(cents)
  const sign = cents < 0 ? '-' : ''
  return `${sign}$${(abs / 100).toFixed(2)}`
}

/**
 * Signed cents → dollar string. Used for P&L where the sign is the point.
 *   formatSignedDollars(123)       -> "+$1.23"
 *   formatSignedDollars(-45)       -> "-$0.45"
 *   formatSignedDollars(0)         -> "$0.00"
 */
export function formatSignedDollars(cents: number): string {
  if (cents === 0) return '$0.00'
  if (cents > 0) return `+$${(cents / 100).toFixed(2)}`
  return `-$${(Math.abs(cents) / 100).toFixed(2)}`
}

/**
 * Cents → "-$X.YZ" or "—" if zero. For columns where "no fee" is more
 * legible as an em-dash than $0.00.
 */
export function formatFee(cents: number): string {
  if (cents === 0) return '—'
  return `-$${(cents / 100).toFixed(2)}`
}

const ET_TZ = 'America/New_York'

/**
 * Live-game clock label derived from ESPN's per-event status fields.
 *
 *   state='pre'        → 'Pre-game'
 *   state='in', period=1, clock='34:12'    → '34'
 *   state='in', period=1, clock='0:00.0'   → 'Half time' (HT marker)
 *   state='in', period=2, clock='67:42'    → '68'   (rounded up, soccer convention)
 *   state='post' (after FT)                → 'Final'
 *
 * ESPN's clock counts UP within each half (not the broadcast countdown).
 * Period 1 = first half (1..45 + stoppage); period 2 = second half
 * (46..90 + stoppage). We display the human match-minute, rounded up so
 * 67:42 reads '68' the same way Kalshi labels in-game prices.
 *
 * Falls back to ESPN's status_detail when it carries a clearer label
 * (e.g. 'HT', 'FT', 'AET') than we can derive ourselves.
 */
export function formatMatchClock(
  state: string | null | undefined,
  period: number | null | undefined,
  clock: string | null | undefined,
  statusDetail: string | null | undefined,
  kickoffIso: string | null | undefined = null,
): string | null {
  if (!state) return null
  if (state === 'pre') {
    // 'Pre-game' should only apply right around kickoff (Kalshi opened
    // trading, ESPN hasn't ticked the clock yet). Earlier than that —
    // hours or days out — return null so the caller falls back to the
    // kickoff time, which is what the user actually wants to see.
    return isPreGameWindow(kickoffIso) ? 'Pre-game' : null
  }
  if (state === 'post') return 'Final'
  if (state !== 'in') return null

  // Halftime: ESPN sets shortDetail to 'HT' (or sometimes 'Halftime').
  const detail = (statusDetail ?? '').trim().toLowerCase()
  if (detail === 'ht' || detail === 'halftime') return 'Half time'

  // Use the period base + minutes-in-half to get the human match minute.
  const halfBase = period === 2 ? 45 : period === 3 ? 90 : period === 4 ? 105 : 0
  if (period === 5) return 'Penalties'
  const minutes = parseClockMinutes(clock)
  if (minutes === null) return statusDetail ? statusDetail : null
  const total = halfBase + Math.ceil(minutes)
  return `${total}'`
}

/**
 * 'Pre-game' window: the ~10 minutes before scheduled kickoff. After
 * kickoff time has passed but ESPN's state is still 'pre' (clock hasn't
 * started), we also call it pre-game — that's the actual "game is about
 * to start" feel. Anything earlier than 10 min out gets the kickoff time
 * instead.
 */
function isPreGameWindow(kickoffIso: string | null | undefined): boolean {
  if (!kickoffIso) return false
  const kickoff = new Date(kickoffIso).getTime()
  if (Number.isNaN(kickoff)) return false
  const now = Date.now()
  const minsToKickoff = (kickoff - now) / 60_000
  // -30 means kickoff was 30 min ago but ESPN hasn't ticked the clock yet —
  // unusual but possible (delayed kickoff, ESPN lag). Cap loosely.
  return minsToKickoff <= 10 && minsToKickoff >= -30
}

function parseClockMinutes(clock: string | null | undefined): number | null {
  if (!clock) return null
  // Common ESPN formats: '34:12', '45+2:00', '0:00.0'
  const trimmed = clock.trim()
  // Strip a trailing fractional-second segment like '.0'.
  const noFrac = trimmed.replace(/\.\d+$/, '')
  // '45+2:00' → take base + stoppage minutes.
  const plusMatch = noFrac.match(/^(\d+)\+(\d+):(\d+)$/)
  if (plusMatch) {
    return parseInt(plusMatch[1], 10) + parseInt(plusMatch[2], 10)
  }
  // '34:12' → minutes + seconds/60.
  const m = noFrac.match(/^(\d+):(\d+)$/)
  if (m) return parseInt(m[1], 10) + parseInt(m[2], 10) / 60
  return null
}

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
