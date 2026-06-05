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
