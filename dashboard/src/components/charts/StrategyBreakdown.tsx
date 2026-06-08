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
    fees_cents: number
    net_pnl_cents: number
    roi: number | null
    net_roi: number | null
  }>
}

// Retired strategy keys still appear on historical bets; label them clearly
// so an old 'draw_value' bar doesn't read as a live strategy. See the
// draw_value -> time_decay rename.
const LEGACY_STRATEGY_LABELS: Record<string, string> = {
  draw_value: 'draw_value (legacy)',
}

type Row = { strategy: string; roi_pct: number; count: number }

/** Tooltip whose ROI value is green when positive, red when negative — matching
 * the bar colors. recharts can't color a value per-row via itemStyle (it's
 * static), so we render the content ourselves. */
function RoiTooltip({
  active,
  payload,
}: {
  active?: boolean
  payload?: Array<{ payload: Row }>
}) {
  if (!active || !payload?.length) return null
  const { strategy, roi_pct, count } = payload[0].payload
  return (
    <div className="rounded-md border border-border bg-bg-card px-2.5 py-1.5 text-[11px]">
      <div className="text-text-muted">{strategy}</div>
      <div>
        <span className={roi_pct >= 0 ? 'text-gain' : 'text-loss'}>
          {roi_pct.toFixed(1)}%
        </span>
        <span className="text-text-muted"> ROI (n={count})</span>
      </div>
    </div>
  )
}

/**
 * Net ROI per strategy as a bar chart. Net = after Kalshi fees. Bars
 * are green when net ROI > 0, red when < 0. Strategies with no settled
 * stake are dropped — you can't chart an unknown.
 */
export default function StrategyBreakdown({ stats }: { stats: Stats | undefined }) {
  const data = (stats?.by_strategy ?? [])
    .filter((row) => row.net_roi !== null && row.stake_cents > 0)
    .map((row) => ({
      strategy: LEGACY_STRATEGY_LABELS[row.strategy] ?? row.strategy,
      roi_pct: (row.net_roi as number) * 100,
      count: row.count,
    }))
    .sort((a, b) => b.roi_pct - a.roi_pct)

  return (
    <div className="rounded-lg border border-border bg-bg-card p-4">
      <h3 className="mb-3 text-sm font-semibold text-text">Net ROI by strategy</h3>
      {data.length === 0 ? (
        <div className="flex h-48 items-center justify-center text-xs text-text-muted">
          No settled bets yet.
        </div>
      ) : (
        <div className="h-64">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} margin={{ top: 4, right: 12, bottom: 4, left: -12 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#2a2d37" />
              {/* Strategy names are long and there are many of them — angle the
                  labels so they don't overlap. interval={0} keeps every bar
                  labeled; height reserves room for the rotated text. */}
              <XAxis
                dataKey="strategy"
                stroke="#6b7280"
                fontSize={10}
                interval={0}
                angle={-40}
                textAnchor="end"
                height={64}
              />
              <YAxis
                tickFormatter={(v: number) => `${v.toFixed(0)}%`}
                stroke="#6b7280"
                fontSize={10}
              />
              <Tooltip cursor={{ fill: '#22252f' }} content={<RoiTooltip />} />
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
