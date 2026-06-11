import { useMemo } from 'react'
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { formatDollars, formatET } from '../../lib/format'

type Bet = {
  placed_at: string | null
  settled_at: string | null
  pnl_cents: number | null
  net_pnl_cents: number | null
}

/**
 * Cumulative net P&L over time. Each settled bet contributes its
 * net_pnl_cents (after Kalshi fees) at its settled_at; the line is the
 * running sum. Open bets contribute nothing (their final pnl is
 * unknown). Empty state shows a message instead of an empty chart frame.
 */
export default function PnLChart({ bets }: { bets: Bet[] }) {
  const data = useMemo(() => {
    const settled = bets
      .filter((b) => b.net_pnl_cents !== null && b.settled_at !== null)
      .sort((a, b) => (a.settled_at! < b.settled_at! ? -1 : 1))
    let running = 0
    return settled.map((b) => {
      running += b.net_pnl_cents!
      return { ts: b.settled_at!, cum_cents: running }
    })
  }, [bets])

  return (
    <div className="rounded-lg border border-border bg-bg-card p-4">
      <h3 className="mb-3 text-sm font-semibold text-text">Cumulative net P&amp;L</h3>
      {data.length === 0 ? (
        <div className="flex h-48 items-center justify-center text-xs text-text-muted">
          No settled bets yet.
        </div>
      ) : (
        <div className="h-48">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data} margin={{ top: 4, right: 12, bottom: 4, left: -12 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#2a2d37" />
              <XAxis
                dataKey="ts"
                tickFormatter={(t: string) => formatET(t, { dateOnly: true })}
                stroke="#6b7280"
                fontSize={10}
              />
              <YAxis
                tickFormatter={(v: number) => `$${(v / 100).toFixed(0)}`}
                stroke="#6b7280"
                fontSize={10}
              />
              <Tooltip
                contentStyle={{
                  background: '#1a1d27',
                  border: '1px solid #2a2d37',
                  borderRadius: 6,
                  fontSize: 11,
                }}
                labelFormatter={(t) => (typeof t === 'string' ? formatET(t) : '')}
                formatter={(v: number) => [formatDollars(v), 'Cumulative']}
              />
              <Line
                type="stepAfter"
                dataKey="cum_cents"
                stroke="#22c55e"
                strokeWidth={2}
                dot={false}
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  )
}
