import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import InlineError from '../components/InlineError'
import PnLChart from '../components/charts/PnLChart'
import StrategyBreakdown from '../components/charts/StrategyBreakdown'
import { formatET } from '../lib/format'

type Bet = {
  id: number
  sport: string
  ticker: string | null
  side: 'yes' | 'no'
  entry_price_cents: number
  exit_price_cents: number | null
  quantity: number
  stake_cents: number
  pnl_cents: number | null
  status: 'open' | 'won' | 'lost' | 'cancelled'
  exit_type: string | null
  source: string
  strategy: string
  confidence: string
  timing: string
  human_reasoning: string | null
  ai_reasoning: string | null
  placed_at: string | null
  settled_at: string | null
}

type LedgerResponse = { bets: Bet[]; next_cursor: number | null }

type Stats = {
  total_bets: number
  by_status: Record<string, number>
  total_pnl_cents: number
  total_stake_cents: number
  win_rate: number | null
  roi: number | null
  by_strategy: Array<{
    strategy: string
    count: number
    pnl_cents: number
    stake_cents: number
    roi: number | null
  }>
}

const STATUS_OPTIONS = ['open', 'won', 'lost', 'cancelled'] as const
const SOURCE_OPTIONS = ['human', 'ai', 'collaborative', 'external'] as const
const STRATEGY_OPTIONS = [
  'mean_reversion',
  'mean_confirmation',
  'lock_parlay',
  'underdog',
  'moon_parlay',
  'draw_value',
  'live_event',
  'manual',
] as const
const TIMING_OPTIONS = ['pre_match', 'live', 'futures'] as const

type Filters = {
  status: string[]
  source: string[]
  strategy: string[]
  timing: string[]
}

export default function Ledger() {
  const [filters, setFilters] = useState<Filters>({
    status: [],
    source: [],
    strategy: [],
    timing: [],
  })
  const [expandedId, setExpandedId] = useState<number | null>(null)

  const queryString = useMemo(() => {
    const params = new URLSearchParams()
    for (const v of filters.status) params.append('status', v)
    for (const v of filters.source) params.append('source', v)
    for (const v of filters.strategy) params.append('strategy', v)
    for (const v of filters.timing) params.append('timing', v)
    params.set('limit', '200')
    return params.toString()
  }, [filters])

  const bets = useQuery<LedgerResponse>({
    queryKey: ['ledger', queryString],
    queryFn: async () => {
      const res = await fetch(`/api/ledger?${queryString}`)
      if (!res.ok) throw new Error(`/api/ledger: ${res.status}`)
      return res.json()
    },
    refetchInterval: 30_000,
  })

  const stats = useQuery<Stats>({
    queryKey: ['ledger_stats', queryString],
    queryFn: async () => {
      const res = await fetch(`/api/ledger/stats?${queryString}`)
      if (!res.ok) throw new Error(`/api/ledger/stats: ${res.status}`)
      return res.json()
    },
    refetchInterval: 30_000,
  })

  const toggle = (dim: keyof Filters, v: string) => {
    setFilters((f) => ({
      ...f,
      [dim]: f[dim].includes(v) ? f[dim].filter((x) => x !== v) : [...f[dim], v],
    }))
  }

  return (
    <div className="space-y-6">
      <header>
        <h2 className="text-lg font-semibold text-text">Ledger</h2>
        <p className="mt-1 text-sm text-text-muted">
          Every bet, tagged. Filter by any combination of dimensions.
        </p>
      </header>

      {stats.isError && (
        <InlineError message="Couldn't load stats." detail={stats.error} />
      )}
      <StatsStrip stats={stats.data} />

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <PnLChart bets={bets.data?.bets ?? []} />
        </div>
        <StrategyBreakdown stats={stats.data} />
      </div>

      <FilterBar
        title="Status"
        options={STATUS_OPTIONS as readonly string[]}
        selected={filters.status}
        onToggle={(v) => toggle('status', v)}
      />
      <FilterBar
        title="Strategy"
        options={STRATEGY_OPTIONS as readonly string[]}
        selected={filters.strategy}
        onToggle={(v) => toggle('strategy', v)}
      />
      <FilterBar
        title="Source"
        options={SOURCE_OPTIONS as readonly string[]}
        selected={filters.source}
        onToggle={(v) => toggle('source', v)}
      />
      <FilterBar
        title="Timing"
        options={TIMING_OPTIONS as readonly string[]}
        selected={filters.timing}
        onToggle={(v) => toggle('timing', v)}
      />

      {bets.isError ? (
        <InlineError message="Couldn't load bets." detail={bets.error} />
      ) : (
        <BetsTable
          bets={bets.data?.bets ?? []}
          isLoading={bets.isPending}
          expandedId={expandedId}
          onToggleExpand={(id) => setExpandedId((cur) => (cur === id ? null : id))}
        />
      )}
    </div>
  )
}

function StatsStrip({ stats }: { stats: Stats | undefined }) {
  const items: Array<{ label: string; value: string; tone?: 'gain' | 'loss' }> = []
  if (stats) {
    const pnl = stats.total_pnl_cents
    items.push({
      label: 'Total P&L',
      value: pnl === 0 ? '$0.00' : `${pnl >= 0 ? '+' : ''}$${(pnl / 100).toFixed(2)}`,
      tone: pnl > 0 ? 'gain' : pnl < 0 ? 'loss' : undefined,
    })
    items.push({ label: 'Bets', value: String(stats.total_bets) })
    items.push({
      label: 'Win rate',
      value: stats.win_rate === null ? '—' : `${(stats.win_rate * 100).toFixed(0)}%`,
    })
    items.push({
      label: 'ROI',
      value: stats.roi === null ? '—' : `${(stats.roi * 100).toFixed(1)}%`,
      tone: stats.roi === null ? undefined : stats.roi > 0 ? 'gain' : 'loss',
    })
  }
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      {items.length > 0
        ? items.map((it) => (
            <div key={it.label} className="rounded-lg border border-border bg-bg-card p-3">
              <div className="text-xs text-text-muted">{it.label}</div>
              <div
                className={`mt-1 font-mono text-xl tabular-nums ${
                  it.tone === 'gain'
                    ? 'text-gain'
                    : it.tone === 'loss'
                    ? 'text-loss'
                    : 'text-text'
                }`}
              >
                {it.value}
              </div>
            </div>
          ))
        : Array.from({ length: 4 }).map((_, i) => (
            <div
              key={i}
              className="h-[68px] animate-pulse rounded-lg border border-border bg-bg-card"
            />
          ))}
    </div>
  )
}

function FilterBar({
  title,
  options,
  selected,
  onToggle,
}: {
  title: string
  options: readonly string[]
  selected: string[]
  onToggle: (v: string) => void
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-xs uppercase tracking-wide text-text-muted">{title}</span>
      {options.map((opt) => {
        const active = selected.includes(opt)
        return (
          <button
            key={opt}
            type="button"
            onClick={() => onToggle(opt)}
            className={`rounded-full border px-3 py-1 text-xs ${
              active
                ? 'border-action bg-action/10 text-action'
                : 'border-border bg-bg-card text-text-muted hover:bg-bg-hover hover:text-text'
            }`}
          >
            {opt}
          </button>
        )
      })}
    </div>
  )
}

function BetsTable({
  bets,
  isLoading,
  expandedId,
  onToggleExpand,
}: {
  bets: Bet[]
  isLoading: boolean
  expandedId: number | null
  onToggleExpand: (id: number) => void
}) {
  if (isLoading) {
    return (
      <div className="space-y-2">
        {Array.from({ length: 6 }).map((_, i) => (
          <div
            key={i}
            className="h-12 animate-pulse rounded-md border border-border bg-bg-card"
          />
        ))}
      </div>
    )
  }
  if (bets.length === 0) {
    return (
      <div className="rounded-lg border border-border bg-bg-card p-8 text-center">
        <p className="text-sm text-text-muted">No bets match the current filters.</p>
      </div>
    )
  }
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-bg-card">
      <table className="w-full text-sm">
        <thead className="border-b border-border bg-bg text-xs uppercase tracking-wide text-text-muted">
          <tr>
            <th className="px-3 py-2 text-left">Placed</th>
            <th className="px-3 py-2 text-left">Market</th>
            <th className="px-3 py-2 text-left">Side</th>
            <th className="px-3 py-2 text-right">Qty × Entry</th>
            <th className="px-3 py-2 text-right">P&amp;L</th>
            <th className="px-3 py-2 text-left">Strategy</th>
            <th className="px-3 py-2 text-left">Status</th>
          </tr>
        </thead>
        <tbody>
          {bets.map((b) => (
            <BetRow
              key={b.id}
              bet={b}
              expanded={expandedId === b.id}
              onToggle={() => onToggleExpand(b.id)}
            />
          ))}
        </tbody>
      </table>
    </div>
  )
}

function BetRow({
  bet,
  expanded,
  onToggle,
}: {
  bet: Bet
  expanded: boolean
  onToggle: () => void
}) {
  const pnl = bet.pnl_cents
  const pnlCls =
    pnl === null ? 'text-text-muted' : pnl > 0 ? 'text-gain' : pnl < 0 ? 'text-loss' : 'text-text'
  const statusCls =
    bet.status === 'won'
      ? 'text-gain'
      : bet.status === 'lost'
      ? 'text-loss'
      : bet.status === 'cancelled'
      ? 'text-text-muted'
      : 'text-action'
  return (
    <>
      <tr
        onClick={onToggle}
        className="cursor-pointer border-b border-border last:border-b-0 hover:bg-bg-hover"
      >
        <td className="px-3 py-2 text-xs text-text-muted">{formatET(bet.placed_at)}</td>
        <td className="px-3 py-2 font-mono text-xs">{bet.ticker ?? '—'}</td>
        <td className="px-3 py-2 text-xs">{bet.side.toUpperCase()}</td>
        <td className="px-3 py-2 text-right font-mono tabular-nums text-xs">
          {bet.quantity} × {bet.entry_price_cents}¢
        </td>
        <td className={`px-3 py-2 text-right font-mono tabular-nums text-xs ${pnlCls}`}>
          {pnl === null ? '—' : `${pnl >= 0 ? '+' : ''}$${(pnl / 100).toFixed(2)}`}
        </td>
        <td className="px-3 py-2 text-xs">{bet.strategy}</td>
        <td className={`px-3 py-2 text-xs font-semibold uppercase ${statusCls}`}>
          {bet.status}
        </td>
      </tr>
      {expanded && (
        <tr className="border-b border-border bg-bg last:border-b-0">
          <td colSpan={7} className="px-3 py-3">
            <dl className="grid gap-x-6 gap-y-1 text-xs sm:grid-cols-2">
              <Pair label="Source" value={bet.source} />
              <Pair label="Confidence" value={bet.confidence} />
              <Pair label="Timing" value={bet.timing} />
              <Pair label="Exit type" value={bet.exit_type ?? '—'} />
              <Pair
                label="Exit price"
                value={bet.exit_price_cents !== null ? `${bet.exit_price_cents}¢` : '—'}
              />
              <Pair label="Settled" value={formatET(bet.settled_at) || '—'} />
              <Pair
                label="Stake"
                value={`$${(bet.stake_cents / 100).toFixed(2)}`}
              />
              {bet.human_reasoning && (
                <Pair label="Human reasoning" value={bet.human_reasoning} wide />
              )}
              {bet.ai_reasoning && (
                <Pair label="AI reasoning" value={bet.ai_reasoning} wide />
              )}
            </dl>
          </td>
        </tr>
      )}
    </>
  )
}

function Pair({ label, value, wide }: { label: string; value: string; wide?: boolean }) {
  return (
    <div className={wide ? 'sm:col-span-2' : ''}>
      <dt className="inline text-text-muted">{label}:</dt>
      <dd className="ml-1 inline text-text">{value}</dd>
    </div>
  )
}
