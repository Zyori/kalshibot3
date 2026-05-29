import type { BookSide } from '../../contexts/WebSocketProvider'

/**
 * Top 5 levels of one side of the book. Sorted highest price first — the
 * highest YES bid is where someone is paying the most for YES; the highest
 * NO bid is where someone is paying the most for NO. The contract that
 * fills "now" is at the BOTTOM of the opposite side.
 *
 * Background bar reflects depth — wider bar = more contracts at that level.
 */
export default function DepthLadder({
  title,
  side,
  rows = 5,
}: {
  title: string
  side: BookSide
  rows?: number
}) {
  // Stored quantities are exact fractional sums of Kalshi's fixed-point deltas
  // (see WebSocketProvider orderbook_delta). Round to whole contracts on read.
  const levels = Object.entries(side)
    .map(([p, q]) => ({ price: Number(p), qty: Math.round(q) }))
    .sort((a, b) => b.price - a.price)
    .slice(0, rows)

  const maxQty = levels.reduce((m, l) => Math.max(m, l.qty), 0)

  return (
    <div className="rounded-lg border border-border bg-bg-card p-4">
      <div className="mb-2 flex items-baseline justify-between">
        <h3 className="text-sm font-semibold text-text">{title}</h3>
        <span className="text-xs text-text-muted">{Object.keys(side).length} levels</span>
      </div>
      {levels.length === 0 ? (
        <p className="text-xs text-text-muted">No resting liquidity.</p>
      ) : (
        <ul className="space-y-1">
          {levels.map((l) => (
            <li
              key={l.price}
              className="relative flex justify-between font-mono text-xs tabular-nums"
            >
              <span
                aria-hidden
                className="absolute inset-y-0 left-0 -z-0 rounded-sm bg-bg-hover"
                style={{ width: `${(l.qty / maxQty) * 100}%` }}
              />
              <span className="relative z-10 text-text">{l.price}¢</span>
              <span className="relative z-10 text-text-muted">
                {l.qty.toLocaleString()}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
