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
  remaining_quantity: number
  stake_cents: number
  pnl_cents: number | null
  realized_pnl_cents: number | null
  entry_fees_cents: number
  exit_fees_cents: number
  fees_cents: number
  net_pnl_cents: number | null
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
  total_fees_cents: number
  total_net_pnl_cents: number
  win_rate: number | null
  roi: number | null
  net_roi: number | null
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

type BetFill = {
  id: number
  trade_id: string
  order_id: string
  ticker: string
  side: 'yes' | 'no'
  action: 'buy' | 'sell'
  price_cents: number
  quantity_centi: number
  quantity: number
  fee_cents: number | null
  is_taker: boolean | null
  fee_synced_at: string | null
  created_time: string | null
}

type BetFillsResponse = { bet_id: number; fills: BetFill[] }

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
  type Item = {
    label: string
    value: string
    sub?: string
    tone?: 'gain' | 'loss'
  }
  const items: Item[] = []
  if (stats) {
    const net = stats.total_net_pnl_cents
    const gross = stats.total_pnl_cents
    const fees = stats.total_fees_cents
    items.push({
      label: 'Net P&L',
      value: net === 0 ? '$0.00' : `${net >= 0 ? '+' : ''}$${(net / 100).toFixed(2)}`,
      sub: `${gross >= 0 ? '+' : ''}$${(gross / 100).toFixed(2)} gross · −$${(fees / 100).toFixed(2)} fees`,
      tone: net > 0 ? 'gain' : net < 0 ? 'loss' : undefined,
    })
    items.push({ label: 'Bets', value: String(stats.total_bets) })
    items.push({
      label: 'Win rate',
      value: stats.win_rate === null ? '—' : `${(stats.win_rate * 100).toFixed(0)}%`,
    })
    items.push({
      label: 'Net ROI',
      value: stats.net_roi === null ? '—' : `${(stats.net_roi * 100).toFixed(1)}%`,
      sub:
        stats.roi === null
          ? undefined
          : `${(stats.roi * 100).toFixed(1)}% gross`,
      tone: stats.net_roi === null ? undefined : stats.net_roi > 0 ? 'gain' : 'loss',
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
              {it.sub && (
                <div className="mt-0.5 font-mono text-[10px] tabular-nums text-text-muted">
                  {it.sub}
                </div>
              )}
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
            <th className="px-3 py-2 text-right">Fees</th>
            <th className="px-3 py-2 text-right">Net P&amp;L</th>
            <th className="px-3 py-2 text-right">Return</th>
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
  const gross = bet.pnl_cents
  const net = bet.net_pnl_cents
  // Use net PnL for tone — that's what actually changes your account.
  const pnlCls =
    net === null ? 'text-text-muted' : net > 0 ? 'text-gain' : net < 0 ? 'text-loss' : 'text-text'
  // Return % = net_pnl / stake. Net is what you actually pocket; gross
  // would overstate ROI by hiding fees from the % figure.
  const returnPct =
    net === null || bet.stake_cents === 0 ? null : (net / bet.stake_cents) * 100
  const statusCls =
    bet.status === 'won'
      ? 'text-gain'
      : bet.status === 'lost'
      ? 'text-loss'
      : bet.status === 'cancelled'
      ? 'text-text-muted'
      : 'text-action'
  const partialClose =
    bet.status === 'open' && bet.remaining_quantity > 0 && bet.remaining_quantity < bet.quantity
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
          {partialClose ? (
            <span>
              <span className="text-text-muted">{bet.quantity} → </span>
              <span className="text-action">{bet.remaining_quantity}</span>
              <span className="text-text-muted"> left</span>
              <span className="ml-1 text-text-muted">@{bet.entry_price_cents}¢</span>
            </span>
          ) : (
            <>
              {bet.quantity} × {bet.entry_price_cents}¢
            </>
          )}
        </td>
        <td className="px-3 py-2 text-right font-mono tabular-nums text-xs text-text-muted">
          {bet.fees_cents === 0 ? '—' : `−$${(bet.fees_cents / 100).toFixed(2)}`}
        </td>
        <td className={`px-3 py-2 text-right font-mono tabular-nums text-xs ${pnlCls}`}>
          {net === null ? (
            '—'
          ) : (
            <>
              <div>{`${net >= 0 ? '+' : ''}$${(net / 100).toFixed(2)}`}</div>
              {gross !== null && gross !== net && (
                <div className="text-[10px] text-text-muted">
                  {`${gross >= 0 ? '+' : ''}$${(gross / 100).toFixed(2)} gross`}
                </div>
              )}
            </>
          )}
        </td>
        <td className={`px-3 py-2 text-right font-mono tabular-nums text-xs ${pnlCls}`}>
          {returnPct === null
            ? '—'
            : `${returnPct >= 0 ? '+' : ''}${returnPct.toFixed(0)}%`}
        </td>
        <td className="px-3 py-2 text-xs">{bet.strategy}</td>
        <td className={`px-3 py-2 text-xs font-semibold uppercase ${statusCls}`}>
          {bet.status}
        </td>
      </tr>
      {expanded && <BetDetail bet={bet} />}
    </>
  )
}

function BetDetail({ bet }: { bet: Bet }) {
  const fills = useQuery<BetFillsResponse>({
    queryKey: ['ledger_fills', bet.id],
    queryFn: async () => {
      const res = await fetch(`/api/ledger/${bet.id}/fills`)
      if (!res.ok) throw new Error(`/api/ledger/${bet.id}/fills: ${res.status}`)
      return res.json()
    },
    refetchInterval: 30_000,
  })
  return (
    <tr className="border-b border-border bg-bg last:border-b-0">
      <td colSpan={9} className="px-3 py-3">
        <div className="grid gap-4 lg:grid-cols-2">
          <dl className="grid gap-x-6 gap-y-1 text-xs sm:grid-cols-2">
            <Pair label="Source" value={bet.source} />
            <Pair label="Confidence" value={bet.confidence} />
            <Pair label="Timing" value={bet.timing} />
            <Pair label="Exit type" value={bet.exit_type ?? '—'} />
            <Pair
              label="Avg exit price"
              value={bet.exit_price_cents !== null ? `${bet.exit_price_cents}¢` : '—'}
            />
            <Pair label="Settled" value={formatET(bet.settled_at) || '—'} />
            <Pair label="Stake" value={`$${(bet.stake_cents / 100).toFixed(2)}`} />
            <Pair
              label="Entry fees"
              value={`$${(bet.entry_fees_cents / 100).toFixed(2)}`}
            />
            <Pair
              label="Exit fees"
              value={`$${(bet.exit_fees_cents / 100).toFixed(2)}`}
            />
            <Pair
              label="Gross P&L"
              value={
                bet.pnl_cents === null
                  ? '—'
                  : `${bet.pnl_cents >= 0 ? '+' : ''}$${(bet.pnl_cents / 100).toFixed(2)}`
              }
            />
            {bet.human_reasoning && (
              <Pair label="Human reasoning" value={bet.human_reasoning} wide />
            )}
            {bet.ai_reasoning && (
              <Pair label="AI reasoning" value={bet.ai_reasoning} wide />
            )}
          </dl>
          <FillsList query={fills} />
        </div>
      </td>
    </tr>
  )
}

function FillsList({
  query,
}: {
  query: ReturnType<typeof useQuery<BetFillsResponse>>
}) {
  if (query.isPending) {
    return <div className="h-24 animate-pulse rounded-md border border-border bg-bg-card" />
  }
  if (query.isError || !query.data) {
    return (
      <div className="rounded-md border border-border bg-bg-card p-3 text-xs text-text-muted">
        Couldn't load fills.
      </div>
    )
  }
  const fills = query.data.fills
  if (fills.length === 0) {
    return (
      <div className="rounded-md border border-border bg-bg-card p-3 text-xs text-text-muted">
        No fills recorded yet.
      </div>
    )
  }
  return (
    <div className="rounded-md border border-border bg-bg-card">
      <div className="border-b border-border px-3 py-2 text-xs uppercase tracking-wide text-text-muted">
        Fills ({fills.length})
      </div>
      <ul className="divide-y divide-border">
        {fills.map((f) => {
          const qty =
            f.quantity_centi % 100 === 0
              ? String(f.quantity_centi / 100)
              : (f.quantity_centi / 100).toFixed(2)
          const taker = f.is_taker === true
          return (
            <li
              key={f.id}
              className="grid grid-cols-[auto_1fr_auto] items-center gap-3 px-3 py-1.5 text-xs"
            >
              <span
                className={`font-mono uppercase ${
                  f.action === 'buy' ? 'text-action' : 'text-gain'
                }`}
              >
                {f.action}
              </span>
              <span className="font-mono tabular-nums text-text">
                {qty} @ {f.price_cents}¢
                <span className="ml-2 text-[10px] uppercase text-text-muted">
                  {taker ? 'taker' : 'maker'}
                </span>
              </span>
              <span className="font-mono tabular-nums text-text-muted">
                {f.fee_cents === null
                  ? 'fee pending'
                  : f.fee_cents === 0
                  ? '$0.00 fee'
                  : `−$${(f.fee_cents / 100).toFixed(2)} fee`}
              </span>
            </li>
          )
        })}
      </ul>
    </div>
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
