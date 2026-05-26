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
  const [count, setCount] = useState<number>(1)
  const [price, setPrice] = useState<number>(50)
  const [postOnly, setPostOnly] = useState(false)
  const [loudReasons, setLoudReasons] = useState<{ reasons: string[]; action: Action } | null>(null)
  const [placedNote, setPlacedNote] = useState<string | null>(null)

  // Snap the typed price to the current ask whenever the user picks a
  // side (or when the book first arrives). After the user manually edits
  // the price field, we stop fighting them — typing a custom price marks
  // it "touched" and we leave it alone until they click YES/NO again, which
  // resets the touched flag and re-snaps to the new side's ask.
  const [priceTouched, setPriceTouched] = useState(false)
  const sideAsk = bestAsk(book, side)
  useEffect(() => {
    if (!priceTouched && sideAsk !== null) setPrice(sideAsk)
  }, [sideAsk, priceTouched])

  // Preview is keyed by action too. We run a preview per direction so each
  // submit button can show its own warning state without an extra round-trip
  // at click time. Both queries are debounced by staleTime.
  const previewBuy = useOrderPreview(ticker, side, 'buy', count, price, postOnly)
  const previewSell = useOrderPreview(ticker, side, 'sell', count, price, postOnly)

  const place = useMutation<PlaceResponse, Error, { action: Action; acknowledged_loud?: boolean }>({
    mutationFn: async ({ action, acknowledged_loud = false }) => {
      const body = { ticker, side, action, count, price_cents: price, post_only: postOnly, acknowledged_loud }
      const res = await fetch('/api/orders/place', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (res.status === 409) {
        const errBody = await res.json()
        setLoudReasons({
          reasons: errBody.detail?.reasons ?? ['Order requires confirmation.'],
          action,
        })
        throw new Error('loud_confirm')
      }
      if (!res.ok) {
        const text = await res.text()
        throw new Error(text || `place: ${res.status}`)
      }
      return res.json()
    },
    onSuccess: (resp) => {
      setLoudReasons(null)
      const filled = resp.yes_price_cents ?? resp.no_price_cents
      setPlacedNote(
        `Placed ${resp.count} ${resp.side.toUpperCase()} @ ${filled}¢ — order ${resp.kalshi_order_id.slice(0, 8)} (${resp.status})`
      )
      queryClient.invalidateQueries({ queryKey: ['ledger'] })
      queryClient.invalidateQueries({ queryKey: ['open_orders'] })
    },
  })

  const quickBuy = quickPrice(book, side, 'buy')
  const quickSell = quickPrice(book, side, 'sell')

  return (
    <div className="rounded-lg border border-border bg-bg-card p-4">
      <h3 className="mb-3 text-sm font-semibold text-text">Place order</h3>

      <div className="mb-3">
        <Toggle value={side} onChange={(v) => { setSide(v); setPriceTouched(false) }}
          options={[{ label: 'YES', value: 'yes' }, { label: 'NO', value: 'no' }]} />
      </div>

      <div className="mb-3 grid grid-cols-2 gap-2">
        <QuickButton
          label={`Buy ${side.toUpperCase()} now`}
          subLabel={quickBuy !== null ? `@ ${quickBuy}¢` : 'no ask'}
          disabled={quickBuy === null || place.isPending}
          onClick={() => {
            if (quickBuy === null) return
            setPrice(quickBuy)
            setPriceTouched(true)
            place.mutate({ action: 'buy' })
          }}
        />
        <QuickButton
          label={`Sell ${side.toUpperCase()} now`}
          subLabel={quickSell !== null ? `@ ${quickSell}¢` : 'no bid'}
          disabled={quickSell === null || place.isPending}
          onClick={() => {
            if (quickSell === null) return
            setPrice(quickSell)
            setPriceTouched(true)
            place.mutate({ action: 'sell' })
          }}
        />
      </div>

      <div className="mb-2 border-t border-border pt-3 text-xs text-text-muted">
        Or place a limit at your own price:
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

      <div className="mb-3 flex items-baseline justify-between text-sm">
        <span className="text-text-muted">Total</span>
        <span className="font-mono tabular-nums text-text">
          {count} × {price}¢ = ${((count * price) / 100).toFixed(2)}
        </span>
      </div>

      {/* The preview reasons that matter most are for the direction the user
          is *likely* to click. We surface both directions' reasons stacked,
          tagged with their action, so the typed-price flow never hides a
          sanity warning. Soft tone is reserved for non-blocking advisories. */}
      <PreviewReasons direction="buy" preview={previewBuy.data} />
      <PreviewReasons direction="sell" preview={previewSell.data} />

      <div className="grid grid-cols-2 gap-2">
        <PlaceButton
          label="Place Buy"
          price={price}
          disabled={place.isPending || previewBuy.data?.verdict === 'hard_refuse'}
          tone="buy"
          onClick={() => place.mutate({ action: 'buy' })}
        />
        <PlaceButton
          label="Place Sell"
          price={price}
          disabled={place.isPending || previewSell.data?.verdict === 'hard_refuse'}
          tone="sell"
          onClick={() => place.mutate({ action: 'sell' })}
        />
      </div>

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
          reasons={loudReasons.reasons}
          onCancel={() => setLoudReasons(null)}
          onConfirm={() => {
            place.mutate({ action: loudReasons.action, acknowledged_loud: true })
          }}
        />
      )}
    </div>
  )
}

function useOrderPreview(
  ticker: string,
  side: Side,
  action: Action,
  count: number,
  price: number,
  postOnly: boolean,
) {
  return useQuery<PreviewResponse>({
    queryKey: ['preview', ticker, side, action, count, price, postOnly],
    queryFn: async () => {
      const res = await fetch('/api/orders/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ticker, side, action, count, price_cents: price, post_only: postOnly }),
      })
      if (!res.ok) throw new Error(`preview: ${res.status}`)
      return res.json()
    },
    staleTime: 750,
  })
}

// === helpers ===

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
  label, subLabel, onClick, disabled,
}: {
  label: string
  subLabel: string
  onClick: () => void
  disabled: boolean
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="flex flex-col items-center rounded-md border border-action bg-bg px-3 py-2 text-action hover:bg-bg-hover disabled:opacity-40"
    >
      <span className="text-sm font-semibold">{label}</span>
      <span className="text-xs font-mono tabular-nums text-text-muted">{subLabel}</span>
    </button>
  )
}

function PlaceButton({
  label, price, onClick, disabled, tone,
}: {
  label: string
  price: number
  onClick: () => void
  disabled: boolean
  tone: 'buy' | 'sell'
}) {
  // Use the dashboard's semantic palette: green for buy (gain side),
  // red for sell (loss side) — matches the rest of the app.
  const cls = tone === 'buy'
    ? 'border-gain bg-gain text-bg'
    : 'border-loss bg-loss text-bg'
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`rounded-md border px-3 py-2 text-sm font-semibold hover:opacity-90 disabled:opacity-40 ${cls}`}
    >
      {label} @ {price}¢
    </button>
  )
}

function PreviewReasons({
  direction, preview,
}: {
  direction: 'buy' | 'sell'
  preview: PreviewResponse | undefined
}) {
  if (!preview || preview.reasons.length === 0) return null
  const tone = preview.verdict === 'loud_confirm' || preview.verdict === 'hard_refuse'
    ? 'border-loss text-loss'
    : 'border-action text-action'
  return (
    <ul className={`mb-2 space-y-1 rounded-md border ${tone} bg-bg p-2 text-xs`}>
      <li className="font-semibold uppercase tracking-wide opacity-70">
        {direction} warnings
      </li>
      {preview.reasons.map((r, i) => <li key={i}>⚠ {r}</li>)}
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
