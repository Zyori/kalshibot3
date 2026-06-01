import { useMutation, useQueryClient } from '@tanstack/react-query'

import { useOpenOrders } from '../../hooks/useOpenOrders'
import InlineError from '../InlineError'

/**
 * Lists currently-resting orders for one ticker. Cancel button on each.
 * Driven entirely by the WS user_order stream — no REST polling.
 */
export default function OpenOrdersCard({ ticker }: { ticker: string }) {
  const { orders, isError, error } = useOpenOrders(ticker)
  const queryClient = useQueryClient()

  const cancel = useMutation({
    mutationFn: async (orderId: string) => {
      const res = await fetch(`/api/orders/${encodeURIComponent(orderId)}`, {
        method: 'DELETE',
      })
      if (!res.ok) {
        // Surface the backend reason. FastAPI puts it in {detail}; fall back to
        // raw text. Without this the mutation threw into the void and Cancel
        // looked like it silently did nothing.
        let msg = `Cancel failed (${res.status})`
        try {
          const body = await res.json()
          msg = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
        } catch {
          msg = (await res.text()) || msg
        }
        throw new Error(msg)
      }
      return res.json()
    },
    onSuccess: () => {
      // WS will deliver user_order(status=canceled); invalidate as backstop.
      queryClient.invalidateQueries({ queryKey: ['open_orders'] })
      queryClient.invalidateQueries({ queryKey: ['open_orders_rest'] })
      queryClient.invalidateQueries({ queryKey: ['positions'] })
      queryClient.invalidateQueries({ queryKey: ['bankroll_deployed'] })
    },
    // onError: no-op handler so a rejected cancel doesn't become an unhandled
    // promise rejection; the error is rendered from cancel.error below.
    onError: () => {},
  })

  if (isError) {
    return <InlineError message="Couldn't load open orders." detail={error} />
  }

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
      {cancel.isError && (
        <div className="mb-3">
          <InlineError message="Couldn't cancel that order." detail={cancel.error} />
        </div>
      )}
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
