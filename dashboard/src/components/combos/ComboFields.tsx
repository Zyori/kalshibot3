/** Small shared form primitives for the combo pages. */

export function Field({
  label,
  children,
}: {
  label: string
  children: React.ReactNode
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs uppercase tracking-wide text-text-muted">
        {label}
      </span>
      {children}
    </label>
  )
}

export function Segmented({
  options,
  value,
  onChange,
}: {
  options: readonly string[]
  value: string
  onChange: (v: string) => void
}) {
  return (
    <div className="flex gap-1">
      {options.map((opt) => (
        <button
          key={opt}
          type="button"
          onClick={() => onChange(opt)}
          className={`flex-1 rounded border px-2 py-1.5 text-xs ${
            value === opt
              ? 'border-action bg-action/10 text-text'
              : 'border-border text-text-muted hover:bg-bg-hover'
          }`}
        >
          {opt.replace('_', ' ')}
        </button>
      ))}
    </div>
  )
}

export const COMBO_STRATEGIES = ['lock_parlay', 'moon_parlay'] as const
export const SIDES = ['yes', 'no'] as const

/**
 * Pull a human-readable message out of a FastAPI `detail`, which may be a
 * string, a {reasons:[...]} object, a {error:"..."} object, or a 422 validation
 * array [{msg,...}]. Never returns "[object Object]".
 */
export function errorMessage(detail: unknown, fallback: string): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const first = detail[0]
    if (first && typeof first.msg === 'string') return first.msg
    return fallback
  }
  if (detail && typeof detail === 'object') {
    const d = detail as Record<string, unknown>
    const reason = Array.isArray(d.reasons) ? d.reasons[0] : undefined
    if (typeof reason === 'string') return reason
    if (typeof d.error === 'string') return d.error
  }
  return fallback
}
