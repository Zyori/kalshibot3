import { Link, useParams } from 'react-router'
import { useQuery } from '@tanstack/react-query'

import { formatET } from '../lib/format'

type Bet = {
  id: number
  sport: string
  ticker: string | null
  side: 'yes' | 'no'
  entry_price_cents: number
  quantity: number
  pnl_cents: number | null
  status: string
  placed_at: string | null
}

type Stats = {
  total_bets: number
  total_pnl_cents: number
  win_rate: number | null
  roi: number | null
  by_status: Record<string, number>
}

export default function SportPortal() {
  const { slug } = useParams<{ slug: string }>()

  return (
    <div className="space-y-6">
      <header>
        <h2 className="text-lg font-semibold text-text capitalize">{slug ?? 'sport'}</h2>
        <p className="mt-1 text-sm text-text-muted">
          Sport-specific portal. Other sections (suggestions, live games, news) land
          with Phase 4. History below is wired now.
        </p>
      </header>

      <PlaceholderGrid />

      <HistorySection sport={slug ?? 'soccer'} />
    </div>
  )
}

function PlaceholderGrid() {
  const sections = ['Suggested Bets', 'Live Games', 'Markets', 'Open Positions', 'News']
  return (
    <div className="grid gap-4 md:grid-cols-2">
      {sections.map((title) => (
        <div key={title} className="rounded-lg border border-border bg-bg-card p-4">
          <h3 className="text-sm font-semibold text-text">{title}</h3>
          <p className="mt-2 text-xs text-text-muted">Coming soon.</p>
        </div>
      ))}
    </div>
  )
}

function HistorySection({ sport }: { sport: string }) {
  // The API takes sport as a repeatable query param; this page is sport-scoped
  // so we only ever send the one. Limit kept small so the section is a
  // glance, not a deep dive — full ledger has filters & charts.
  const qs = `?sport=${encodeURIComponent(sport)}&limit=10`

  const recent = useQuery<{ bets: Bet[] }>({
    queryKey: ['portal_recent_bets', sport],
    queryFn: async () => {
      const res = await fetch(`/api/ledger${qs}`)
      if (!res.ok) throw new Error(`/api/ledger: ${res.status}`)
      return res.json()
    },
    refetchInterval: 30_000,
  })
  const stats = useQuery<Stats>({
    queryKey: ['portal_stats', sport],
    queryFn: async () => {
      const res = await fetch(`/api/ledger/stats?sport=${encodeURIComponent(sport)}`)
      if (!res.ok) throw new Error(`/api/ledger/stats: ${res.status}`)
      return res.json()
    },
    refetchInterval: 30_000,
  })

  const bets = recent.data?.bets ?? []
  const s = stats.data

  return (
    <section className="space-y-3">
      <div className="flex items-baseline justify-between">
        <h3 className="text-sm font-semibold uppercase tracking-wide text-text-muted">
          Recent history
        </h3>
        <Link to="/ledger" className="text-xs text-action hover:underline">
          Full ledger →
        </Link>
      </div>

      {s && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Stat
            label="Bets"
            value={String(s.total_bets)}
          />
          <Stat
            label="P&L"
            value={
              s.total_pnl_cents === 0
                ? '$0.00'
                : `${s.total_pnl_cents >= 0 ? '+' : ''}$${(s.total_pnl_cents / 100).toFixed(2)}`
            }
            tone={s.total_pnl_cents > 0 ? 'gain' : s.total_pnl_cents < 0 ? 'loss' : undefined}
          />
          <Stat
            label="Win rate"
            value={s.win_rate === null ? '—' : `${(s.win_rate * 100).toFixed(0)}%`}
          />
          <Stat
            label="ROI"
            value={s.roi === null ? '—' : `${(s.roi * 100).toFixed(1)}%`}
            tone={s.roi === null ? undefined : s.roi > 0 ? 'gain' : 'loss'}
          />
        </div>
      )}

      {bets.length === 0 ? (
        <div className="rounded-lg border border-border bg-bg-card p-4 text-center text-xs text-text-muted">
          No bets yet for {sport}.
        </div>
      ) : (
        <ul className="overflow-hidden rounded-lg border border-border bg-bg-card">
          {bets.map((b) => (
            <li
              key={b.id}
              className="grid items-center gap-3 border-b border-border px-3 py-2 text-xs last:border-b-0"
              style={{ gridTemplateColumns: '1fr auto auto auto' }}
            >
              <div className="min-w-0">
                <div className="truncate font-mono text-text-muted">{b.ticker ?? '—'}</div>
                <div className="text-[10px] text-text-muted">{formatET(b.placed_at)}</div>
              </div>
              <div className="font-mono tabular-nums text-text">
                {b.side.toUpperCase()} {b.quantity} @ {b.entry_price_cents}¢
              </div>
              <div
                className={`font-mono tabular-nums ${
                  b.pnl_cents === null
                    ? 'text-text-muted'
                    : b.pnl_cents > 0
                    ? 'text-gain'
                    : b.pnl_cents < 0
                    ? 'text-loss'
                    : 'text-text'
                }`}
              >
                {b.pnl_cents === null
                  ? '—'
                  : `${b.pnl_cents >= 0 ? '+' : ''}$${(b.pnl_cents / 100).toFixed(2)}`}
              </div>
              <span
                className={`text-[10px] uppercase ${
                  b.status === 'won'
                    ? 'text-gain'
                    : b.status === 'lost'
                    ? 'text-loss'
                    : 'text-text-muted'
                }`}
              >
                {b.status}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function Stat({
  label,
  value,
  tone,
}: {
  label: string
  value: string
  tone?: 'gain' | 'loss'
}) {
  const cls =
    tone === 'gain' ? 'text-gain' : tone === 'loss' ? 'text-loss' : 'text-text'
  return (
    <div className="rounded-lg border border-border bg-bg-card p-3">
      <div className="text-xs text-text-muted">{label}</div>
      <div className={`mt-1 font-mono text-lg tabular-nums ${cls}`}>{value}</div>
    </div>
  )
}
