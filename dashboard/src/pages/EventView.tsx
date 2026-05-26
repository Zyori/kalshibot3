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
import { bestAsk, bestBid } from '../lib/book'
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
  league: string | null
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

  // Seed every child's book cache once the event loads. Without this, only
  // the active tab gets a snapshot — non-active tabs show '—/—' until a WS
  // delta happens to mention them, which can be a long wait on quiet markets.
  // We fire all snapshots in parallel; MarketPanel's per-tab seeder skips
  // re-fetching if the cache is already populated.
  const queryClient = useQueryClient()
  useEffect(() => {
    if (!data?.markets) return
    for (const m of data.markets) {
      const existing = queryClient.getQueryData<MarketBook>(['book', m.ticker])
      if (existing && (Object.keys(existing.yes).length || Object.keys(existing.no).length)) {
        continue
      }
      fetch(`/api/markets/${encodeURIComponent(m.ticker)}`)
        .then((res) => (res.ok ? res.json() : null))
        .then((snap: { yes: Array<{ price: number; qty: number }>; no: Array<{ price: number; qty: number }> } | null) => {
          if (!snap) return
          const cur = queryClient.getQueryData<MarketBook>(['book', m.ticker])
          if (cur && (Object.keys(cur.yes).length || Object.keys(cur.no).length)) return
          queryClient.setQueryData<MarketBook>(['book', m.ticker], {
            ticker: m.ticker,
            yes: Object.fromEntries(snap.yes.map((l) => [l.price, l.qty])),
            no: Object.fromEntries(snap.no.map((l) => [l.price, l.qty])),
          })
        })
        .catch(() => {})
    }
  }, [data?.markets, queryClient])

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
        {detail?.league && (
          <div className="mt-1 text-xs font-semibold uppercase tracking-wide text-action">
            {detail.league}
          </div>
        )}
        <h2 className="mt-0.5 truncate text-lg font-semibold text-text">
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
      {markets.map((m) => (
        <TabButton
          key={m.ticker}
          market={m}
          active={m.ticker === activeTicker}
          onClick={() => onPick(m.ticker)}
        />
      ))}
    </div>
  )
}

function TabButton({
  market: m,
  active,
  onClick,
}: {
  market: ChildMarket
  active: boolean
  onClick: () => void
}) {
  // Per-tab top-of-book from the live cache so the labels tick in real time.
  // Falls back to the event-endpoint snapshot if the cache hasn't been seeded
  // yet for this child (e.g. before the user has visited that tab).
  const queryClient = useQueryClient()
  const { data: book } = useQuery<MarketBook | undefined>({
    queryKey: ['book', m.ticker],
    queryFn: () => undefined,
    enabled: false,
  })
  void queryClient
  const bid = bestBid(book, 'yes') ?? m.yes_bid_cents
  const ask = bestAsk(book, 'yes') ?? m.yes_ask_cents
  const label = outcomeLabel(m.yes_sub_title) || m.ticker
  return (
    <button
      type="button"
      onClick={onClick}
      className={`relative -mb-px rounded-t-md border-x border-t px-4 py-2 text-sm ${
        active
          ? 'border-border bg-bg-card text-text'
          : 'border-transparent text-text-muted hover:bg-bg-hover hover:text-text'
      }`}
    >
      <span>{label}</span>
      <span className="ml-2 font-mono text-xs text-text-muted">
        {bid ?? '—'}/{ask ?? '—'}¢
      </span>
      {m.position && (
        <span className="ml-2 rounded-full bg-action/10 px-2 py-0.5 text-[10px] text-action">
          pos {m.position.quantity}
        </span>
      )}
    </button>
  )
}

type MarketDetailResponse = {
  ticker: string
  yes: Array<{ price: number; qty: number }>
  no: Array<{ price: number; qty: number }>
}

function MarketPanel({ ticker }: { ticker: string }) {
  // Reuses the existing trading components, which read from the
  // ['book', ticker] cache that WebSocketProvider keeps fresh via deltas.
  // Switching tabs flips the ticker prop and we re-seed below.
  const queryClient = useQueryClient()
  const { data: liveBook } = useQuery<MarketBook | undefined>({
    queryKey: ['book', ticker],
    queryFn: () => undefined,
    enabled: false,
  })

  // On tab activation, fetch the full per-market snapshot so the depth
  // ladder + top-of-book have data immediately — without this, tabs that
  // haven't received a WS snapshot yet render blank until the next delta
  // happens to mention them. The fetch also enrolls the ticker for WS
  // subscription if it wasn't already (the route side-effects that).
  const snapshot = useQuery<MarketDetailResponse>({
    queryKey: ['market_snapshot', ticker],
    queryFn: async () => {
      const res = await fetch(`/api/markets/${encodeURIComponent(ticker)}`)
      if (!res.ok) throw new Error(`/api/markets: ${res.status}`)
      return res.json()
    },
    // Snapshot once per tab activation; WS deltas keep it fresh after.
    staleTime: Infinity,
  })

  // Seed the live-book cache from the snapshot if nothing's there yet.
  // Don't clobber a fresher cache (e.g. WS deltas that beat the REST
  // round-trip).
  useEffect(() => {
    if (!snapshot.data) return
    const existing = queryClient.getQueryData<MarketBook>(['book', ticker])
    if (existing && (Object.keys(existing.yes).length || Object.keys(existing.no).length)) {
      return
    }
    queryClient.setQueryData<MarketBook>(['book', ticker], {
      ticker,
      yes: Object.fromEntries(snapshot.data.yes.map((l) => [l.price, l.qty])),
      no: Object.fromEntries(snapshot.data.no.map((l) => [l.price, l.qty])),
    })
  }, [snapshot.data, ticker, queryClient])

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
