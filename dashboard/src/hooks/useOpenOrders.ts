import { useQuery, useQueryClient } from '@tanstack/react-query'
import type { OpenOrder } from '../contexts/WebSocketProvider'

/**
 * Open orders, keyed by order_id. Populated by WebSocketProvider when
 * user_order events arrive; this hook is the read side.
 *
 * There's no REST endpoint to bootstrap from on first page load — Phase 2
 * gets by because user_order events fire immediately after we place via
 * /orders/place, and any prior resting orders would have been ours from
 * previous sessions (handled by chunk 13 position reconciliation).
 */
export function useOpenOrders(filterTicker?: string) {
  const queryClient = useQueryClient()
  const queryResult = useQuery<Record<string, OpenOrder>>({
    queryKey: ['open_orders'],
    queryFn: async () => queryClient.getQueryData(['open_orders']) ?? {},
    staleTime: Infinity,
  })

  const all = Object.values(queryResult.data ?? {})
  const filtered = filterTicker
    ? all.filter((o) => o.ticker === filterTicker)
    : all
  return filtered
}
