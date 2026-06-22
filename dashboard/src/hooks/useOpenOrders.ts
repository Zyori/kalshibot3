import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import type { OpenOrder } from '../contexts/WebSocketProvider'

type OrdersResponse = {
  orders: Array<{
    order_id: string
    client_order_id: string | null
    ticker: string
    side: 'yes' | 'no'
    status: string
    price_cents: number | null
    remaining_count: number
  }>
}

/**
 * Open orders, keyed by order_id. Two sources:
 *
 *  - REST bootstrap (/api/orders?status=resting) — source of truth on
 *    every page load. Without this the page would only show orders placed
 *    in the current browser session; reloading after a placement would
 *    leave the user blind.
 *  - WS user_order events (via WebSocketProvider) — live updates between
 *    REST polls so cancels/fills reflect immediately.
 *
 * Periodic refetch every 15s as backstop in case a WS message dropped.
 */
export function useOpenOrders(filterTicker?: string) {
  const queryClient = useQueryClient()

  const bootstrap = useQuery<OrdersResponse>({
    queryKey: ['open_orders_rest'],
    queryFn: async () => {
      const res = await fetch('/api/orders?status=resting')
      if (!res.ok) throw new Error(`/api/orders: ${res.status}`)
      return res.json()
    },
    refetchInterval: 15_000,
  })

  // Merge REST result into the WS-fed cache. WS deltas can arrive faster
  // than the REST round-trip; only fill in entries that aren't already
  // there (don't clobber a fresher WS update with a stale REST snapshot).
  useEffect(() => {
    if (!bootstrap.data) return
    queryClient.setQueryData<Record<string, OpenOrder>>(
      ['open_orders'],
      (prev) => {
        const next: Record<string, OpenOrder> = { ...(prev ?? {}) }
        const seen = new Set<string>()
        for (const o of bootstrap.data!.orders) {
          seen.add(o.order_id)
          if (!next[o.order_id]) {
            next[o.order_id] = {
              order_id: o.order_id,
              client_order_id: o.client_order_id,
              ticker: o.ticker,
              side: o.side,
              status: o.status as OpenOrder['status'],
              price_cents: o.price_cents,
              remaining_count: o.remaining_count,
            }
          }
        }
        // Drop any cached order that REST says is no longer resting. This
        // is the corrective path when a fill/cancel WS event was missed.
        for (const id of Object.keys(next)) {
          if (!seen.has(id)) delete next[id]
        }
        return next
      },
    )
  }, [bootstrap.data, queryClient])

  // Subscribe to the live cache.
  const { data } = useQuery<Record<string, OpenOrder>>({
    queryKey: ['open_orders'],
    queryFn: () => queryClient.getQueryData(['open_orders']) ?? {},
    staleTime: Infinity,
  })

  const all = Object.values(data ?? {})
  const orders = filterTicker ? all.filter((o) => o.ticker === filterTicker) : all
  return {
    orders,
    isError: bootstrap.isError,
    error: bootstrap.error,
  }
}
