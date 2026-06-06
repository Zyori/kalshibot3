// Per-sport visual identity + badge-sport resolution. Data and helpers live
// here (not in SportBadge.tsx) so the component file only exports a component
// (react-refresh/only-export-components).

export type SportMeta = {
  label: string
  icon: string
  color: string // tailwind text color class
}

export const SPORTS: Record<string, SportMeta> = {
  soccer: { label: 'Soccer', icon: '⚽', color: 'text-emerald-400' },
  nfl: { label: 'NFL', icon: '🏈', color: 'text-amber-500' },
  nba: { label: 'NBA', icon: '🏀', color: 'text-orange-400' },
  nhl: { label: 'NHL', icon: '🏒', color: 'text-sky-400' },
  mlb: { label: 'MLB', icon: '⚾', color: 'text-amber-300' },
  ufc: { label: 'UFC', icon: '🥊', color: 'text-red-400' },
  // Mixed-sport parlay (legs span more than one sport). A same-sport parlay
  // badges as its sport via badgeSport(); this is the fallback.
  combo: { label: 'Parlay', icon: '🎟️', color: 'text-text-muted' },
}

export const KNOWN_SPORTS = Object.keys(SPORTS)

/**
 * The sport to badge for a bet/position. A combo carries sport='combo' (its own
 * category, kept out of sport stats) but a same-sport parlay also carries
 * leg_sport ('soccer' for an all-World-Cup parlay) — prefer that so the badge
 * shows the real sport instead of the generic parlay icon. Falls back to the
 * primary sport for singles and mixed parlays.
 */
export function badgeSport(sport: string, legSport?: string | null): string {
  if (sport === 'combo' && legSport) return legSport
  return sport
}
