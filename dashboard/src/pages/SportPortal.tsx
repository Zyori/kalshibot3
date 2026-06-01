import { Link, useNavigate, useParams } from 'react-router'
import { useQuery } from '@tanstack/react-query'

import InlineError from '../components/InlineError'
import NudgeChips from '../components/trading/NudgeChip'
import SuggestionCard from '../components/trading/SuggestionCard'
import { useSuggestions } from '../hooks/useSuggestions'
import {
  formatET,
  formatMatchClock,
  formatPriceCents,
  formatSignedDollars,
} from '../lib/format'
import type { Bet, LedgerStats, Suggestion } from '../lib/types'

// The app is soccer-only today (slug is always "soccer"); the feed is
// already all-soccer, so the Live Games tile shows the whole live block.
type FeedMarket = {
  ticker: string
  event_ticker: string
  event_title: string
  yes_sub_title: string | null
  league: string | null
  open_time: string | null
  yes_bid_cents: number | null
  yes_ask_cents: number | null
  espn_state: 'pre' | 'in' | 'post' | null
  espn_period: number | null
  espn_clock: string | null
  espn_status_detail: string | null
  home_name: string | null
  away_name: string | null
  home_score: number | null
  away_score: number | null
}

type FeedResponse = { live: FeedMarket[] }

type Position = {
  ticker: string
  side: 'yes' | 'no'
  quantity: number
  avg_entry_price_cents: number | null
  avg_entry_price: number | null
  current_price_cents: number | null
  unrealized_pnl_cents: number | null
  realized_pnl_cents: number | null
}

type PositionsResponse = { positions: Position[] }

export default function SportPortal() {
  const { slug } = useParams<{ slug: string }>()
  const sport = slug ?? 'soccer'

  return (
    <div className="space-y-6">
      <header>
        <h2 className="text-lg font-semibold text-text capitalize">{sport}</h2>
        <p className="mt-1 text-sm text-text-muted">
          Live games and your open positions. Suggestions, markets, and news land later.
        </p>
      </header>

      <NudgeChips />

      <div className="grid gap-4 md:grid-cols-2">
        <LiveGamesTile />
        <OpenPositionsTile />
        <PlaceholderTile title="Markets" note="Browse the full feed →" to="/" />
        <SuggestedBetsTile />
        <PlaceholderTile title="News" note="Coming soon." />
      </div>

      <HistorySection sport={sport} />
    </div>
  )
}

function Tile({
  title,
  count,
  children,
}: {
  title: string
  count?: number
  children: React.ReactNode
}) {
  return (
    <div className="rounded-lg border border-border bg-bg-card p-4">
      <div className="mb-2 flex items-baseline justify-between">
        <h3 className="text-sm font-semibold text-text">{title}</h3>
        {count !== undefined && (
          <span className="text-xs text-text-muted">{count}</span>
        )}
      </div>
      {children}
    </div>
  )
}

function PlaceholderTile({
  title,
  note,
  to,
}: {
  title: string
  note: string
  to?: string
}) {
  return (
    <Tile title={title}>
      {to ? (
        <Link to={to} className="text-xs text-action hover:underline">
          {note}
        </Link>
      ) : (
        <p className="text-xs text-text-muted">{note}</p>
      )}
    </Tile>
  )
}

function LiveGamesTile() {
  const { data, isPending, isError } = useQuery<FeedResponse>({
    queryKey: ['markets-feed'],
    queryFn: async () => {
      const res = await fetch('/api/markets/feed')
      if (!res.ok) throw new Error(`feed: ${res.status}`)
      return res.json()
    },
    refetchInterval: 30_000,
  })

  // One row per event (the feed is per-market — 3 rows for a 3-way moneyline).
  const events = groupLiveByEvent(data?.live ?? [])

  return (
    <Tile title="Live Games" count={isPending ? undefined : events.length}>
      {isError ? (
        <InlineError message="Couldn't load live games." />
      ) : isPending ? (
        <p className="text-xs text-text-muted">Loading…</p>
      ) : events.length === 0 ? (
        <p className="text-xs text-text-muted">No matches currently in play.</p>
      ) : (
        <ul className="space-y-1">
          {events.map((e) => (
            <li key={e.event_ticker}>
              <Link
                to={`/event/${encodeURIComponent(e.event_ticker)}`}
                className="flex items-baseline justify-between gap-2 rounded-md px-2 py-1.5 text-xs hover:bg-bg-hover"
              >
                <span className="min-w-0 truncate text-text">{e.event_title}</span>
                <span className="flex shrink-0 items-baseline gap-2 tabular-nums">
                  {e.home_score !== null && e.away_score !== null && (
                    <span className="font-mono font-semibold text-text">
                      {e.home_score}–{e.away_score}
                    </span>
                  )}
                  <span className="font-semibold text-action">{e.clock}</span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Tile>
  )
}

type LiveEvent = {
  event_ticker: string
  event_title: string
  home_score: number | null
  away_score: number | null
  clock: string
}

function groupLiveByEvent(rows: FeedMarket[]): LiveEvent[] {
  const seen = new Map<string, LiveEvent>()
  for (const m of rows) {
    if (seen.has(m.event_ticker)) continue
    const clock =
      formatMatchClock(
        m.espn_state,
        m.espn_period,
        m.espn_clock,
        m.espn_status_detail,
        m.open_time,
      ) ?? formatET(m.open_time)
    seen.set(m.event_ticker, {
      event_ticker: m.event_ticker,
      event_title: m.event_title,
      home_score: m.home_score,
      away_score: m.away_score,
      clock,
    })
  }
  return Array.from(seen.values())
}

function OpenPositionsTile() {
  const { data, isPending, isError } = useQuery<PositionsResponse>({
    queryKey: ['positions'],
    queryFn: async () => {
      const res = await fetch('/api/positions')
      if (!res.ok) throw new Error(`/api/positions: ${res.status}`)
      return res.json()
    },
    refetchInterval: 10_000,
  })

  const positions = data?.positions ?? []

  return (
    <Tile title="Open Positions" count={isPending ? undefined : positions.length}>
      {isError ? (
        <InlineError message="Couldn't load positions." />
      ) : isPending ? (
        <p className="text-xs text-text-muted">Loading…</p>
      ) : positions.length === 0 ? (
        <p className="text-xs text-text-muted">No open positions.</p>
      ) : (
        <ul className="space-y-1">
          {positions.map((p) => {
            const pnl = p.unrealized_pnl_cents
            const tone =
              pnl === null ? 'text-text-muted' : pnl >= 0 ? 'text-gain' : 'text-loss'
            // % return on cost basis (entry × qty). Both the exact fractional
            // entry and quantity are on the row, so derive here rather than
            // adding a backend field. null when we can't compute (no entry/pnl).
            const entry = p.avg_entry_price ?? p.avg_entry_price_cents
            const costCents = entry !== null ? entry * p.quantity : null
            const pct =
              pnl !== null && costCents !== null && costCents > 0
                ? (pnl / costCents) * 100
                : null
            return (
              <li key={`${p.ticker}:${p.side}`}>
                <Link
                  to={`/event/${encodeURIComponent(eventTickerOf(p.ticker))}?market=${encodeURIComponent(p.ticker)}`}
                  className="flex items-baseline justify-between gap-2 rounded-md px-2 py-1.5 text-xs hover:bg-bg-hover"
                >
                  <span className="min-w-0 truncate font-mono text-text-muted">
                    {p.ticker}
                  </span>
                  <span className="flex shrink-0 items-baseline gap-2 tabular-nums">
                    <span className="font-mono text-text">
                      {p.side.toUpperCase()} {p.quantity} @{' '}
                      {formatPriceCents(p.avg_entry_price ?? p.avg_entry_price_cents)}
                    </span>
                    <span className={`font-mono ${tone}`}>
                      {pnl === null ? '—' : formatSignedDollars(pnl)}
                      {pct !== null && (
                        <span className="ml-1">
                          ({pct >= 0 ? '+' : ''}{pct.toFixed(1)}%)
                        </span>
                      )}
                    </span>
                  </span>
                </Link>
              </li>
            )
          })}
        </ul>
      )}
    </Tile>
  )
}

// A market ticker is "{EVENT}-{OUTCOME}"; the event ticker is everything
// before the last hyphen segment. Used to deep-link a position to its event.
function eventTickerOf(marketTicker: string): string {
  const i = marketTicker.lastIndexOf('-')
  return i === -1 ? marketTicker : marketTicker.slice(0, i)
}

function SuggestedBetsTile() {
  const navigate = useNavigate()
  const { suggestions, isError } = useSuggestions()
  // Entry suggestions surface here; exit suggestions render on their market's
  // card inside the event page.
  const entries = suggestions.filter((s) => s.kind === 'entry')

  // Staging an entry from the feed deep-links to the event with the market
  // pre-selected, where the OrderPanel pre-fill completes the hand-off. We
  // carry the suggestion's price/side in the URL so the event card can apply
  // it. (The market card reads ?stage params on open.)
  const stage = (s: Suggestion) => {
    if (!s.ticker) return
    const ev = eventTickerOf(s.ticker)
    const params = new URLSearchParams({
      market: s.ticker,
      stage_side: s.side,
      stage_price: String(s.suggested_price_cents),
      stage_size: String(s.suggested_size_cents),
    })
    navigate(`/event/${encodeURIComponent(ev)}?${params.toString()}`)
  }

  return (
    <Tile title="Suggested Bets" count={entries.length || undefined}>
      {isError ? (
        <InlineError message="Couldn't load suggestions." />
      ) : entries.length === 0 ? (
        <p className="text-xs text-text-muted">
          No suggestions. Ask the partner in a terminal session.
        </p>
      ) : (
        <div className="space-y-2">
          {entries.map((s) => (
            <SuggestionCard key={s.id} suggestion={s} onStage={stage} />
          ))}
        </div>
      )}
    </Tile>
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
  const stats = useQuery<LedgerStats>({
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
          <Stat label="Bets" value={String(s.total_bets)} />
          <Stat
            label="Net P&L"
            value={formatSignedDollars(s.total_net_pnl_cents)}
            tone={
              s.total_net_pnl_cents > 0
                ? 'gain'
                : s.total_net_pnl_cents < 0
                ? 'loss'
                : undefined
            }
          />
          <Stat
            label="Win rate"
            value={s.win_rate === null ? '—' : `${(s.win_rate * 100).toFixed(0)}%`}
          />
          <Stat
            label="Net ROI"
            value={s.net_roi === null ? '—' : `${(s.net_roi * 100).toFixed(1)}%`}
            tone={s.net_roi === null ? undefined : s.net_roi > 0 ? 'gain' : 'loss'}
          />
        </div>
      )}

      {recent.isError || stats.isError ? (
        <InlineError
          message="Couldn't load history."
          detail={recent.error ?? stats.error}
        />
      ) : bets.length === 0 ? (
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
                {b.side.toUpperCase()} {b.quantity} @{' '}
                {formatPriceCents(b.entry_price ?? b.entry_price_cents)}
              </div>
              <div
                className={`font-mono tabular-nums ${
                  b.net_pnl_cents === null
                    ? 'text-text-muted'
                    : b.net_pnl_cents > 0
                    ? 'text-gain'
                    : b.net_pnl_cents < 0
                    ? 'text-loss'
                    : 'text-text'
                }`}
              >
                {b.net_pnl_cents === null ? '—' : formatSignedDollars(b.net_pnl_cents)}
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
