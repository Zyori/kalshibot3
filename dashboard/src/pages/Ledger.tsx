import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import InlineError from '../components/InlineError'
import PnLChart from '../components/charts/PnLChart'
import StrategyBreakdown from '../components/charts/StrategyBreakdown'
import BetMetadataForm from '../components/ledger/BetMetadataForm'
import { SportBadge, KNOWN_SPORTS } from '../components/ledger/SportBadge'
import { formatET, formatDollars, formatFee, formatSignedDollars } from '../lib/format'
import type { Bet, BetFillsResponse, LedgerStats as Stats } from '../lib/types'

type LedgerResponse = { bets: Bet[]; next_cursor: number | null }

const STATUS_OPTIONS = ['open', 'won', 'lost', 'cancelled'] as const
const SOURCE_OPTIONS = ['human', 'ai', 'collaborative', 'external'] as const
const STRATEGY_OPTIONS = [
  'mean_reversion',
  'mean_confirmation',
  'lock_parlay',
  'underdog',
  'moon_parlay',
  'draw_value',
  'scalp',
  'hedge',
  'manual',
] as const
const TIMING_OPTIONS = ['pre_match', 'live', 'futures'] as const

type Filters = {
  sport: string[]
  status: string[]
  source: string[]
  strategy: string[]
  timing: string[]
}

export default function Ledger() {
  const [filters, setFilters] = useState<Filters>({
    sport: [],
    status: [],
    source: [],
    strategy: [],
    timing: [],
  })
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [editingId, setEditingId] = useState<number | null>(null)

  const queryString = useMemo(() => {
    const params = new URLSearchParams()
    for (const v of filters.sport) params.append('sport', v)
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
        title="Sport"
        options={KNOWN_SPORTS}
        selected={filters.sport}
        onToggle={(v) => toggle('sport', v)}
      />
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
          editingId={editingId}
          onToggleExpand={(id) => {
            setExpandedId((cur) => (cur === id ? null : id))
            setEditingId(null)
          }}
          onStartEdit={(id) => setEditingId(id)}
          onStopEdit={() => setEditingId(null)}
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
      value: formatSignedDollars(net),
      sub: `${formatSignedDollars(gross)} gross · -${formatDollars(fees)} fees`,
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
  editingId,
  onToggleExpand,
  onStartEdit,
  onStopEdit,
}: {
  bets: Bet[]
  isLoading: boolean
  expandedId: number | null
  editingId: number | null
  onToggleExpand: (id: number) => void
  onStartEdit: (id: number) => void
  onStopEdit: () => void
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
            <th className="px-3 py-2 text-left"></th>
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
              editing={editingId === b.id}
              onToggle={() => onToggleExpand(b.id)}
              onStartEdit={() => onStartEdit(b.id)}
              onStopEdit={onStopEdit}
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
  editing,
  onToggle,
  onStartEdit,
  onStopEdit,
}: {
  bet: Bet
  expanded: boolean
  editing: boolean
  onToggle: () => void
  onStartEdit: () => void
  onStopEdit: () => void
}) {
  const gross = bet.pnl_cents
  const net = bet.net_pnl_cents
  // Use net PnL for tone — that's what actually changes your account.
  const pnlCls =
    net === null ? 'text-text-muted' : net > 0 ? 'text-gain' : net < 0 ? 'text-loss' : 'text-text'
  // Return % = net_pnl / total committed capital (stake + entry fees).
  // Using stake alone produced > -100% on fully-lost bets because the entry
  // fee was in the numerator but not the denominator. The fee was real
  // capital out the door — count it on both sides.
  const committed = bet.stake_cents + bet.entry_fees_cents
  const returnPct = net === null || committed === 0 ? null : (net / committed) * 100
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
  // The market settled on Kalshi but our row hasn't caught up. Sweeper
  // runs every 60s; this label tells the user the system knows and is
  // waiting rather than stuck.
  const awaitingSettlement =
    bet.status === 'open' &&
    (bet.market_status === 'closed' || bet.market_status === 'settled')
  return (
    <>
      <tr
        onClick={onToggle}
        className="cursor-pointer border-b border-border last:border-b-0 hover:bg-bg-hover"
      >
        <td className="px-3 py-2 text-center">
          <SportBadge sport={bet.sport} compact />
        </td>
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
          {formatFee(bet.fees_cents)}
        </td>
        <td className={`px-3 py-2 text-right font-mono tabular-nums text-xs ${pnlCls}`}>
          {net === null ? (
            '—'
          ) : (
            <>
              <div>{formatSignedDollars(net)}</div>
              {gross !== null && gross !== net && (
                <div className="text-[10px] text-text-muted">
                  {`${formatSignedDollars(gross)} gross`}
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
        <td className="px-3 py-2 text-xs">
          <span>{bet.strategy}</span>
          {bet.metadata_edited_at && (
            <span
              className="ml-1 text-[10px] text-text-muted"
              title={`Edited ${formatET(bet.metadata_edited_at)}`}
            >
              ✎
            </span>
          )}
        </td>
        <td className={`px-3 py-2 text-xs font-semibold uppercase ${statusCls}`}>
          {awaitingSettlement ? (
            <span className="text-action" title="Market closed on Kalshi; sweeper will resolve this shortly.">
              awaiting settlement
            </span>
          ) : (
            bet.status
          )}
        </td>
      </tr>
      {expanded && (
        <BetDetail
          bet={bet}
          editing={editing}
          onStartEdit={onStartEdit}
          onStopEdit={onStopEdit}
        />
      )}
    </>
  )
}

function BetDetail({
  bet,
  editing,
  onStartEdit,
  onStopEdit,
}: {
  bet: Bet
  editing: boolean
  onStartEdit: () => void
  onStopEdit: () => void
}) {
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
      <td colSpan={10} className="px-3 py-3">
        {bet.status === 'open' && <ForceSettleControl bet={bet} />}
        <div className="mb-2 flex items-center justify-end">
          {!editing ? (
            <button
              type="button"
              onClick={onStartEdit}
              className="rounded border border-border px-2 py-0.5 text-[11px] text-text-muted hover:bg-bg-hover hover:text-text"
            >
              ✎ Edit details
            </button>
          ) : null}
        </div>
        <div className="grid gap-4 lg:grid-cols-2">
          {editing ? (
            <BetMetadataForm bet={bet} onDone={onStopEdit} />
          ) : (
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
              <Pair label="Stake" value={formatDollars(bet.stake_cents)} />
              <Pair label="Entry fees" value={formatDollars(bet.entry_fees_cents)} />
              <Pair label="Exit fees" value={formatDollars(bet.exit_fees_cents)} />
              <Pair
                label="Gross P&L"
                value={bet.pnl_cents === null ? '—' : formatSignedDollars(bet.pnl_cents)}
              />
              {bet.tags && bet.tags.length > 0 && (
                <Pair label="Tags" value={bet.tags.join(', ')} wide />
              )}
              {bet.human_reasoning && (
                <Pair label="Why" value={bet.human_reasoning} wide />
              )}
              {bet.ai_reasoning && (
                <Pair label="AI reasoning" value={bet.ai_reasoning} wide />
              )}
              {bet.metadata_edited_at && (
                <Pair
                  label="Last edited"
                  value={formatET(bet.metadata_edited_at) || '—'}
                />
              )}
            </dl>
          )}
          <FillsList query={fills} />
        </div>
      </td>
    </tr>
  )
}

function ForceSettleControl({ bet }: { bet: Bet }) {
  const qc = useQueryClient()
  const mut = useMutation({
    mutationFn: async (value: number) => {
      const res = await fetch(`/api/ledger/${bet.id}/force-settle`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ settlement_value_cents: value }),
      })
      if (!res.ok) {
        const body = await res.text()
        throw new Error(`force-settle ${res.status}: ${body}`)
      }
      return res.json()
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['ledger'] })
      qc.invalidateQueries({ queryKey: ['ledger_stats'] })
      qc.invalidateQueries({ queryKey: ['ledger_fills', bet.id] })
    },
  })
  // YES-side payoff. NO won = 0, YES won = 100, void = 50.
  const lostValue = bet.side === 'yes' ? 0 : 100
  const wonValue = bet.side === 'yes' ? 100 : 0
  return (
    <div className="mb-3 flex flex-wrap items-center gap-2 rounded-md border border-border bg-bg-card p-2 text-xs">
      <span className="text-text-muted">Force-settle (escape hatch):</span>
      <button
        type="button"
        disabled={mut.isPending}
        onClick={() => {
          if (confirm(`Mark this ${bet.side.toUpperCase()} bet as LOST?`)) {
            mut.mutate(lostValue)
          }
        }}
        className="rounded border border-loss/40 px-2 py-0.5 text-loss hover:bg-loss/10 disabled:opacity-50"
      >
        Lost
      </button>
      <button
        type="button"
        disabled={mut.isPending}
        onClick={() => {
          if (confirm(`Mark this ${bet.side.toUpperCase()} bet as WON?`)) {
            mut.mutate(wonValue)
          }
        }}
        className="rounded border border-gain/40 px-2 py-0.5 text-gain hover:bg-gain/10 disabled:opacity-50"
      >
        Won
      </button>
      <button
        type="button"
        disabled={mut.isPending}
        onClick={() => {
          if (confirm('Mark market as VOIDED (refund at 50¢)?')) {
            mut.mutate(50)
          }
        }}
        className="rounded border border-border px-2 py-0.5 text-text-muted hover:bg-bg-hover disabled:opacity-50"
      >
        Void
      </button>
      {mut.isError && (
        <span className="text-loss">{(mut.error as Error).message}</span>
      )}
    </div>
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
                  : `${formatFee(f.fee_cents)} fee`}
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
