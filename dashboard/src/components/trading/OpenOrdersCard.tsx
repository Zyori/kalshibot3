import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import type { OpenOrder } from '../../contexts/WebSocketProvider'
import { useOpenOrders } from '../../hooks/useOpenOrders'
import InlineError from '../InlineError'

/**
 * Lists currently-resting orders for one ticker. Each row can be cancelled or
 * edited in place (amend: change resting price + count). Driven by the WS
 * user_order stream + REST bootstrap.
 */
export default function OpenOrdersCard({ ticker }: { ticker: string }) {
  const { orders, isError, error } = useOpenOrders(ticker)

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
      <ul className="space-y-2">
        {orders.map((o) => (
          <OrderRow key={o.order_id} order={o} />
        ))}
      </ul>
    </div>
  )
}

function OrderRow({ order: o }: { order: OpenOrder }) {
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState(false)
  // price_cents is already in the held side's own frame (backend un-inverts
  // Kalshi's YES-book read-back), and the amend body's `price_cents` is that
  // same side's price (the backend maps it back to yes_price/no_price).
  const [price, setPrice] = useState<number>(o.price_cents ?? 0)
  const [count, setCount] = useState<number>(o.remaining_count)

  const invalidateAll = () => {
    queryClient.invalidateQueries({ queryKey: ['open_orders'] })
    queryClient.invalidateQueries({ queryKey: ['open_orders_rest'] })
    queryClient.invalidateQueries({ queryKey: ['positions'] })
    queryClient.invalidateQueries({ queryKey: ['bankroll_deployed'] })
  }

  // Pull the backend reason out of {detail} (string or {reasons:[...]}), else text.
  const reasonFrom = async (res: Response, fallback: string): Promise<string> => {
    const raw = await res.text()
    try {
      const body = JSON.parse(raw)
      const reasons = body?.detail?.reasons
      if (Array.isArray(reasons)) return reasons.join(' ')
      if (typeof body?.detail === 'string') return body.detail
    } catch {
      /* not JSON */
    }
    return raw || fallback
  }

  const cancel = useMutation({
    mutationFn: async () => {
      const res = await fetch(`/api/orders/${encodeURIComponent(o.order_id)}`, { method: 'DELETE' })
      if (!res.ok) throw new Error(await reasonFrom(res, `Cancel failed (${res.status})`))
      return res.json()
    },
    onSuccess: invalidateAll,
    onError: () => {},
  })

  const amend = useMutation({
    mutationFn: async () => {
      const res = await fetch(`/api/orders/${encodeURIComponent(o.order_id)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ price_cents: price, count }),
      })
      if (!res.ok) throw new Error(await reasonFrom(res, `Edit failed (${res.status})`))
      return res.json()
    },
    onSuccess: () => {
      setEditing(false)
      invalidateAll()
    },
    onError: () => {},
  })

  const displayPrice = o.price_cents

  return (
    <li className="rounded-md border border-border bg-bg p-2 text-xs">
      <div className="grid items-center gap-2" style={{ gridTemplateColumns: '1fr auto auto auto' }}>
        <div>
          <div className="font-mono text-text">
            {o.side.toUpperCase()} {o.remaining_count} @ {displayPrice ?? '—'}¢
          </div>
          <div className="text-[10px] text-text-muted">{o.status}</div>
        </div>
        <span className="font-mono text-[10px] text-text-muted">{o.order_id.slice(0, 8)}</span>
        <button
          type="button"
          onClick={() => {
            // Reset inputs to the live values whenever opening the editor.
            if (!editing) {
              setPrice(o.price_cents ?? 0)
              setCount(o.remaining_count)
            }
            setEditing((v) => !v)
          }}
          className="rounded-md border border-border bg-bg-hover px-2 py-1 text-[11px] text-text hover:border-action hover:text-action"
        >
          {editing ? 'Close' : 'Edit'}
        </button>
        <button
          type="button"
          onClick={() => cancel.mutate()}
          disabled={cancel.isPending}
          className="rounded-md border border-border bg-bg-hover px-2 py-1 text-[11px] text-text hover:border-loss hover:text-loss"
        >
          Cancel
        </button>
      </div>

      {editing && (
        <div className="mt-2 flex items-end gap-2 border-t border-border pt-2">
          <label className="flex flex-col gap-1 text-[10px] text-text-muted">
            Price (¢)
            <input
              type="number"
              min={1}
              max={99}
              value={price}
              onChange={(e) => setPrice(Number(e.target.value))}
              className="w-16 rounded-md border border-border bg-bg-card px-2 py-1 font-mono text-xs text-text"
            />
          </label>
          <label className="flex flex-col gap-1 text-[10px] text-text-muted">
            Count
            <input
              type="number"
              min={1}
              value={count}
              onChange={(e) => setCount(Number(e.target.value))}
              className="w-16 rounded-md border border-border bg-bg-card px-2 py-1 font-mono text-xs text-text"
            />
          </label>
          {o.price_cents === null && (
            <span className="text-[10px] text-text-muted">enter a price</span>
          )}
          <button
            type="button"
            onClick={() => amend.mutate()}
            disabled={amend.isPending || price < 1 || price > 99 || count < 1}
            className="rounded-md border border-action bg-bg px-3 py-1 text-[11px] font-semibold text-action hover:bg-bg-hover disabled:opacity-40"
          >
            {amend.isPending ? 'Saving…' : 'Save'}
          </button>
        </div>
      )}

      {cancel.isError && (
        <div className="mt-2">
          <InlineError message="Couldn't cancel that order." detail={cancel.error} />
        </div>
      )}
      {amend.isError && (
        <div className="mt-2">
          <InlineError message="Couldn't edit that order." detail={amend.error} />
        </div>
      )}
    </li>
  )
}
