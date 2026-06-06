import { SPORTS } from '../../lib/sport'

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
