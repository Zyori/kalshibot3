import { Link, useParams } from 'react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'

import DepthLadder from '../components/trading/DepthLadder'
import OpenOrdersCard from '../components/trading/OpenOrdersCard'
import OrderPanel from '../components/trading/OrderPanel'
import PriceHistoryChart from '../components/trading/PriceHistoryChart'
import TopOfBook from '../components/trading/TopOfBook'
import type { MarketBook } from '../contexts/WebSocketProvider'
import { formatET, outcomeLabel } from '../lib/format'

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
  event_title: string | null
  market_title: string | null
  yes_sub_title: string | null
  open_time: string | null
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
      <MarketHeader decoded={decoded} detail={detail} />

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

/**
 * Header for the market detail page. Left side reads naturally
 * ("Saint-Etienne vs Nice — Saint-Etienne Wins"); right side keeps the
 * machine ticker visible with a one-click copy button so power users
 * can still grab the underlying identifier.
 */
function MarketHeader({
  decoded,
  detail,
}: {
  decoded: string
  detail: MarketDetail | undefined
}) {
  const [copied, setCopied] = useState(false)

  const eventTitle = detail?.event_title
  const outcome = outcomeLabel(detail?.yes_sub_title)
  const kickoff = formatET(detail?.open_time)

  const copyTicker = async () => {
    try {
      await navigator.clipboard.writeText(decoded)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // Clipboard API unavailable (e.g. insecure context). Silent — the
      // ticker is still on-screen for manual copy.
    }
  }

  return (
    <header className="flex items-start justify-between gap-4">
      <div className="min-w-0">
        <Link to="/" className="text-xs text-text-muted hover:text-text">
          ← Markets
        </Link>
        <h2 className="mt-1 truncate text-lg font-semibold text-text">
          {eventTitle ?? decoded}
        </h2>
        {outcome && (
          <div className="mt-0.5 text-sm text-text-muted">{outcome}</div>
        )}
        {detail && (
          <p className="mt-1 text-xs text-text-muted">
            {kickoff && <>Kickoff {kickoff} · </>}
            Status: {detail.status}
            {detail.last_update_ago_s !== null && (
              <span> · book updated {detail.last_update_ago_s.toFixed(1)}s ago</span>
            )}
          </p>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <code className="rounded border border-border bg-bg-card px-2 py-1 font-mono text-xs text-text-muted">
          {decoded}
        </code>
        <button
          type="button"
          onClick={copyTicker}
          className="rounded border border-border bg-bg-card px-2 py-1 text-xs text-text-muted hover:bg-bg-hover hover:text-text"
          title="Copy ticker"
        >
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>
    </header>
  )
}
