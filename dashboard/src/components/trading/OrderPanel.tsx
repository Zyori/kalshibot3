import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import type { MarketBook } from '../../contexts/WebSocketProvider'
import { bestAsk, bestBid } from '../../lib/book'

type Side = 'yes' | 'no'
type Action = 'buy' | 'sell'

type PreviewResponse = {
  verdict: 'ok' | 'soft_warn' | 'loud_confirm' | 'hard_refuse'
  reasons: string[]
  total_cost_cents: number
}

type PlaceResponse = {
  bet_id: number
  kalshi_order_id: string
  client_order_id: string
  status: string
  ticker: string
  side: string
  count: number
  remaining_count: number
  yes_price_cents: number | null
  no_price_cents: number | null
  sanity_reasons: string[]
}

/**
 * Order entry for one market. Live cost preview, three-tier sanity guard
 * from the backend, two quick-action buttons that skip the typed-price
 * flow entirely.
 *
 * Quick-action buttons:
 *   Buy now  → limit at (best_ask + 1) — guaranteed to cross unless the
 *              book moves between click and execution
 *   Sell now → limit at (best_bid - 1) — symmetric
 * Neither requires a confirm; only LOUD_CONFIRM verdicts prompt a dialog.
 */
export default function OrderPanel({
  ticker,
  book,
}: {
  ticker: string
  book: MarketBook | undefined
}) {
  const queryClient = useQueryClient()
  const [side, setSide] = useState<Side>('yes')
  const [action, setAction] = useState<Action>('buy')
  const [count, setCount] = useState<number>(1)
  const [price, setPrice] = useState<number>(50)
  const [postOnly, setPostOnly] = useState(false)
  const [loudReasons, setLoudReasons] = useState<string[] | null>(null)
  const [placedNote, setPlacedNote] = useState<string | null>(null)

  // Auto-track best price when the user hasn't manually overridden price.
  const [priceTouched, setPriceTouched] = useState(false)
  const recommendedPrice = recommendPrice(book, side, action)
  useEffect(() => {
    if (!priceTouched && recommendedPrice !== null) setPrice(recommendedPrice)
  }, [recommendedPrice, priceTouched])

  const previewBody = { ticker, side, action, count, price_cents: price, post_only: postOnly }
  const preview = useQuery<PreviewResponse>({
    queryKey: ['preview', ticker, side, action, count, price, postOnly],
    queryFn: async () => {
      const res = await fetch('/api/orders/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(previewBody),
      })
      if (!res.ok) throw new Error(`preview: ${res.status}`)
      return res.json()
    },
    // Brief stale-time so rapid typing doesn't hammer the backend.
    staleTime: 750,
  })

  const place = useMutation<PlaceResponse, Error, { acknowledged_loud?: boolean }>({
    mutationFn: async ({ acknowledged_loud = false }) => {
      const res = await fetch('/api/orders/place', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...previewBody, acknowledged_loud }),
      })
      if (res.status === 409) {
        const body = await res.json()
        setLoudReasons(body.detail?.reasons ?? ['Order requires confirmation.'])
        throw new Error('loud_confirm')
      }
      if (!res.ok) {
        const body = await res.text()
        throw new Error(body || `place: ${res.status}`)
      }
      return res.json()
    },
    onSuccess: (resp) => {
      setLoudReasons(null)
      setPlacedNote(
        `Placed ${resp.count} ${resp.side.toUpperCase()} @ ${resp.yes_price_cents ?? resp.no_price_cents}¢ — order ${resp.kalshi_order_id.slice(0, 8)} (${resp.status})`
      )
      queryClient.invalidateQueries({ queryKey: ['ledger'] })
      queryClient.invalidateQueries({ queryKey: ['open_orders'] })
    },
  })

  const placeButtonDisabled =
    place.isPending ||
    !preview.data ||
    preview.data.verdict === 'hard_refuse'

  return (
    <div className="rounded-lg border border-border bg-bg-card p-4">
      <h3 className="mb-3 text-sm font-semibold text-text">Place order</h3>

      <div className="mb-3 grid grid-cols-2 gap-2">
        <Toggle value={side} onChange={setSide}
          options={[{ label: 'YES', value: 'yes' }, { label: 'NO', value: 'no' }]} />
        <Toggle value={action} onChange={setAction}
          options={[{ label: 'Buy', value: 'buy' }, { label: 'Sell', value: 'sell' }]} />
      </div>

      <div className="mb-3 grid grid-cols-2 gap-2">
        <QuickButton
          label={`Buy ${side.toUpperCase()} @ ${quickPrice(book, side, 'buy') ?? '—'}¢`}
          disabled={quickPrice(book, side, 'buy') === null || place.isPending}
          onClick={() => {
            const p = quickPrice(book, side, 'buy')
            if (p === null) return
            setAction('buy')
            setPrice(p)
            setPriceTouched(true)
            place.mutate({})
          }}
        />
        <QuickButton
          label={`Sell ${side.toUpperCase()} @ ${quickPrice(book, side, 'sell') ?? '—'}¢`}
          disabled={quickPrice(book, side, 'sell') === null || place.isPending}
          onClick={() => {
            const p = quickPrice(book, side, 'sell')
            if (p === null) return
            setAction('sell')
            setPrice(p)
            setPriceTouched(true)
            place.mutate({})
          }}
        />
      </div>

      <div className="mb-3 grid grid-cols-2 gap-3">
        <NumberField label="Count" value={count} onChange={setCount} min={1} />
        <NumberField
          label="Price (¢)"
          value={price}
          onChange={(v) => { setPrice(v); setPriceTouched(true) }}
          min={1}
          max={99}
        />
      </div>

      <label className="mb-3 flex items-center gap-2 text-xs text-text-muted">
        <input
          type="checkbox"
          checked={postOnly}
          onChange={(e) => setPostOnly(e.target.checked)}
        />
        post-only (refuse to cross spread)
      </label>

      <div className="mb-3 flex items-baseline justify-between border-t border-border pt-3 text-sm">
        <span className="text-text-muted">Total cost</span>
        <span className="font-mono tabular-nums text-text">
          {count} × {price}¢ = ${((count * price) / 100).toFixed(2)}
        </span>
      </div>

      {preview.data && preview.data.reasons.length > 0 && (
        <ReasonsList
          reasons={preview.data.reasons}
          tone={preview.data.verdict === 'loud_confirm' ? 'loud' : 'soft'}
        />
      )}

      <button
        type="button"
        onClick={() => place.mutate({})}
        disabled={placeButtonDisabled}
        className="w-full rounded-md border border-action bg-action px-3 py-2 text-sm font-semibold text-bg hover:opacity-90 disabled:opacity-50"
      >
        {place.isPending ? 'Placing…' : 'Place limit order'}
      </button>

      {placedNote && (
        <div className="mt-3 rounded-md border border-gain bg-bg p-2 text-xs text-gain">
          {placedNote}
        </div>
      )}
      {place.isError && !loudReasons && (
        <div className="mt-3 rounded-md border border-loss bg-bg p-2 text-xs text-loss">
          {String(place.error)}
        </div>
      )}

      {loudReasons !== null && (
        <ConfirmDialog
          reasons={loudReasons}
          onCancel={() => setLoudReasons(null)}
          onConfirm={() => {
            place.mutate({ acknowledged_loud: true })
          }}
        />
      )}
    </div>
  )
}

// === helpers ===

function recommendPrice(book: MarketBook | undefined, side: Side, action: Action): number | null {
  if (!book) return null
  if (action === 'buy') return bestAsk(book, side)
  return bestBid(book, side)
}

function quickPrice(book: MarketBook | undefined, side: Side, action: Action): number | null {
  /** "Buy now" = best_ask + 1 to cross; "Sell now" = best_bid - 1. */
  if (!book) return null
  if (action === 'buy') {
    const ask = bestAsk(book, side)
    return ask !== null ? Math.min(99, ask + 1) : null
  }
  const bid = bestBid(book, side)
  return bid !== null ? Math.max(1, bid - 1) : null
}

// === sub-components ===

function Toggle<T extends string>({
  value, onChange, options,
}: {
  value: T
  onChange: (v: T) => void
  options: { label: string; value: T }[]
}) {
  return (
    <div className="grid grid-cols-2 overflow-hidden rounded-md border border-border">
      {options.map((opt) => (
        <button
          key={opt.value}
          type="button"
          onClick={() => onChange(opt.value)}
          className={
            value === opt.value
              ? 'bg-bg-hover py-1.5 text-xs text-text'
              : 'py-1.5 text-xs text-text-muted hover:bg-bg-hover'
          }
        >
          {opt.label}
        </button>
      ))}
    </div>
  )
}

function NumberField({
  label, value, onChange, min, max,
}: {
  label: string
  value: number
  onChange: (v: number) => void
  min?: number
  max?: number
}) {
  return (
    <label className="block">
      <span className="block text-xs text-text-muted">{label}</span>
      <input
        type="number"
        min={min}
        max={max}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="mt-1 w-full rounded-md border border-border bg-bg px-2 py-1.5 font-mono text-sm tabular-nums text-text focus:border-accent focus:outline-none"
      />
    </label>
  )
}

function QuickButton({
  label, onClick, disabled,
}: {
  label: string
  onClick: () => void
  disabled: boolean
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="rounded-md border border-action bg-bg px-3 py-2 text-xs font-mono text-action hover:bg-bg-hover disabled:opacity-40"
    >
      {label}
    </button>
  )
}

function ReasonsList({ reasons, tone }: { reasons: string[]; tone: 'soft' | 'loud' }) {
  const color = tone === 'loud' ? 'border-loss text-loss' : 'border-action text-action'
  return (
    <ul className={`mb-3 space-y-1 rounded-md border ${color} bg-bg p-2 text-xs`}>
      {reasons.map((r, i) => <li key={i}>⚠ {r}</li>)}
    </ul>
  )
}

function ConfirmDialog({
  reasons, onCancel, onConfirm,
}: {
  reasons: string[]
  onCancel: () => void
  onConfirm: () => void
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-bg/80 p-4">
      <div className="w-full max-w-md rounded-lg border border-loss bg-bg-card p-6 shadow-2xl">
        <h3 className="mb-3 text-base font-semibold text-loss">This order looks unusual</h3>
        <ul className="mb-4 space-y-2 text-sm text-text">
          {reasons.map((r, i) => <li key={i}>{r}</li>)}
        </ul>
        <p className="mb-4 text-xs text-text-muted">
          Are you sure you want to place this order anyway?
        </p>
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            className="rounded-md border border-border bg-bg-hover px-3 py-1.5 text-sm text-text"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            className="rounded-md border border-loss bg-loss px-3 py-1.5 text-sm font-semibold text-white"
          >
            Place anyway
          </button>
        </div>
      </div>
    </div>
  )
}
