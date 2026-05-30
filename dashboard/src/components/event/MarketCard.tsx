/**
 * One collapsible card per child market. Header shows outcome label +
 * top-of-book + a position pill if the user is holding. Click to expand —
 * inside is the full trading surface (top-of-book, depth ladders, order
 * panel, open orders).
 *
 * Several cards can be open at once. The caller controls open-state via
 * `expanded` + `onToggle`; defaults are wired in EventView (favorite
 * auto-opens, the user's held market auto-opens).
 */
import { useEffect } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'

import DepthLadder from '../trading/DepthLadder'
import OpenOrdersCard from '../trading/OpenOrdersCard'
import OrderPanel from '../trading/OrderPanel'
import TopOfBook from '../trading/TopOfBook'
import type { MarketBook } from '../../contexts/WebSocketProvider'
import { bestAsk, bestBid } from '../../lib/book'
import { formatPriceCents, outcomeLabel } from '../../lib/format'
import type { ChildMarket } from '../../lib/types'

// Match CombinedPriceChart.COLORS — green / red / blue / amber / purple / cyan.
const COLOR_DOTS = ['bg-gain', 'bg-loss', 'bg-blue-500', 'bg-action', 'bg-purple-500', 'bg-cyan-500']

export default function MarketCard({
  market,
  expanded,
  onToggle,
  colorIndex,
}: {
  market: ChildMarket
  expanded: boolean
  onToggle: () => void
  colorIndex: number
}) {
  const label = outcomeLabel(market.yes_sub_title) || market.ticker
  return (
    <section className="overflow-hidden rounded-lg border border-border bg-bg-card">
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left hover:bg-bg-hover"
        aria-expanded={expanded}
      >
        <div className="flex min-w-0 items-center gap-3">
          <span
            aria-hidden
            className={`h-2.5 w-2.5 rounded-full ${COLOR_DOTS[colorIndex % COLOR_DOTS.length]}`}
          />
          <span className="truncate text-sm font-medium text-text">{label}</span>
          {market.position && <PositionPill market={market} />}
        </div>
        <div className="flex shrink-0 items-center gap-3 text-xs">
          <Quote market={market} />
          <span className="text-text-muted" aria-hidden>
            {expanded ? '▾' : '▸'}
          </span>
        </div>
      </button>
      {expanded && <ExpandedBody ticker={market.ticker} />}
    </section>
  )
}

function PositionPill({ market }: { market: ChildMarket }) {
  const p = market.position!
  const pnl = p.unrealized_pnl_cents
  const tone =
    pnl === null ? 'text-text-muted' : pnl >= 0 ? 'text-gain' : 'text-loss'
  return (
    <span className="flex items-center gap-2 rounded-full bg-action/10 px-2 py-0.5 text-[11px] text-action">
      <span className="font-mono tabular-nums">
        {p.side.toUpperCase()} {p.quantity} @ {formatPriceCents(p.avg_entry_price ?? p.avg_entry_price_cents)}
      </span>
      {pnl !== null && (
        <span className={`font-mono tabular-nums ${tone}`}>
          {pnl >= 0 ? '+' : ''}${(pnl / 100).toFixed(2)}
        </span>
      )}
    </span>
  )
}

function Quote({ market }: { market: ChildMarket }) {
  // Top-of-book direct from the live cache (ticks in real time). Falls
  // back to the event-endpoint snapshot until a delta touches this ticker.
  const { data: book } = useQuery<MarketBook | undefined>({
    queryKey: ['book', market.ticker],
    queryFn: () => undefined,
    enabled: false,
  })
  const bid = bestBid(book, 'yes') ?? market.yes_bid_cents
  const ask = bestAsk(book, 'yes') ?? market.yes_ask_cents
  return (
    <span className="font-mono tabular-nums text-text">
      <span className="text-text-muted">{bid ?? '—'}</span>
      <span className="mx-0.5 text-text-muted">/</span>
      <span>{ask ?? '—'}¢</span>
    </span>
  )
}

type MarketDetailResponse = {
  ticker: string
  yes: Array<{ price: number; qty: number }>
  no: Array<{ price: number; qty: number }>
}

function ExpandedBody({ ticker }: { ticker: string }) {
  // Snapshot-on-expand: hit /api/markets for a fresh book to seed the cache
  // when empty, then let WS deltas keep it fresh. Seeding is guarded below so
  // a re-expand can't clobber a live WS book.
  const queryClient = useQueryClient()
  const { data: liveBook } = useQuery<MarketBook | undefined>({
    queryKey: ['book', ticker],
    queryFn: () => undefined,
    enabled: false,
  })
  const snapshot = useQuery<MarketDetailResponse>({
    queryKey: ['market_snapshot', ticker],
    queryFn: async () => {
      const res = await fetch(`/api/markets/${encodeURIComponent(ticker)}`)
      if (!res.ok) throw new Error(`/api/markets: ${res.status}`)
      return res.json()
    },
    staleTime: Infinity,
  })
  useEffect(() => {
    if (!snapshot.data) return
    // Seed only when the cache is empty. The card re-fetches on every
    // expand/collapse; without this guard a re-expand would clobber the live
    // exact-float WS book with rounded REST ints. Producer no-ops once WS owns
    // the cache. Frontend counterpart to the backend ws_owned guard.
    queryClient.setQueryData<MarketBook>(
      ['book', ticker],
      (prev) =>
        prev ?? {
          ticker,
          yes: Object.fromEntries(snapshot.data.yes.map((l) => [l.price, l.qty])),
          no: Object.fromEntries(snapshot.data.no.map((l) => [l.price, l.qty])),
        },
    )
  }, [snapshot.data, ticker, queryClient])

  return (
    <div className="space-y-4 border-t border-border bg-bg p-4">
      <TopOfBook book={liveBook} />
      <div className="grid gap-4 md:grid-cols-2">
        <DepthLadder title="YES depth" side={liveBook?.yes ?? {}} />
        <DepthLadder title="NO depth" side={liveBook?.no ?? {}} />
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <OrderPanel ticker={ticker} book={liveBook} />
        <OpenOrdersCard ticker={ticker} />
      </div>
    </div>
  )
}
