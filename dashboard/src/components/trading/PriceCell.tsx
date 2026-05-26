import { useEffect, useRef, useState } from 'react'

/**
 * Flash-on-change numeric cell. Briefly tints green on increase, red on
 * decrease, then fades back to the muted base tone over 800ms.
 *
 * "Briefly" is critical — a flash that lingers makes a steady-state book
 * look perpetually red/green. 800ms is long enough to catch out of the
 * corner of an eye, short enough not to dominate.
 */
export default function PriceCell({
  value,
  className = '',
}: {
  value: number | null
  className?: string
}) {
  const prev = useRef<number | null>(value)
  const [flash, setFlash] = useState<'gain' | 'loss' | null>(null)

  useEffect(() => {
    if (value === null || prev.current === null) {
      prev.current = value
      return
    }
    if (value > prev.current) setFlash('gain')
    else if (value < prev.current) setFlash('loss')
    prev.current = value

    const t = setTimeout(() => setFlash(null), 800)
    return () => clearTimeout(t)
  }, [value])

  const flashClass =
    flash === 'gain' ? 'text-gain' : flash === 'loss' ? 'text-loss' : 'text-text'

  return (
    <span
      className={`font-mono tabular-nums transition-colors duration-500 ${flashClass} ${className}`}
    >
      {value === null ? '—' : `${value}¢`}
    </span>
  )
}
