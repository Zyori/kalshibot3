import { createContext, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'

/**
 * WebSocket context. ONE connection per browser tab, regardless of how many
 * components want to read state.
 *
 * Why context, not hook: each render of a hook gets a new closure. If
 * `useWebSocket()` were a hook, each consuming component would try to open
 * its own connection. A context provider establishes the connection once at
 * the app root.
 *
 * What we do with events: apply directly to the TanStack Query cache via
 * `setQueryData`. We never `invalidateQueries` for hot data — that would
 * trigger a REST refetch on every WS tick. The cache is the live store;
 * REST is the cold bootstrap.
 */

// === Wire-format payloads from /ws ===
//
// Mirrors src/core/ws_manager.py _serialize(). Keep this in sync with the
// backend — any divergence means events get dropped silently here.

// Full current book for one market, computed server-side from the
// authoritative (guarded) LiveState book. The browser stores this verbatim —
// it does NOT reconstruct a book from deltas. Mirrors MarketBook.to_wire() on
// the backend and the /api/markets/{ticker} REST shape; one book shape for
// cold load and live updates.
type BookEvent = {
  type: 'book'
  ticker: string
  yes: Array<{ price: number; qty: number }>
  no: Array<{ price: number; qty: number }>
  yes_bid_cents: number | null
  yes_ask_cents: number | null
  no_bid_cents: number | null
  no_ask_cents: number | null
}

type FillEvent = {
  type: 'fill'
  trade_id: string
  order_id: string
  ticker: string
  side: 'yes' | 'no'
  action: 'buy' | 'sell'
  count: number
  yes_price: number
  no_price: number
}

type UserOrderEvent = {
  type: 'user_order'
  order_id: string
  client_order_id: string | null
  ticker: string
  side: 'yes' | 'no'
  status: 'resting' | 'canceled' | 'executed' | 'pending'
  price_cents: number | null
  remaining_count: number
}

type MarketLifecycleEvent = {
  type: 'market_lifecycle'
  ticker: string
  status: string
  settlement_value: number | null
}

// Server-derived signal: a position reconciliation just committed. Carries no
// payload — it's a "refetch your position-bearing queries now" nudge, emitted
// post-commit so the refetch reads fresh DB state.
type PositionSyncedEvent = {
  type: 'position_synced'
}

// The AI partner staged (or dismissed) a suggestion. Discrete event — we
// invalidate the suggestions query, matching the position_synced pattern,
// rather than threading the row through the hot-data cache.
type SuggestionEvent = {
  type: 'suggestion'
  suggestion_id: number
  kind?: 'entry' | 'exit'
  ticker?: string
  dismissed?: boolean
}

// A passive threshold nudge ("worth a look — ask the partner?"). Push-only and
// ephemeral — there's no GET to invalidate, so unlike `suggestion` this one
// appends into a client-held cache via setQueryData.
type NudgeEvent = {
  type: 'nudge'
  subject: string
  trigger: 'profit_50' | 'clock_75' | 'red_card'
  label: string
}

type WsEvent =
  | BookEvent
  | FillEvent
  | UserOrderEvent
  | MarketLifecycleEvent
  | PositionSyncedEvent
  | SuggestionEvent
  | NudgeEvent

type WsPayload = { events: WsEvent[] }

// === Cache-side data shapes ===
//
// What TanStack stores under each query key. The components read from
// these via useQuery(['book', ticker]) etc.

export type BookSide = Record<number, number>
"price_cents → whole-contract quantity. Server-rounded (MarketBook.to_wire); empty levels are already absent."

export type MarketBook = {
  ticker: string
  yes: BookSide
  no: BookSide
}

export type OpenOrder = {
  order_id: string
  client_order_id: string | null
  ticker: string
  side: 'yes' | 'no'
  status: 'resting' | 'canceled' | 'executed' | 'pending'
  // Price in the held side's own frame (a NO order's NO price), already
  // un-inverted from Kalshi's YES-book read-back by the backend.
  price_cents: number | null
  remaining_count: number
}

export type ActiveNudge = {
  subject: string
  trigger: 'profit_50' | 'clock_75' | 'red_card'
  label: string
}

export type WsStatus = 'connecting' | 'open' | 'closed'

const queryKeyForBook = (ticker: string) => ['book', ticker] as const

const queryKeyForNudges = ['nudges'] as const

const queryKeyForOpenOrders = ['open_orders'] as const

// === Context ===

type WsContextValue = {
  status: WsStatus
}

const WsContext = createContext<WsContextValue>({ status: 'connecting' })

export function useWebSocketStatus(): WsStatus {
  return useContext(WsContext).status
}

// === Provider ===

const RECONNECT_BASE_MS = 500
const RECONNECT_MAX_MS = 10_000

export function WebSocketProvider({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient()
  const [status, setStatus] = useState<WsStatus>('connecting')
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectAttempts = useRef(0)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true

    const connect = () => {
      // Use the page's origin so nginx routes ws → backend the same way it
      // routes /api. wss in prod, ws on localhost dev.
      const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
      const url = `${proto}://${window.location.host}/ws`

      setStatus('connecting')
      const ws = new WebSocket(url)
      wsRef.current = ws

      ws.onopen = () => {
        reconnectAttempts.current = 0
        setStatus('open')
      }

      ws.onmessage = (e) => {
        try {
          const payload = JSON.parse(e.data) as WsPayload
          for (const event of payload.events) {
            applyEvent(queryClient, event)
          }
        } catch (err) {
          // Bad JSON or schema mismatch — log but don't tear down the connection.
          // eslint-disable-next-line no-console
          console.warn('ws message parse failed', err)
        }
      }

      ws.onclose = () => {
        setStatus('closed')
        if (!mounted.current) return
        const delay = Math.min(
          RECONNECT_BASE_MS * 2 ** reconnectAttempts.current,
          RECONNECT_MAX_MS,
        )
        reconnectAttempts.current += 1
        reconnectTimer.current = setTimeout(connect, delay)
      }

      ws.onerror = () => {
        // onclose will follow.
      }
    }

    connect()

    return () => {
      mounted.current = false
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
      wsRef.current?.close()
      wsRef.current = null
    }
    // queryClient is stable; intentionally not in deps.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const value = useMemo(() => ({ status }), [status])
  return <WsContext.Provider value={value}>{children}</WsContext.Provider>
}

// === Event application ===

function applyEvent(qc: ReturnType<typeof useQueryClient>, event: WsEvent) {
  switch (event.type) {
    case 'book': {
      // Full current book from the server's authoritative LiveState. Store it
      // verbatim — overwrite, never merge. The browser does NO delta math: the
      // backend already applied every update to a guarded book (crossed/locked
      // detection + REST resync) and sends us the result. This is the single
      // source of truth; the old client-side delta reconstruction is gone, and
      // with it the crossed/empty-book bug it caused on lopsided markets.
      const yes: BookSide = {}
      const no: BookSide = {}
      for (const l of event.yes) yes[l.price] = l.qty
      for (const l of event.no) no[l.price] = l.qty
      qc.setQueryData<MarketBook>(queryKeyForBook(event.ticker), {
        ticker: event.ticker,
        yes,
        no,
      })
      return
    }
    case 'user_order': {
      qc.setQueryData<Record<string, OpenOrder>>(queryKeyForOpenOrders, (prev) => {
        const next = { ...(prev ?? {}) }
        if (event.status === 'canceled' || event.status === 'executed') {
          delete next[event.order_id]
        } else {
          next[event.order_id] = {
            order_id: event.order_id,
            client_order_id: event.client_order_id,
            ticker: event.ticker,
            side: event.side,
            status: event.status,
            price_cents: event.price_cents,
            remaining_count: event.remaining_count,
          }
        }
        return next
      })
      return
    }
    case 'fill': {
      // Fills land in the BET ledger via the backend. Invalidate the ledger
      // here (cold-bootstrap data, not hot) so the user sees their fill
      // immediately. Position-bearing queries are NOT refetched here — a fill
      // triggers a backend position sync, and we refetch those on the
      // `position_synced` signal it emits, which fires post-commit. Refetching
      // on `fill` would race that sync and read pre-fill state.
      qc.invalidateQueries({ queryKey: ['ledger'] })
      qc.invalidateQueries({ queryKey: ['ledger_stats'] })
      return
    }
    case 'position_synced': {
      // A position reconciliation committed. Refetch everything that carries
      // position data: the event page (markets[].position lives in ['event']),
      // the positions list, and the ledger list + stats (the stats strip,
      // P&L graph, and strategy breakdown all derive from closed bets, so a
      // close must refresh them too — they're a separate query key from the
      // ['ledger'] list and don't invalidate with it).
      qc.invalidateQueries({ queryKey: ['event'] })
      qc.invalidateQueries({ queryKey: ['positions'] })
      qc.invalidateQueries({ queryKey: ['ledger'] })
      qc.invalidateQueries({ queryKey: ['ledger_stats'] })
      return
    }
    case 'market_lifecycle': {
      // Surface status changes by invalidating the market-detail query.
      // Cheap, fires rarely.
      qc.invalidateQueries({ queryKey: ['market', event.ticker] })
      return
    }
    case 'suggestion': {
      // A suggestion was staged or dismissed. Discrete, low-frequency —
      // invalidate the cold-load query so the cards refetch (same pattern as
      // position_synced). Never setQueryData here; this isn't hot book data.
      qc.invalidateQueries({ queryKey: ['suggestions'] })
      return
    }
    case 'nudge': {
      // Push-only ephemeral chip — no GET to invalidate, so append into the
      // client cache (de-duped by subject+trigger; a re-fire replaces). This is
      // the one discrete event that legitimately uses setQueryData: there's no
      // server list to refetch.
      qc.setQueryData<ActiveNudge[]>(queryKeyForNudges, (prev) => {
        const next = (prev ?? []).filter(
          (n) => !(n.subject === event.subject && n.trigger === event.trigger),
        )
        next.push({ subject: event.subject, trigger: event.trigger, label: event.label })
        return next
      })
      return
    }
  }
}
