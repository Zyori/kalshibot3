import { Link, useParams } from 'react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'

import DepthLadder from '../components/trading/DepthLadder'
import OpenOrdersCard from '../components/trading/OpenOrdersCard'
import OrderPanel from '../components/trading/OrderPanel'
import PriceHistoryChart from '../components/trading/PriceHistoryChart'
import TopOfBook from '../components/trading/TopOfBook'
import type { MarketBook } from '../contexts/WebSocketProvider'

type MarketDetail = {
  ticker: string
  status: string
  yes: Array<{ price: number; qty: number }>
  no: Array<{ price: number; qty: number }>
  yes_best_bid: number | null
  yes_best_ask: number | null
  no_best_bid: number | null
  no_best_ask: number | null
  last_update_ago_s: number | null
}

/**
 * One market's live view. Top-of-book + depth ladder + price history chart.
 *
 * Data flow:
 *   1. On mount, fetch /api/markets/{ticker} once — this also asks the
 *      backend to start subscribing to the WS orderbook for this ticker.
 *   2. The REST response is seeded into ['book', ticker] so the page has
 *      something to render immediately.
 *   3. After that, WebSocketProvider streams orderbook_delta events into
 *      the same cache key. Components below read straight from the cache
 *      and rerender automatically.
 */
export default function MarketView() {
  const { ticker = '' } = useParams<{ ticker: string }>()
  const decoded = decodeURIComponent(ticker)
  const queryClient = useQueryClient()

  // Cold bootstrap.
  const { data: detail, isPending, isError, error } = useQuery<MarketDetail>({
    queryKey: ['market', decoded],
    queryFn: async () => {
      const res = await fetch(`/api/markets/${encodeURIComponent(decoded)}`)
      if (!res.ok) {
        const body = await res.text()
        throw new Error(`${res.status}: ${body}`)
      }
      return res.json()
    },
    // Refetch on a longer cadence as a backstop in case a WS message was
    // dropped between snapshot and delta. Deltas are the primary feed.
    refetchInterval: 60_000,
  })

  // Seed the book cache so the first paint shows actual liquidity instead
  // of waiting for the first WS message. WS updates supersede this.
  useEffect(() => {
    if (!detail) return
    const seed: MarketBook = {
      ticker: detail.ticker,
      yes: Object.fromEntries(detail.yes.map((l) => [l.price, l.qty])),
      no: Object.fromEntries(detail.no.map((l) => [l.price, l.qty])),
    }
    // Only seed if there's nothing in the cache yet — don't clobber WS
    // updates that arrived faster than the REST round-trip.
    const existing = queryClient.getQueryData<MarketBook>(['book', decoded])
    if (!existing) {
      queryClient.setQueryData(['book', decoded], seed)
    }
  }, [detail, decoded, queryClient])

  // Read from the live cache.
  const liveBook = queryClient.getQueryData<MarketBook>(['book', decoded])

  return (
    <div className="space-y-4">
      <header className="flex items-baseline justify-between">
        <div>
          <Link to="/" className="text-xs text-text-muted hover:text-text">
            ← Markets
          </Link>
          <h2 className="mt-1 font-mono text-base text-text">{decoded}</h2>
          {detail && (
            <p className="mt-1 text-xs text-text-muted">
              Status: {detail.status}
              {detail.last_update_ago_s !== null && (
                <span> · last book update {detail.last_update_ago_s.toFixed(1)}s ago</span>
              )}
            </p>
          )}
        </div>
      </header>

      {isPending && <Box>Loading…</Box>}
      {isError && <Box tone="loss">{String(error)}</Box>}

      {detail && (
        <>
          <TopOfBook book={liveBook} />

          <div className="grid gap-4 md:grid-cols-2">
            <DepthLadder title="YES depth" side={liveBook?.yes ?? {}} />
            <DepthLadder title="NO depth"  side={liveBook?.no  ?? {}} />
          </div>

          <PriceHistoryChart ticker={decoded} />

          <div className="grid gap-4 lg:grid-cols-2">
            <OrderPanel ticker={decoded} book={liveBook} />
            <OpenOrdersCard ticker={decoded} />
          </div>
        </>
      )}
    </div>
  )
}

function Box({
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
