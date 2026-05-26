import { useMutation, useQueryClient } from '@tanstack/react-query'

import { useOpenOrders } from '../../hooks/useOpenOrders'

/**
 * Lists currently-resting orders for one ticker. Cancel button on each.
 * Driven entirely by the WS user_order stream — no REST polling.
 */
export default function OpenOrdersCard({ ticker }: { ticker: string }) {
  const orders = useOpenOrders(ticker)
  const queryClient = useQueryClient()

  const cancel = useMutation({
    mutationFn: async (orderId: string) => {
      const res = await fetch(`/api/orders/${encodeURIComponent(orderId)}`, {
        method: 'DELETE',
      })
      if (!res.ok) {
        const body = await res.text()
        throw new Error(body)
      }
      return res.json()
    },
    onSuccess: () => {
      // WS will deliver user_order(status=canceled); invalidate as backstop.
      queryClient.invalidateQueries({ queryKey: ['open_orders'] })
    },
  })

  if (orders.length === 0) {
    return (
      <div className="rounded-lg border border-border bg-bg-card p-4 text-xs text-text-muted">
        No resting orders on this market.
      </div>
    )
  }

  return (
    <div className="rounded-lg border border-border bg-bg-card p-4">
      <h3 className="mb-3 text-sm font-semibold text-text">Resting orders</h3>
      <ul className="space-y-2">
        {orders.map((o) => (
          <li
            key={o.order_id}
            className="grid items-center gap-2 rounded-md border border-border bg-bg p-2 text-xs"
            style={{ gridTemplateColumns: '1fr auto auto' }}
          >
            <div>
              <div className="font-mono text-text">
                {o.side.toUpperCase()} {o.remaining_count} @ {o.yes_price ?? '—'}¢
              </div>
              <div className="text-[10px] text-text-muted">{o.status}</div>
            </div>
            <span className="font-mono text-[10px] text-text-muted">
              {o.order_id.slice(0, 8)}
            </span>
            <button
              type="button"
              onClick={() => cancel.mutate(o.order_id)}
              disabled={cancel.isPending}
              className="rounded-md border border-border bg-bg-hover px-2 py-1 text-[11px] text-text hover:border-loss hover:text-loss"
            >
              Cancel
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}
