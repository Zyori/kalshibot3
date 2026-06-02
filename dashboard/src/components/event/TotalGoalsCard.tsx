/**
 * TotalGoalsCard — the per-game Over/Under goals ladder, as its own block on the
 * event page. Deliberately separate from the moneyline MarketCards and NEVER on
 * the price chart: total goals are a different question (how many goals) than
 * the 3-way result (who wins), and graphing them together would be nonsense.
 *
 * Each rung (Over 1.5/2.5/3.5/4.5) shows its live YES price and expands to a
 * full OrderPanel for that market — tradeable like any other, routed through the
 * same confirm-then-place path.
 */
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'

import type { MarketBook } from '../../contexts/WebSocketProvider'
import type { TotalGoal } from '../../lib/types'
import { bestAsk, bestBid } from '../../lib/book'
import OrderPanel from '../trading/OrderPanel'

export default function TotalGoalsCard({ totals }: { totals: TotalGoal[] }) {
  // Only show rungs that are still tradeable (active). A finalized/closed rung
  // (e.g. Over 1.5 once the 2nd goal is in) is noise on a live game.
  const tradeable = totals.filter((t) => t.status === 'active' || t.status === 'initialized')
  if (tradeable.length === 0) return null

  return (
    <div className="rounded-lg border border-border bg-bg-card p-4">
      <div className="mb-3 flex items-baseline justify-between">
        <h3 className="text-sm font-semibold text-text">Total goals</h3>
        <span className="text-[10px] text-text-muted">Over/Under · separate from the result</span>
      </div>
      <ul className="space-y-1.5">
        {tradeable.map((t) => (
          <TotalGoalRow key={t.ticker} total={t} />
        ))}
      </ul>
    </div>
  )
}

function TotalGoalRow({ total: t }: { total: TotalGoal }) {
  const [expanded, setExpanded] = useState(false)
  return (
    <li className="rounded-md border border-border bg-bg">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center justify-between px-3 py-2 text-left text-xs hover:bg-bg-hover"
      >
        <span className="font-medium text-text">Over {t.threshold.toFixed(1)} goals</span>
        <span className="flex items-center gap-3">
          <TotalQuote total={t} />
          <span className="text-[10px] text-text-muted">{expanded ? '−' : '+'}</span>
        </span>
      </button>
      {expanded && <TotalGoalBody ticker={t.ticker} snapshot={t} />}
    </li>
  )
}

function TotalQuote({ total: t }: { total: TotalGoal }) {
  const { data: book } = useQuery<MarketBook | undefined>({
    queryKey: ['book', t.ticker],
    queryFn: () => undefined,
    enabled: false,
  })
  const bid = bestBid(book, 'yes') ?? t.yes_bid_cents
  const ask = bestAsk(book, 'yes') ?? t.yes_ask_cents
  return (
    <span className="font-mono tabular-nums text-text">
      <span className="text-text-muted">{bid ?? '—'}</span>
      <span className="mx-0.5 text-text-muted">/</span>
      <span>{ask ?? '—'}¢</span>
    </span>
  )
}

function TotalGoalBody({ ticker, snapshot }: { ticker: string; snapshot: TotalGoal }) {
  const queryClient = useQueryClient()
  const { data: book } = useQuery<MarketBook | undefined>({
    queryKey: ['book', ticker],
    queryFn: () => undefined,
    enabled: false,
  })
  // Seed the cache from the event-endpoint snapshot so the panel opens with a
  // real top-of-book. Totals are NOT WS-subscribed (that would leak subscriptions
  // the tier dispatcher can't prune), so this REST snapshot is the price source —
  // it refreshes on each event-page load, not live-tick. Enough for the panel's
  // auto-follow + placing a limit; order placement doesn't need a live book.
  if (book === undefined && (snapshot.yes_bid_cents !== null || snapshot.yes_ask_cents !== null)) {
    queryClient.setQueryData<MarketBook>(['book', ticker], {
      ticker,
      yes: snapshot.yes_bid_cents !== null ? { [snapshot.yes_bid_cents]: 1 } : {},
      no: snapshot.no_bid_cents !== null ? { [snapshot.no_bid_cents]: 1 } : {},
    })
  }
  return (
    <div className="border-t border-border p-3">
      <OrderPanel ticker={ticker} book={book} />
    </div>
  )
}
