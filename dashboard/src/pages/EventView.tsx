/**
 * EventView — one page per event (e.g. Nigeria vs Zimbabwe), with a tab
 * strip per outcome market (NGR / ZIM / TIE). Replaces the per-market
 * page as the primary trading surface. Matches kalshi.com's structure.
 *
 * Data flow:
 *   - /api/events/{event_ticker} on mount: event metadata + every child
 *     market + current top-of-book + current position per side.
 *   - WebSocketProvider keeps the per-ticker book cache fresh; the active
 *     tab reads from ['book', activeTicker] via TanStack.
 *   - The all-positions strip stays visible across tab switches.
 *
 * Routing:
 *   /event/{event_ticker}             default tab = first child
 *   /event/{event_ticker}?market=X    open with child X active
 */

import { useEffect, useMemo } from 'react'
import { Link, useParams, useSearchParams } from 'react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'

import DepthLadder from '../components/trading/DepthLadder'
import InlineError from '../components/InlineError'
import OpenOrdersCard from '../components/trading/OpenOrdersCard'
import OrderPanel from '../components/trading/OrderPanel'
import PriceHistoryChart from '../components/trading/PriceHistoryChart'
import Skeleton from '../components/Skeleton'
import TopOfBook from '../components/trading/TopOfBook'
import type { MarketBook } from '../contexts/WebSocketProvider'
import { formatET, outcomeLabel } from '../lib/format'

type ChildPosition = {
  side: 'yes' | 'no'
  quantity: number
  avg_entry_price_cents: number | null
  current_price_cents: number | null
  unrealized_pnl_cents: number | null
}

type ChildMarket = {
  ticker: string
  yes_sub_title: string | null
  market_title: string | null
  status: string
  yes_bid_cents: number | null
  yes_ask_cents: number | null
  no_bid_cents: number | null
  no_ask_cents: number | null
  position: ChildPosition | null
}

type EventDetail = {
  event_ticker: string
  event_title: string | null
  series: string
  open_time: string | null
  close_time: string | null
  bucket: 'live' | 'upcoming' | 'recent'
  markets: ChildMarket[]
}

export default function EventView() {
  const { eventTicker = '' } = useParams<{ eventTicker: string }>()
  const decoded = decodeURIComponent(eventTicker)
  const [searchParams, setSearchParams] = useSearchParams()

  const { data, isPending, isError, error } = useQuery<EventDetail>({
    queryKey: ['event', decoded],
    queryFn: async () => {
      const res = await fetch(`/api/events/${encodeURIComponent(decoded)}`)
      if (!res.ok) {
        const body = await res.text()
        throw new Error(`${res.status}: ${body}`)
      }
      return res.json()
    },
    refetchInterval: 30_000,
  })

  // Pick the active tab: ?market=X if present and valid, else first child.
  const requestedTab = searchParams.get('market')
  const activeTicker = useMemo(() => {
    if (!data?.markets.length) return null
    if (requestedTab && data.markets.some((m) => m.ticker === requestedTab)) {
      return requestedTab
    }
    return data.markets[0].ticker
  }, [data, requestedTab])

  // Keep the URL in sync when we default to the first tab (so refreshes
  // don't change the visible tab if the user lands without ?market=).
  useEffect(() => {
    if (activeTicker && activeTicker !== requestedTab) {
      setSearchParams({ market: activeTicker }, { replace: true })
    }
  }, [activeTicker, requestedTab, setSearchParams])

  return (
    <div className="space-y-4">
      <EventHeader detail={data} decoded={decoded} />

      {isPending && <EventSkeleton />}
      {isError && <InlineError message="Couldn't load this event." detail={error} />}

      {data && activeTicker && (
        <>
          <PositionsStrip
            markets={data.markets}
            activeTicker={activeTicker}
            onPick={(t) => setSearchParams({ market: t })}
          />
          <TabStrip
            markets={data.markets}
            activeTicker={activeTicker}
            onPick={(t) => setSearchParams({ market: t })}
          />
          <MarketPanel ticker={activeTicker} />
        </>
      )}
    </div>
  )
}

function EventHeader({
  detail,
  decoded,
}: {
  detail: EventDetail | undefined
  decoded: string
}) {
  const kickoff = formatET(detail?.open_time)
  return (
    <header className="flex items-start justify-between gap-4">
      <div className="min-w-0">
        <Link to="/" className="text-xs text-text-muted hover:text-text">
          ← Markets
        </Link>
        <h2 className="mt-1 truncate text-lg font-semibold text-text">
          {detail?.event_title ?? decoded}
        </h2>
        {detail && (
          <p className="mt-1 text-xs text-text-muted">
            {kickoff && <>Kickoff {kickoff} · </>}
            {detail.bucket === 'live' ? (
              <span className="text-action">LIVE</span>
            ) : (
              detail.bucket
            )}{' '}
            · {detail.markets.length} markets
          </p>
        )}
      </div>
      <code className="rounded border border-border bg-bg-card px-2 py-1 font-mono text-xs text-text-muted">
        {decoded}
      </code>
    </header>
  )
}

function PositionsStrip({
  markets,
  activeTicker,
  onPick,
}: {
  markets: ChildMarket[]
  activeTicker: string
  onPick: (ticker: string) => void
}) {
  const held = markets.filter((m) => m.position !== null)
  if (held.length === 0) return null
  return (
    <div className="rounded-lg border border-border bg-bg-card p-3">
      <div className="mb-2 text-xs uppercase tracking-wide text-text-muted">
        Your positions on this event
      </div>
      <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {held.map((m) => {
          const p = m.position!
          const pnl = p.unrealized_pnl_cents
          const pnlTone =
            pnl === null ? 'text-text-muted' : pnl >= 0 ? 'text-gain' : 'text-loss'
          return (
            <li key={m.ticker}>
              <button
                type="button"
                onClick={() => onPick(m.ticker)}
                className={`flex w-full items-center justify-between rounded-md border px-3 py-2 text-left ${
                  m.ticker === activeTicker
                    ? 'border-action bg-bg'
                    : 'border-border bg-bg hover:bg-bg-hover'
                }`}
              >
                <div>
                  <div className="text-sm text-text">{outcomeLabel(m.yes_sub_title)}</div>
                  <div className="font-mono text-xs text-text-muted">
                    {p.side.toUpperCase()} {p.quantity} @ {p.avg_entry_price_cents ?? '—'}¢
                  </div>
                </div>
                <div className={`text-right font-mono tabular-nums text-sm ${pnlTone}`}>
                  {pnl === null
                    ? '—'
                    : `${pnl >= 0 ? '+' : ''}$${(pnl / 100).toFixed(2)}`}
                </div>
              </button>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

function TabStrip({
  markets,
  activeTicker,
  onPick,
}: {
  markets: ChildMarket[]
  activeTicker: string
  onPick: (ticker: string) => void
}) {
  return (
    <div className="flex flex-wrap gap-1 border-b border-border">
      {markets.map((m) => {
        const active = m.ticker === activeTicker
        const label = outcomeLabel(m.yes_sub_title) || m.ticker
        return (
          <button
            key={m.ticker}
            type="button"
            onClick={() => onPick(m.ticker)}
            className={`relative -mb-px rounded-t-md border-x border-t px-4 py-2 text-sm ${
              active
                ? 'border-border bg-bg-card text-text'
                : 'border-transparent text-text-muted hover:bg-bg-hover hover:text-text'
            }`}
          >
            <span>{label}</span>
            <span className="ml-2 font-mono text-xs text-text-muted">
              {m.yes_bid_cents ?? '—'}/{m.yes_ask_cents ?? '—'}¢
            </span>
            {m.position && (
              <span className="ml-2 rounded-full bg-action/10 px-2 py-0.5 text-[10px] text-action">
                pos {m.position.quantity}
              </span>
            )}
          </button>
        )
      })}
    </div>
  )
}

function MarketPanel({ ticker }: { ticker: string }) {
  // Reuses the existing trading components — they're already correct per-ticker
  // and read from the WS-fed ['book', ticker] cache. Switching tabs flips
  // the ticker prop; the components pick up the right book and resubscribe.
  const queryClient = useQueryClient()
  const { data: liveBook } = useQuery<MarketBook | undefined>({
    queryKey: ['book', ticker],
    queryFn: () => undefined,
    enabled: false,
  })

  // When a tab activates, ensure the backend WS is subscribed. The event
  // endpoint subscribes all children on first GET, but a long-lived page
  // that re-renders later (e.g. tab switch after the supervisor's tier
  // classifier dropped the ticker) benefits from a defensive re-subscribe
  // via the per-market detail endpoint, which also enrolls SOON tickers.
  useEffect(() => {
    fetch(`/api/markets/${encodeURIComponent(ticker)}`).catch(() => {
      // Read failure is non-fatal; the WS will catch up or the event
      // endpoint's next refresh will re-subscribe.
    })
    // We intentionally don't seed the book cache from this response — the
    // event endpoint already gave us per-ticker top-of-book in the parent
    // query, and the WS deltas are the source of truth from here forward.
    // queryClient access kept to avoid the lint warning about useQuery.
    void queryClient
  }, [ticker, queryClient])

  return (
    <div className="space-y-4">
      <TopOfBook book={liveBook} />
      <div className="grid gap-4 md:grid-cols-2">
        <DepthLadder title="YES depth" side={liveBook?.yes ?? {}} />
        <DepthLadder title="NO depth" side={liveBook?.no ?? {}} />
      </div>
      <PriceHistoryChart ticker={ticker} />
      <div className="grid gap-4 lg:grid-cols-2">
        <OrderPanel ticker={ticker} book={liveBook} />
        <OpenOrdersCard ticker={ticker} />
      </div>
    </div>
  )
}

function EventSkeleton() {
  return (
    <div className="space-y-4">
      <Skeleton height={64} />
      <Skeleton height={44} />
      <div className="grid gap-3 md:grid-cols-2">
        <Skeleton height={108} />
        <Skeleton height={108} />
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <Skeleton height={220} />
        <Skeleton height={220} />
      </div>
      <Skeleton height={260} />
      <div className="grid gap-4 lg:grid-cols-2">
        <Skeleton height={420} />
        <Skeleton height={120} />
      </div>
    </div>
  )
}
