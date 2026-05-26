import { useQuery } from '@tanstack/react-query'
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

type Trade = {
  trade_id: string
  ts: string
  yes_price: number
  count: number
  taker_side: 'yes' | 'no'
}

type TradesResponse = {
  ticker: string
  trades: Trade[]
}

/**
 * Recent trade history for one market. Y-axis is YES price in cents (always
 * 1–99 range for Kalshi binary contracts); X-axis is trade index since the
 * trade timestamps are irregular and a time-based axis would compress
 * recent activity awkwardly.
 *
 * Trades reload every 30s from REST. Real-time fill data on this same
 * market would flow through the WS (chunk 12 connects them) but for now
 * the polled snapshot is fine for the chart.
 */
export default function PriceHistoryChart({ ticker }: { ticker: string }) {
  const { data, isPending, isError } = useQuery<TradesResponse>({
    queryKey: ['trades', ticker],
    queryFn: async () => {
      const res = await fetch(`/api/markets/${encodeURIComponent(ticker)}/trades?limit=500`)
      if (!res.ok) throw new Error(`trades: ${res.status}`)
      return res.json()
    },
    refetchInterval: 30_000,
  })

  return (
    <div className="rounded-lg border border-border bg-bg-card p-4">
      <div className="mb-2 flex items-baseline justify-between">
        <h3 className="text-sm font-semibold text-text">YES price history</h3>
        <span className="text-xs text-text-muted">
          {data ? `${data.trades.length} trades` : ''}
        </span>
      </div>
      <div className="h-48">
        {isPending && (
          <div className="flex h-full items-center justify-center text-xs text-text-muted">
            Loading…
          </div>
        )}
        {isError && (
          <div className="flex h-full items-center justify-center text-xs text-loss">
            Couldn't load trades.
          </div>
        )}
        {data && data.trades.length === 0 && (
          <div className="flex h-full items-center justify-center text-xs text-text-muted">
            No trades on this market yet.
          </div>
        )}
        {data && data.trades.length > 0 && (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart
              data={data.trades.map((t, i) => ({ idx: i, price: t.yes_price, ts: t.ts }))}
              margin={{ top: 8, right: 12, left: -8, bottom: 8 }}
            >
              <CartesianGrid stroke="#2a2d37" vertical={false} />
              <XAxis dataKey="idx" stroke="#71717a" tick={{ fontSize: 10 }} />
              <YAxis
                stroke="#71717a"
                tick={{ fontSize: 10 }}
                domain={[1, 99]}
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
                labelFormatter={(idx) => {
                  const t = data.trades[idx as number]
                  return t ? new Date(t.ts).toLocaleString() : ''
                }}
                formatter={(value) => [`${value}¢`, 'YES price']}
              />
              <Line
                type="monotone"
                dataKey="price"
                stroke="#22c55e"
                strokeWidth={2}
                dot={false}
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  )
}
