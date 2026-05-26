import { useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { useQuery } from '@tanstack/react-query'
import InlineError from '../components/InlineError'
import Skeleton from '../components/Skeleton'
import { formatET, outcomeLabel } from '../lib/format'

type FeedMarket = {
  ticker: string
  event_ticker: string
  event_title: string
  market_title: string
  yes_sub_title: string | null
  series: string
  status: string
  open_time: string | null
  close_time: string | null
  yes_bid_cents: number | null
  yes_ask_cents: number | null
  volume: number | null
  bucket: 'live' | 'upcoming' | 'recent'
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

  return (
    <section>
      <div className="mb-2 flex items-baseline justify-between">
        <h3 className="text-sm font-semibold text-text">{title}</h3>
        <span className="text-xs text-text-muted">{markets.length}</span>
      </div>
      {subtitle && <p className="mb-3 text-xs text-text-muted">{subtitle}</p>}
      {markets.length === 0 ? (
        <SectionMessage>{empty}</SectionMessage>
      ) : (
        <ul className={collapsed ? 'grid gap-1' : 'grid gap-2'}>
          {markets.map((m) => (
            <MarketRow key={m.ticker} market={m} compact={collapsed} />
          ))}
        </ul>
      )}
    </section>
  )
}

function MarketRow({ market, compact }: { market: FeedMarket; compact: boolean }) {
  const time = formatET(market.open_time)
  return (
    <li>
      <Link
        to={`/event/${encodeURIComponent(market.event_ticker)}?market=${encodeURIComponent(market.ticker)}`}
        className="grid items-center gap-3 rounded-md border border-border bg-bg-card px-3 py-2 transition-colors hover:bg-bg-hover"
        style={{ gridTemplateColumns: '1fr auto auto' }}
      >
        <div className="min-w-0">
          <div className="truncate text-sm text-text">{market.event_title}</div>
          {!compact && (
            <div className="truncate text-xs text-text-muted">
              {outcomeLabel(market.yes_sub_title)}
            </div>
          )}
        </div>
        {!compact && (
          <Price yes_bid={market.yes_bid_cents} yes_ask={market.yes_ask_cents} />
        )}
        <div className="text-right text-xs text-text-muted tabular-nums">{time}</div>
      </Link>
    </li>
  )
}

function Price({ yes_bid, yes_ask }: { yes_bid: number | null; yes_ask: number | null }) {
  if (yes_bid === null && yes_ask === null) {
    return <span className="text-xs text-text-muted">—</span>
  }
  return (
    <span className="text-xs font-mono tabular-nums text-text-muted">
      {yes_bid ?? '—'}
      <span className="px-0.5 text-text-muted">/</span>
      {yes_ask ?? '—'}
      <span className="ml-1 text-text-muted">¢</span>
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
