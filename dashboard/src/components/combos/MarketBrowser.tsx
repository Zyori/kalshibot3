import { useQuery } from '@tanstack/react-query'

import InlineError from '../InlineError'
import Skeleton from '../Skeleton'
import { formatET, formatMatchClock } from '../../lib/format'
import type { FeedMarket, FeedResponse, SlipLeg } from './types'

/**
 * Browse soccer markets and click an outcome to add it to the combo slip.
 * LIVE and UPCOMING games, each showing its pickable outcomes as price chips.
 * A leg is a YES on one outcome (you back it to happen).
 */
export default function MarketBrowser({
  selected,
  onAddLeg,
}: {
  selected: Set<string> // market_tickers already in the slip
  onAddLeg: (leg: SlipLeg) => void
}) {
  const { data, isPending, isError, error } = useQuery<FeedResponse>({
    queryKey: ['markets-feed'],
    queryFn: async () => {
      const res = await fetch('/api/markets/feed')
      if (!res.ok) throw new Error(`feed: ${res.status}`)
      return res.json()
    },
    refetchInterval: 30_000,
  })

  if (isPending) {
    return (
      <div className="space-y-2">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} height={64} />
        ))}
      </div>
    )
  }
  if (isError) {
    return <InlineError message="Couldn't load markets." detail={error} />
  }

  return (
    <div className="space-y-6">
      <Section title="Live" empty="No live games right now." markets={data.live} onAddLeg={onAddLeg} selected={selected} />
      <Section title="Upcoming" empty="No upcoming games." markets={data.upcoming} onAddLeg={onAddLeg} selected={selected} />
    </div>
  )
}

function Section({
  title,
  empty,
  markets,
  selected,
  onAddLeg,
}: {
  title: string
  empty: string
  markets: FeedMarket[]
  selected: Set<string>
  onAddLeg: (leg: SlipLeg) => void
}) {
  const events = groupByEvent(markets)
  return (
    <section>
      <div className="mb-2 flex items-baseline justify-between">
        <h3 className="text-sm font-semibold text-text">{title}</h3>
        <span className="text-xs text-text-muted">
          {events.length} {events.length === 1 ? 'event' : 'events'}
        </span>
      </div>
      {events.length === 0 ? (
        <p className="rounded-md border border-border bg-bg-card px-3 py-4 text-center text-xs text-text-muted">
          {empty}
        </p>
      ) : (
        <ul className="grid gap-2">
          {events.map((g) => (
            <EventRow key={g.event_ticker} group={g} selected={selected} onAddLeg={onAddLeg} />
          ))}
        </ul>
      )}
    </section>
  )
}

type EventGroup = {
  event_ticker: string
  event_title: string
  league: string | null
  open_time: string | null
  espn_state: FeedMarket['espn_state']
  espn_period: number | null
  espn_clock: string | null
  espn_status_detail: string | null
  home_score: number | null
  away_score: number | null
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
        espn_state: m.espn_state,
        espn_period: m.espn_period,
        espn_clock: m.espn_clock,
        espn_status_detail: m.espn_status_detail,
        home_score: m.home_score,
        away_score: m.away_score,
        markets: [],
      }
      map.set(m.event_ticker, g)
    }
    g.markets.push(m)
  }
  return Array.from(map.values())
}

function EventRow({
  group,
  selected,
  onAddLeg,
}: {
  group: EventGroup
  selected: Set<string>
  onAddLeg: (leg: SlipLeg) => void
}) {
  const matchLabel = formatMatchClock(
    group.espn_state, group.espn_period, group.espn_clock,
    group.espn_status_detail, group.open_time,
  )
  const time = matchLabel ?? formatET(group.open_time)
  const hasScore =
    group.home_score !== null && group.away_score !== null &&
    (group.espn_state === 'in' || group.espn_state === 'post')
  // TIE last, otherwise alphabetical — stable so chips don't reshuffle.
  const sorted = [...group.markets].sort((a, b) => {
    const at = a.yes_sub_title?.toLowerCase() === 'tie' ? 1 : 0
    const bt = b.yes_sub_title?.toLowerCase() === 'tie' ? 1 : 0
    if (at !== bt) return at - bt
    return (a.yes_sub_title ?? a.ticker).localeCompare(b.yes_sub_title ?? b.ticker)
  })
  return (
    <li className="rounded-md border border-border bg-bg-card px-3 py-2">
      <div className="flex items-baseline justify-between gap-3">
        <div className="min-w-0 truncate text-sm text-text">
          {group.league && (
            <span className="mr-2 text-[10px] font-semibold uppercase tracking-wide text-action">
              {group.league}
            </span>
          )}
          {group.event_title}
        </div>
        <div className="flex shrink-0 items-baseline gap-2 text-right text-xs tabular-nums">
          {hasScore && (
            <span className={`font-mono font-semibold ${group.espn_state === 'in' ? 'text-text' : 'text-text-muted'}`}>
              {group.home_score}–{group.away_score}
            </span>
          )}
          <span className={group.espn_state === 'in' ? 'font-semibold text-action' : 'text-text-muted'}>
            {time}
          </span>
        </div>
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {sorted.map((m) => (
          <OutcomeButton
            key={m.ticker}
            market={m}
            picked={selected.has(m.ticker)}
            onAdd={onAddLeg}
          />
        ))}
      </div>
    </li>
  )
}

function OutcomeButton({
  market,
  picked,
  onAdd,
}: {
  market: FeedMarket
  picked: boolean
  onAdd: (leg: SlipLeg) => void
}) {
  const label =
    market.yes_sub_title?.toLowerCase() === 'tie'
      ? 'Draw'
      : market.yes_sub_title ?? market.ticker
  const price = market.yes_ask_cents ?? market.yes_bid_cents
  return (
    <button
      type="button"
      disabled={picked}
      onClick={() =>
        onAdd({
          market_ticker: market.ticker,
          event_ticker: market.event_ticker,
          side: 'yes',
          title: label,
          price_cents: price,
        })
      }
      className={`flex items-baseline gap-1.5 rounded border px-2 py-1 text-xs transition-colors ${
        picked
          ? 'border-action bg-action/15 text-text'
          : 'border-border text-text-muted hover:border-action hover:bg-bg-hover hover:text-text'
      }`}
    >
      <span>{label}</span>
      <span className="font-mono tabular-nums text-text">
        {price !== null ? `${price}¢` : '—'}
      </span>
      {picked && <span className="text-action">✓</span>}
    </button>
  )
}
