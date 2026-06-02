import { useQuery } from '@tanstack/react-query'

import InlineError from '../components/InlineError'
import { formatET } from '../lib/format'

type NewsArticle = {
  headline: string
  description: string
  published: string | null
  teams: string[]
  url: string | null
}
type NewsResponse = { articles: NewsArticle[]; refreshed_at: string | null }

type FuturesOption = {
  ticker: string
  label: string | null
  yes_bid: string | null
  yes_ask: string | null
  last: string | null
  status: string
}
type FuturesEvent = {
  event_ticker: string
  title: string | null
  options: FuturesOption[]
}
type FuturesSection = {
  series: string
  title: string
  events: FuturesEvent[]
}
type FuturesResponse = { sections: FuturesSection[] }

/**
 * World Cup futures board — read-only. Tournament-level markets (Winner, Golden
 * Boot, group outcomes) priced in deci-cents, which the app's whole-cent money
 * core can't trade, so this is a reading surface; futures trades go on
 * kalshi.com. Prices are display strings ("17.1¢") straight from the backend.
 */
export default function Futures() {
  const { data, isPending, isError, error } = useQuery<FuturesResponse>({
    queryKey: ['futures'],
    queryFn: async () => {
      const res = await fetch('/api/futures')
      if (!res.ok) throw new Error(`/api/futures: ${res.status}`)
      return res.json()
    },
    refetchInterval: 60_000,
  })

  return (
    <div className="space-y-6">
      <div className="flex items-baseline justify-between">
        <h2 className="text-lg font-semibold text-text">World Cup</h2>
        <span className="text-[11px] text-text-muted">
          Read-only · trade futures on kalshi.com
        </span>
      </div>

      <NewsBoard />

      {isError && <InlineError message="Couldn't load futures." detail={error} />}
      {isPending && <div className="text-sm text-text-muted">Loading the board…</div>}

      {data?.sections.length === 0 && (
        <div className="rounded-lg border border-border bg-bg-card p-4 text-sm text-text-muted">
          No futures listed right now.
        </div>
      )}

      {data?.sections.map((section) => (
        <FuturesSectionBlock key={section.series} section={section} />
      ))}
    </div>
  )
}

function FuturesSectionBlock({ section }: { section: FuturesSection }) {
  // A single-event section (Winner, Golden Boot) renders one list; a multi-event
  // one (Group Qualifiers) renders a sub-heading per event.
  const single = section.events.length === 1
  return (
    <section className="rounded-lg border border-border bg-bg-card p-4">
      <h3 className="mb-3 text-sm font-semibold text-text">{section.title}</h3>
      <div className="space-y-4">
        {section.events.map((ev) => (
          <div key={ev.event_ticker}>
            {!single && (
              <h4 className="mb-1.5 text-xs font-medium text-text-muted">{ev.title}</h4>
            )}
            <OptionTable options={ev.options} />
          </div>
        ))}
      </div>
    </section>
  )
}

function NewsBoard() {
  const { data, isError } = useQuery<NewsResponse>({
    queryKey: ['news'],
    queryFn: async () => {
      const res = await fetch('/api/news')
      if (!res.ok) throw new Error(`/api/news: ${res.status}`)
      return res.json()
    },
    refetchInterval: 300_000, // 5 min
  })

  // Don't render an empty/broken news block — it's a bonus surface.
  if (isError || !data || data.articles.length === 0) return null

  return (
    <section className="rounded-lg border border-border bg-bg-card p-4">
      <h3 className="mb-3 text-sm font-semibold text-text">World Cup news</h3>
      <ul className="divide-y divide-border">
        {data.articles.slice(0, 15).map((a, i) => (
          <li key={i} className="py-2">
            <a
              href={a.url ?? '#'}
              target="_blank"
              rel="noreferrer"
              className="text-xs text-text hover:text-action"
            >
              {a.headline}
            </a>
            <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[10px] text-text-muted">
              {a.published && <span>{formatET(a.published)}</span>}
              {a.teams.slice(0, 4).map((t) => (
                <span key={t} className="rounded bg-bg px-1.5 py-0.5">{t}</span>
              ))}
            </div>
          </li>
        ))}
      </ul>
    </section>
  )
}

function OptionTable({ options }: { options: FuturesOption[] }) {
  // Only show options with a live price — far longshots Kalshi hasn't priced
  // are noise on the board.
  const priced = options.filter((o) => o.yes_ask || o.yes_bid || o.last)
  if (priced.length === 0) {
    return <div className="text-xs text-text-muted">No prices yet.</div>
  }
  return (
    <ul className="divide-y divide-border">
      {priced.map((o) => (
        <li key={o.ticker} className="flex items-center justify-between py-1.5 text-xs">
          <span className="text-text">{o.label ?? o.ticker}</span>
          <span className="flex items-center gap-3 font-mono tabular-nums">
            <span className="text-text-muted">{o.yes_bid ?? '—'}</span>
            <span className="text-text-muted">/</span>
            <span className="text-text">{o.yes_ask ?? '—'}</span>
          </span>
        </li>
      ))}
    </ul>
  )
}
