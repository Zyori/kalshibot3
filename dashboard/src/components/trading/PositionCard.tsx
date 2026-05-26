import { useQuery } from '@tanstack/react-query'

type Position = {
  ticker: string
  side: 'yes' | 'no'
  quantity: number
  avg_entry_price_cents: number | null
  current_price_cents: number | null
  unrealized_pnl_cents: number | null
}

type PositionsResponse = { positions: Position[] }

/**
 * Current open position for this ticker, if any. Polls /api/positions
 * every 10s — position_sync runs every 60s server-side plus on every fill,
 * so a 10s client poll is the right granularity (live enough, not chatty).
 */
export default function PositionCard({ ticker }: { ticker: string }) {
  const { data } = useQuery<PositionsResponse>({
    queryKey: ['positions'],
    queryFn: async () => {
      const res = await fetch('/api/positions')
      if (!res.ok) throw new Error(`/api/positions: ${res.status}`)
      return res.json()
    },
    refetchInterval: 10_000,
  })

  const positions = (data?.positions ?? []).filter((p) => p.ticker === ticker)

  if (positions.length === 0) {
    return (
      <div className="rounded-lg border border-border bg-bg-card p-4 text-xs text-text-muted">
        No position on this market.
      </div>
    )
  }

  return (
    <div className="rounded-lg border border-border bg-bg-card p-4">
      <h3 className="mb-3 text-sm font-semibold text-text">Position</h3>
      <ul className="space-y-2">
        {positions.map((p) => {
          const pnl = p.unrealized_pnl_cents
          const pnlTone =
            pnl === null ? 'text-text-muted' : pnl >= 0 ? 'text-gain' : 'text-loss'
          return (
            <li
              key={`${p.ticker}:${p.side}`}
              className="grid items-center gap-2 rounded-md border border-border bg-bg p-2 text-xs"
              style={{ gridTemplateColumns: '1fr auto auto' }}
            >
              <div>
                <div className="font-mono text-text">
                  {p.side.toUpperCase()} {p.quantity} @ avg{' '}
                  {p.avg_entry_price_cents ?? '—'}¢
                </div>
                <div className="text-[10px] text-text-muted">
                  Mark {p.current_price_cents ?? '—'}¢
                </div>
              </div>
              <div className={`text-right font-mono tabular-nums text-sm ${pnlTone}`}>
                {pnl === null
                  ? '—'
                  : `${pnl >= 0 ? '+' : ''}$${(pnl / 100).toFixed(2)}`}
              </div>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
