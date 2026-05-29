/**
 * EventView — one page per event, Kalshi-style.
 *
 * Layout (top → bottom):
 *   1. MatchHeader — live score, in-game stats, last event.
 *   2. CombinedPriceChart — all child markets on one axis, color coded.
 *   3. MarketCard list — each child collapsible; click to expand the
 *      full trading surface (top-of-book, depth, order panel, orders).
 *
 * Default expansion:
 *   - ?market=X param expands market X (deep-link target from Ledger/etc).
 *   - Otherwise, any market the user has a position on auto-expands.
 *   - Otherwise the favorite (highest YES bid) auto-expands.
 *
 * Multiple cards can be open at once; user toggles freely.
 */

import { useEffect, useMemo, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'

import CombinedPriceChart from '../components/event/CombinedPriceChart'
import InlineError from '../components/InlineError'
import MarketCard from '../components/event/MarketCard'
import MatchHeader from '../components/event/MatchHeader'
import Skeleton from '../components/Skeleton'
import type { MarketBook } from '../contexts/WebSocketProvider'
import type { ChildMarket, EventDetail } from '../lib/types'

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

  // Seed every child's book cache on event load so collapsed cards show
  // a real top-of-book in their header (without the user having to expand
  // each one to trigger a snapshot). Always overwrite from REST — the
  // server runs a locked-book resync before responding.
  const queryClient = useQueryClient()
  useEffect(() => {
    if (!data?.markets) return
    for (const m of data.markets) {
      fetch(`/api/markets/${encodeURIComponent(m.ticker)}`)
        .then((res) => (res.ok ? res.json() : null))
        .then(
          (
            snap:
              | { yes: Array<{ price: number; qty: number }>; no: Array<{ price: number; qty: number }> }
              | null,
          ) => {
            if (!snap) return
            // Seed only when the cache is empty. If WS deltas have already
            // populated this book, the producer no-ops — a REST snapshot
            // (rounded ints) must not clobber the live exact-float WS state.
            // The WS snapshot handler, which IS authoritative, still overwrites
            // unconditionally. Frontend counterpart to the backend ws_owned guard.
            queryClient.setQueryData<MarketBook>(
              ['book', m.ticker],
              (prev) =>
                prev ?? {
                  ticker: m.ticker,
                  yes: Object.fromEntries(snap.yes.map((l) => [l.price, l.qty])),
                  no: Object.fromEntries(snap.no.map((l) => [l.price, l.qty])),
                },
            )
          },
        )
        .catch(() => {})
    }
  }, [data?.markets, queryClient])

  const initialOpen = useMemo(
    () => initialOpenTickers(data?.markets ?? [], searchParams.get('market')),
    [data?.markets, searchParams],
  )

  // Local state for which cards are open. Initialized from the URL +
  // position/favorite heuristic; user toggles freely after that.
  const [openTickers, setOpenTickers] = useState<Set<string>>(initialOpen)

  // Whenever the underlying initialOpen set changes (event loaded fresh,
  // URL param flipped), reseed. Don't fight the user mid-session.
  useEffect(() => {
    setOpenTickers(initialOpen)
  }, [initialOpen])

  const toggle = (ticker: string) => {
    setOpenTickers((prev) => {
      const next = new Set(prev)
      if (next.has(ticker)) next.delete(ticker)
      else next.add(ticker)
      return next
    })
    // Update URL so deep-links / refresh keep this card open as the
    // "primary" target. We only carry one ticker in the query for
    // simplicity — first-open wins.
    setSearchParams({ market: ticker }, { replace: true })
  }

  return (
    <div className="space-y-4">
      <div>
        <Link to="/" className="text-xs text-text-muted hover:text-text">
          ← Markets
        </Link>
      </div>

      <MatchHeader detail={data} decoded={decoded} />

      {isPending && <EventSkeleton />}
      {isError && <InlineError message="Couldn't load this event." detail={error} />}

      {data && data.markets.length > 0 && (
        <>
          <CombinedPriceChart markets={data.markets} />
          <div className="space-y-2">
            {data.markets.map((m, i) => (
              <MarketCard
                key={m.ticker}
                market={m}
                colorIndex={i}
                expanded={openTickers.has(m.ticker)}
                onToggle={() => toggle(m.ticker)}
              />
            ))}
          </div>
        </>
      )}
    </div>
  )
}

function initialOpenTickers(
  markets: ChildMarket[],
  requested: string | null,
): Set<string> {
  const open = new Set<string>()
  if (markets.length === 0) return open
  // Priority 1: ?market=X if it matches a child.
  if (requested && markets.some((m) => m.ticker === requested)) {
    open.add(requested)
  }
  // Priority 2: every market the user has a position on.
  for (const m of markets) {
    if (m.position) open.add(m.ticker)
  }
  // Priority 3: if nothing else opened, pick the favorite (highest YES bid).
  if (open.size === 0) {
    let best = markets[0]
    let bestPrice = best.yes_bid_cents ?? -1
    for (const m of markets.slice(1)) {
      const p = m.yes_bid_cents ?? -1
      if (p > bestPrice) {
        best = m
        bestPrice = p
      }
    }
    open.add(best.ticker)
  }
  return open
}

function EventSkeleton() {
  return (
    <div className="space-y-4">
      <Skeleton height={120} />
      <Skeleton height={280} />
      <div className="space-y-2">
        <Skeleton height={56} />
        <Skeleton height={56} />
        <Skeleton height={56} />
      </div>
    </div>
  )
}
