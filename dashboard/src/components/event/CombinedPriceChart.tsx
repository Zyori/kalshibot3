/**
 * One chart, N lines — YES price for each child market on a shared y-axis
 * (1-99¢). Matches Kalshi's event view: each outcome has its own color,
 * the favorite sits on top, the underdog at the bottom.
 *
 * Data: fetches /api/markets/{ticker}/trades for each child in parallel
 * (TanStack handles the dedupe + caching). For the x-axis we use shared
 * timestamps from the union of all series; missing points use the prior
 * value (step-after interpolation) so a market that hasn't traded recently
 * still draws a flat line at its last price.
 */
import { useMemo } from 'react'
import { useQueries } from '@tanstack/react-query'
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { formatET } from '../../lib/format'
import { outcomeLabel } from '../../lib/format'
import type { ChildMarket } from '../../lib/types'

type Trade = {
  trade_id: string
  ts: string
  yes_price: number
  count: number
  taker_side: 'yes' | 'no'
}

type TradesResponse = { ticker: string; trades: Trade[] }

// Red / green / blue — colorblind-friendly enough for protan + deutan because
// of the saturation gap; tritan users separate red from green well. If a 4th
// market ever shows up we extend with high-contrast amber/purple.
const COLORS = ['#22c55e', '#ef4444', '#3b82f6', '#f59e0b', '#a855f7', '#06b6d4']

export default function CombinedPriceChart({ markets }: { markets: ChildMarket[] }) {
  const queries = useQueries({
    queries: markets.map((m) => ({
      queryKey: ['trades', m.ticker],
      queryFn: async (): Promise<TradesResponse> => {
        const res = await fetch(`/api/markets/${encodeURIComponent(m.ticker)}/trades`)
        if (!res.ok) throw new Error(`trades: ${res.status}`)
        return res.json()
      },
      refetchInterval: 30_000,
      staleTime: 25_000,
    })),
  })

  const tradesByTicker = useMemo(() => {
    const out: Record<string, Trade[]> = {}
    queries.forEach((q, i) => {
      out[markets[i].ticker] = q.data?.trades ?? []
    })
    return out
  }, [queries, markets])

  // Build merged time series. X-axis = union of all trade timestamps,
  // y for each ticker = last-seen yes_price at-or-before that ts (step-after).
  const chartData = useMemo(() => {
    const allTs = new Set<string>()
    for (const m of markets) {
      for (const t of tradesByTicker[m.ticker] ?? []) allTs.add(t.ts)
    }
    const sortedTs = Array.from(allTs).sort()
    if (sortedTs.length === 0) return []

    // Per-ticker walking index into its (chronologically-sorted) trades.
    const sortedByTicker: Record<string, Trade[]> = {}
    const idxByTicker: Record<string, number> = {}
    const lastPriceByTicker: Record<string, number | null> = {}
    for (const m of markets) {
      const sorted = [...(tradesByTicker[m.ticker] ?? [])].sort((a, b) =>
        a.ts < b.ts ? -1 : 1,
      )
      sortedByTicker[m.ticker] = sorted
      idxByTicker[m.ticker] = 0
      lastPriceByTicker[m.ticker] = null
    }

    return sortedTs.map((ts) => {
      const row: Record<string, number | string | null> = { ts }
      for (const m of markets) {
        const sorted = sortedByTicker[m.ticker]
        let i = idxByTicker[m.ticker]
        while (i < sorted.length && sorted[i].ts <= ts) {
          lastPriceByTicker[m.ticker] = sorted[i].yes_price
          i += 1
        }
        idxByTicker[m.ticker] = i
        row[m.ticker] = lastPriceByTicker[m.ticker]
      }
      return row
    })
  }, [markets, tradesByTicker])

  const totalTrades = useMemo(
    () => Object.values(tradesByTicker).reduce((acc, ts) => acc + ts.length, 0),
    [tradesByTicker],
  )
  const isLoading = queries.some((q) => q.isPending)

  return (
    <div className="rounded-lg border border-border bg-bg-card p-4">
      <div className="mb-2 flex items-baseline justify-between">
        <h3 className="text-sm font-semibold text-text">YES price history</h3>
        <span className="text-xs text-text-muted">{totalTrades} trades</span>
      </div>
      <div className="h-64">
        {isLoading && chartData.length === 0 && (
          <div className="flex h-full items-center justify-center text-xs text-text-muted">
            Loading…
          </div>
        )}
        {!isLoading && chartData.length === 0 && (
          <div className="flex h-full items-center justify-center text-xs text-text-muted">
            No trades on this event yet.
          </div>
        )}
        {chartData.length > 0 && (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData} margin={{ top: 8, right: 12, left: -8, bottom: 8 }}>
              <CartesianGrid stroke="#2a2d37" vertical={false} />
              <XAxis
                dataKey="ts"
                stroke="#71717a"
                tick={{ fontSize: 10 }}
                tickFormatter={(ts: string) => formatET(ts, { timeOnly: true })}
              />
              <YAxis
                stroke="#71717a"
                tick={{ fontSize: 10 }}
                domain={[0, 100]}
                tickFormatter={(v) => `${v}¢`}
                width={40}
              />
              <Tooltip
                contentStyle={{
                  background: '#1a1d27',
                  border: '1px solid #2a2d37',
                  borderRadius: 6,
                  fontSize: 11,
                }}
                labelFormatter={(ts) =>
                  typeof ts === 'string' ? formatET(ts) : ''
                }
                formatter={(value, name) => {
                  if (value === null || value === undefined) return ['—', String(name)]
                  return [`${value}¢`, String(name)]
                }}
              />
              <Legend
                wrapperStyle={{ fontSize: 11 }}
                iconType="plainline"
              />
              {markets.map((m, i) => (
                <Line
                  key={m.ticker}
                  type="stepAfter"
                  dataKey={m.ticker}
                  name={outcomeLabel(m.yes_sub_title) || m.ticker}
                  stroke={COLORS[i % COLORS.length]}
                  strokeWidth={2}
                  dot={false}
                  isAnimationActive={false}
                  connectNulls
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  )
}
