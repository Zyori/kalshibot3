import { useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { useQuery } from '@tanstack/react-query'
import InlineError from '../components/InlineError'
import Skeleton from '../components/Skeleton'
import { formatET, formatMatchClock } from '../lib/format'

type FeedMarket = {
  ticker: string
  event_ticker: string
  event_title: string
  market_title: string
  yes_sub_title: string | null
  series: string
  league: string | null
  status: string
  open_time: string | null
  close_time: string | null
  yes_bid_cents: number | null
  yes_ask_cents: number | null
  volume: number | null
  bucket: 'live' | 'upcoming' | 'recent'
  espn_state: 'pre' | 'in' | 'post' | null
  espn_period: number | null
  espn_clock: string | null
  espn_status_detail: string | null
}

type FeedResponse = {
  live: FeedMarket[]
  upcoming: FeedMarket[]
  recent: FeedMarket[]
  refreshed_at: string | null
}

export default function Dashboard() {
  const { data, isPending, isError } = useQuery<FeedResponse>({
    queryKey: ['markets-feed'],
    queryFn: async () => {
      const res = await fetch('/api/markets/feed')
      if (!res.ok) throw new Error(`feed: ${res.status}`)
      return res.json()
    },
    // The backend polls Kalshi every 60s; refetching faster than that just
    // returns the same cache. 30s gives us at-most-30s staleness on the UI.
    refetchInterval: 30_000,
  })

  return (
    <div className="space-y-8">
      <header>
        <h2 className="text-lg font-semibold text-text">Markets</h2>
        <p className="mt-1 text-sm text-text-muted">
          Soccer matches across every Kalshi series we track. Click any market to open it.
        </p>
      </header>

      <TickerLookup />

      {isPending && <FeedSkeleton />}
      {isError && (
        <InlineError message="Couldn't load the market feed." />
      )}

      {data && (
        <>
          <Section
            title="Live now"
            empty="No matches currently in play."
            markets={data.live}
          />
          <Section
            title="Upcoming"
            subtitle="Sorted by kickoff (soonest first)."
            empty="No matches in the next 30 days."
            markets={data.upcoming}
          />
          <Section
            title="Recent results"
            empty=""
            markets={data.recent}
            showWhenEmpty={false}
            collapsed
          />
        </>
      )}
    </div>
  )
}

function TickerLookup() {
  const [value, setValue] = useState('')
  const navigate = useNavigate()

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const ticker = value.trim().toUpperCase()
    if (!ticker) return
    navigate(`/market/${encodeURIComponent(ticker)}`)
  }

  return (
    <form
      onSubmit={onSubmit}
      className="flex items-center gap-2 rounded-lg border border-border bg-bg-card p-3"
    >
      <label className="text-xs text-text-muted">Open ticker:</label>
      <input
        type="text"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="KXWCGAME-26JUN11KORCZE-KOR"
        className="flex-1 rounded-md border border-border bg-bg px-3 py-1.5 text-xs font-mono text-text placeholder:text-text-muted focus:border-accent focus:outline-none"
      />
      <button
        type="submit"
        className="rounded-md border border-border bg-bg-hover px-3 py-1.5 text-xs text-text hover:border-accent"
      >
        Open
      </button>
    </form>
  )
}

function FeedSkeleton() {
  // Mimics one bucket's shape — header row + a handful of card rows. The
  // pulse animation keeps it feeling alive while the data loads.
  return (
    <div className="space-y-8">
      {[1, 2].map((b) => (
        <section key={b} className="space-y-3">
          <Skeleton className="h-5 w-32" />
          <div className="space-y-2">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} height={56} />
            ))}
          </div>
        </section>
      ))}
    </div>
  )
}

/**
 * Group markets by event_ticker, preserving the per-event order of the
 * input (Section already sorts upstream). Each group is `{event, markets}`
 * where `event` carries the metadata shared across children.
 */
type EventGroup = {
  event_ticker: string
  event_title: string
  league: string | null
  open_time: string | null
  bucket: FeedMarket['bucket']
  espn_state: FeedMarket['espn_state']
  espn_period: number | null
  espn_clock: string | null
  espn_status_detail: string | null
  markets: FeedMarket[]
}

function groupByEvent(rows: FeedMarket[]): EventGroup[] {
  const map = new Map<string, EventGroup>()
  for (const m of rows) {
    let g = map.get(m.event_ticker)
    if (!g) {
      g = {
        event_ticker: m.event_ticker,
        event_title: m.event_title,
        league: m.league,
        open_time: m.open_time,
        bucket: m.bucket,
        espn_state: m.espn_state,
        espn_period: m.espn_period,
        espn_clock: m.espn_clock,
        espn_status_detail: m.espn_status_detail,
        markets: [],
      }
      map.set(m.event_ticker, g)
    }
    g.markets.push(m)
  }
  return Array.from(map.values())
}

function Section({
  title,
  subtitle,
  empty,
  markets,
  showWhenEmpty = true,
  collapsed = false,
}: {
  title: string
  subtitle?: string
  empty: string
  markets: FeedMarket[]
  showWhenEmpty?: boolean
  collapsed?: boolean
}) {
  if (markets.length === 0 && !showWhenEmpty) return null
  const events = groupByEvent(markets)

  return (
    <section>
      <div className="mb-2 flex items-baseline justify-between">
        <h3 className="text-sm font-semibold text-text">{title}</h3>
        <span className="text-xs text-text-muted">
          {events.length} {events.length === 1 ? 'event' : 'events'}
        </span>
      </div>
      {subtitle && <p className="mb-3 text-xs text-text-muted">{subtitle}</p>}
      {events.length === 0 ? (
        <SectionMessage>{empty}</SectionMessage>
      ) : (
        <ul className={collapsed ? 'grid gap-1' : 'grid gap-2'}>
          {events.map((g) => (
            <EventRow key={g.event_ticker} group={g} compact={collapsed} />
          ))}
        </ul>
      )}
    </section>
  )
}

function EventRow({ group, compact }: { group: EventGroup; compact: boolean }) {
  // For live games show the match clock ('68\'', 'Half time'). For pre/post
  // show the ESPN-derived label ('Pre-game', 'Final'). Fallback to the
  // kickoff time when ESPN didn't match — better than blank.
  const matchLabel = formatMatchClock(
    group.espn_state,
    group.espn_period,
    group.espn_clock,
    group.espn_status_detail,
    group.open_time,
  )
  const time = matchLabel ?? formatET(group.open_time)
  // Outcome order: put TIE last, otherwise alphabetical by yes_sub_title.
  // Stable across renders so the price chips don't reshuffle.
  const sorted = [...group.markets].sort((a, b) => {
    const at = a.yes_sub_title?.toLowerCase() === 'tie' ? 1 : 0
    const bt = b.yes_sub_title?.toLowerCase() === 'tie' ? 1 : 0
    if (at !== bt) return at - bt
    return (a.yes_sub_title ?? a.ticker).localeCompare(b.yes_sub_title ?? b.ticker)
  })
  return (
    <li>
      <Link
        to={`/event/${encodeURIComponent(group.event_ticker)}`}
        className="block rounded-md border border-border bg-bg-card px-3 py-2 transition-colors hover:bg-bg-hover"
      >
        <div className="flex items-baseline justify-between gap-3">
          <div className="min-w-0 truncate text-sm text-text">
            {group.league && (
              <span className="mr-2 text-[10px] font-semibold uppercase tracking-wide text-action">
                {group.league}
              </span>
            )}
            {group.event_title}
          </div>
          <span
            className={`shrink-0 text-right text-xs tabular-nums ${
              group.espn_state === 'in' ? 'font-semibold text-action' : 'text-text-muted'
            }`}
          >
            {time}
          </span>
        </div>
        {!compact && sorted.length > 0 && (
          <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
            {sorted.map((m) => (
              <OutcomeChip key={m.ticker} market={m} />
            ))}
          </div>
        )}
      </Link>
    </li>
  )
}

function OutcomeChip({ market }: { market: FeedMarket }) {
  const label =
    market.yes_sub_title?.toLowerCase() === 'tie'
      ? 'Draw'
      : market.yes_sub_title ?? market.ticker
  const price = market.yes_ask_cents ?? market.yes_bid_cents
  return (
    <span className="flex items-baseline gap-1 text-text-muted">
      <span>{label}</span>
      <span className="font-mono tabular-nums text-text">
        {price !== null ? `${price}¢` : '—'}
      </span>
    </span>
  )
}

function SectionMessage({
  children,
  tone = 'muted',
}: {
  children: React.ReactNode
  tone?: 'muted' | 'loss'
}) {
  const cls = tone === 'loss' ? 'text-loss' : 'text-text-muted'
  return (
    <div className={`rounded-md border border-border bg-bg-card p-4 text-sm ${cls}`}>
      {children}
    </div>
  )
}
