// Per-sport visual identity. Icon + accent color. Add a row when a new
// sport lands in core/types.Sport.

type SportMeta = {
  label: string
  icon: string
  color: string // tailwind text color class
}

const SPORTS: Record<string, SportMeta> = {
  soccer: { label: 'Soccer', icon: '⚽', color: 'text-emerald-400' },
  nfl: { label: 'NFL', icon: '🏈', color: 'text-amber-500' },
  ufc: { label: 'UFC', icon: '🥊', color: 'text-red-400' },
}

export function SportBadge({ sport, compact = false }: { sport: string; compact?: boolean }) {
  const meta = SPORTS[sport] ?? { label: sport, icon: '•', color: 'text-text-muted' }
  if (compact) {
    return (
      <span title={meta.label} className={`${meta.color} text-sm leading-none`}>
        {meta.icon}
      </span>
    )
  }
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border border-border bg-bg-card px-2 py-0.5 text-[11px] ${meta.color}`}
    >
      <span>{meta.icon}</span>
      <span>{meta.label}</span>
    </span>
  )
}

export const KNOWN_SPORTS = Object.keys(SPORTS)
