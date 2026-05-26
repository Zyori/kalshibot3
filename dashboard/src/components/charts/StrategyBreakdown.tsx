import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

type Stats = {
  by_strategy: Array<{
    strategy: string
    count: number
    pnl_cents: number
    stake_cents: number
    roi: number | null
  }>
}

/**
 * ROI per strategy as a bar chart. Bars colored green when ROI > 0,
 * red when < 0. Strategies with no settled stake (ROI = null) are
 * dropped — you can't chart an unknown.
 */
export default function StrategyBreakdown({ stats }: { stats: Stats | undefined }) {
  const data = (stats?.by_strategy ?? [])
    .filter((row) => row.roi !== null && row.stake_cents > 0)
    .map((row) => ({
      strategy: row.strategy,
      roi_pct: (row.roi as number) * 100,
      count: row.count,
    }))
    .sort((a, b) => b.roi_pct - a.roi_pct)

  return (
    <div className="rounded-lg border border-border bg-bg-card p-4">
      <h3 className="mb-3 text-sm font-semibold text-text">ROI by strategy</h3>
      {data.length === 0 ? (
        <div className="flex h-48 items-center justify-center text-xs text-text-muted">
          No settled bets yet.
        </div>
      ) : (
        <div className="h-48">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} margin={{ top: 4, right: 12, bottom: 4, left: -12 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#2a2d37" />
              <XAxis dataKey="strategy" stroke="#6b7280" fontSize={10} interval={0} />
              <YAxis
                tickFormatter={(v: number) => `${v.toFixed(0)}%`}
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
                formatter={(v: number, _name, item) => [
                  `${v.toFixed(1)}% (n=${(item.payload as { count: number }).count})`,
                  'ROI',
                ]}
              />
              <Bar dataKey="roi_pct">
                {data.map((d) => (
                  <Cell
                    key={d.strategy}
                    fill={d.roi_pct >= 0 ? '#22c55e' : '#ef4444'}
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  )
}
