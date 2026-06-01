import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import type { MarketBook } from '../../contexts/WebSocketProvider'
import { bestAsk, bestBid } from '../../lib/book'
import { contractsForUnits } from '../../lib/units'

type PositionsResponse = {
  positions: Array<{ ticker: string; side: 'yes' | 'no'; quantity: number }>
}

type Side = 'yes' | 'no'
type Action = 'buy' | 'sell'

// How long a user-entered price is protected from market auto-follow. Long
// enough to type + click without the box moving under you; short enough that
// a forgotten stale price resumes tracking the market.
const PRICE_HOLD_MS = 60_000

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
/**
 * A staged pre-fill pushed in from a suggestion card. `nonce` changes on every
 * "Stage" click so re-staging the same values still re-applies (a plain value
 * compare wouldn't fire). `count` is optional — exit cards may leave it to the
 * panel's default.
 */
export type OrderPrefill = {
  side: Side
  price: number
  count?: number
  nonce: number
}

export default function OrderPanel({
  ticker,
  book,
  prefill,
}: {
  ticker: string
  book: MarketBook | undefined
  prefill?: OrderPrefill | null
}) {
  const queryClient = useQueryClient()
  const [side, setSide] = useState<Side>('yes')
  const [count, setCount] = useState<number>(1)
  const [price, setPrice] = useState<number>(50)
  const [postOnly, setPostOnly] = useState(false)
  const [loudReasons, setLoudReasons] = useState<
    { reasons: string[]; action: Action; price: number } | null
  >(null)
  const [placedNote, setPlacedNote] = useState<string | null>(null)
  // Synchronous double-submit guard. `place.isPending` only disables the button
  // after a re-render; a sub-frame double-click fires twice before that. This
  // ref flips synchronously inside the click handler and resets in onSettled.
  const submittingRef = useRef(false)

  // The manual Price box auto-follows the live market — but only while the
  // user isn't actively setting it. Once they type (or a quick button fills
  // it), that value is theirs and we freeze auto-follow for PRICE_HOLD_MS so
  // a market tick can't stomp a deliberately-entered price mid-edit. After
  // the hold expires, auto-follow resumes so a stale hand-typed number
  // doesn't sit there all game. Switching YES/NO clears the hold (null) to
  // re-snap immediately to the new side.
  //
  // We follow the MIDPOINT, not the ask: this row is direction-neutral (one
  // price, then Buy or Sell), so snapping to the ask made both Place buttons
  // look ask-priced. The quick buttons above own the cross-now prices.
  const [priceHeldAt, setPriceHeldAt] = useState<number | null>(null)
  const holdPrice = () => setPriceHeldAt(Date.now())
  const sideBid = bestBid(book, side)
  const sideAsk = bestAsk(book, side)
  const sideMid =
    sideBid !== null && sideAsk !== null
      ? Math.round((sideBid + sideAsk) / 2)
      : sideAsk ?? sideBid
  useEffect(() => {
    const held = priceHeldAt !== null && Date.now() - priceHeldAt < PRICE_HOLD_MS
    if (!held && sideMid !== null) setPrice(sideMid)
    // Re-arm once the hold window lapses, even on a quiet book that isn't
    // ticking sideMid. Without this, a hand-typed price during a still book
    // would never resume auto-follow.
    if (held) {
      const t = setTimeout(() => setPriceHeldAt(null), PRICE_HOLD_MS - (Date.now() - priceHeldAt!))
      return () => clearTimeout(t)
    }
  }, [sideMid, priceHeldAt])

  // Apply a staged pre-fill from a suggestion card. Keyed on nonce so a repeat
  // "Stage" re-applies. holdPrice() freezes auto-follow for PRICE_HOLD_MS so the
  // suggested price isn't immediately stomped by the next market tick — the user
  // sees exactly what LUTZ proposed, then confirms or adjusts. Syncing an
  // external trigger (a Stage click) into local form state is exactly what an
  // effect is for here — same pattern as the auto-follow effect above. nonce is
  // the trigger; the rest are read at apply time, so the deps array is intentional.
  /* eslint-disable react-hooks/set-state-in-effect, react-hooks/exhaustive-deps */
  useEffect(() => {
    if (!prefill) return
    setSide(prefill.side)
    setPrice(prefill.price)
    if (prefill.count !== undefined) setCount(prefill.count)
    holdPrice()
  }, [prefill?.nonce])
  /* eslint-enable react-hooks/set-state-in-effect, react-hooks/exhaustive-deps */

  // Preview is keyed by action too. We run a preview per direction so each
  // submit button can show its own warning state without an extra round-trip
  // at click time. Both queries are debounced by staleTime.
  const previewBuy = useOrderPreview(ticker, side, 'buy', count, price, postOnly)
  const previewSell = useOrderPreview(ticker, side, 'sell', count, price, postOnly)

  const place = useMutation<
    PlaceResponse,
    Error,
    { action: Action; acknowledged_loud?: boolean; price_override?: number; count_override?: number }
  >({
    mutationFn: async ({ action, acknowledged_loud = false, price_override, count_override }) => {
      // price_override / count_override carry the exact price + size the
      // clicked button computed (quick buy/sell = ask+1 / bid-1 at ½ unit).
      // Relying on `price`/`count` state here was a bug: setState before
      // mutate() doesn't flush before this closure runs, so the quick buttons
      // posted the previously-typed values. The overrides are what the user
      // actually saw and clicked.
      const effectivePrice = price_override ?? price
      const effectiveCount = count_override ?? count
      const body = {
        ticker, side, action, count: effectiveCount,
        price_cents: effectivePrice, post_only: postOnly, acknowledged_loud,
      }
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
          price: effectivePrice,
        })
        throw new Error('loud_confirm')
      }
      if (!res.ok) {
        const text = await res.text()
        throw new Error(text || `place: ${res.status}`)
      }
      return res.json()
    },
    onSettled: () => {
      submittingRef.current = false
    },
    onSuccess: (resp) => {
      setLoudReasons(null)
      const filled = resp.yes_price_cents ?? resp.no_price_cents
      setPlacedNote(
        `Placed ${resp.count} ${resp.side.toUpperCase()} @ ${filled}¢ — order ${resp.kalshi_order_id.slice(0, 8)} (${resp.status})`
      )
      queryClient.invalidateQueries({ queryKey: ['ledger'] })
      queryClient.invalidateQueries({ queryKey: ['open_orders'] })
      queryClient.invalidateQueries({ queryKey: ['open_orders_rest'] })
      queryClient.invalidateQueries({ queryKey: ['positions'] })
      queryClient.invalidateQueries({ queryKey: ['bankroll_deployed'] })
    },
  })

  // Single guarded entry point for every order submission. The ref check is
  // synchronous, so a fast double-click can't fire two mutations before the
  // first re-render disables the buttons.
  const submit = (vars: { action: Action; acknowledged_loud?: boolean; price_override?: number; count_override?: number }) => {
    if (submittingRef.current) return
    submittingRef.current = true
    place.mutate(vars)
  }

  const quickBuy = quickPrice(book, side, 'buy')
  const quickSell = quickPrice(book, side, 'sell')

  // Ghost-share guard: a sell into no position is mathematically a buy of
  // the opposite side. We disable Sell buttons unless we actually hold the
  // chosen side. The backend enforces the same rule with a 400 — this is
  // defense in depth; the UI guard is for clarity, not safety.
  const positions = useQuery<PositionsResponse>({
    queryKey: ['positions'],
    queryFn: async () => {
      const res = await fetch('/api/positions')
      if (!res.ok) throw new Error(`/api/positions: ${res.status}`)
      return res.json()
    },
    refetchInterval: 10_000,
  })
  const heldOnThisSide =
    positions.data?.positions.find((p) => p.ticker === ticker && p.side === side)?.quantity ?? 0
  const canSell = heldOnThisSide >= count
  const sellDisabledReason = !canSell
    ? heldOnThisSide === 0
      ? `You hold no ${side.toUpperCase()} on this market. Buy ${
          side === 'yes' ? 'NO' : 'YES'
        } if you want the other side.`
      : `You only hold ${heldOnThisSide} ${side.toUpperCase()} — can't sell ${count}.`
    : undefined

  // Quick buttons default to ½ unit (sized at the cross-now price). Sell is
  // capped at what we actually hold — never try to sell more than the position.
  const quickBuyCount = contractsForUnits(0.5, quickBuy)
  const quickSellCount =
    quickSell === null
      ? null
      : Math.min(heldOnThisSide, contractsForUnits(0.5, quickSell) ?? 0) || null

  return (
    <div className="rounded-lg border border-border bg-bg-card p-4">
      <h3 className="mb-3 text-sm font-semibold text-text">Place order</h3>

      <div className="mb-3">
        <Toggle value={side} onChange={(v) => { setSide(v); setPriceHeldAt(null) }}
          options={[{ label: 'YES', value: 'yes' }, { label: 'NO', value: 'no' }]} />
      </div>

      <div className="mb-3 grid grid-cols-2 gap-2">
        <QuickButton
          label={`Buy ${side.toUpperCase()} now`}
          subLabel={
            quickBuy === null
              ? 'no ask'
              : quickBuyCount === null
              ? `@ ${quickBuy}¢`
              : `½u · ${quickBuyCount} @ ${quickBuy}¢`
          }
          disabled={quickBuy === null || place.isPending}
          resetKey={`${side}:${quickBuy}:${quickBuyCount}`}
          onConfirm={() => {
            if (quickBuy === null || quickBuyCount === null) return
            setPrice(quickBuy)
            setCount(quickBuyCount)
            holdPrice()
            submit({ action: 'buy', price_override: quickBuy, count_override: quickBuyCount })
          }}
        />
        <QuickButton
          label={`Sell ${side.toUpperCase()} now`}
          subLabel={
            !canSell
              ? 'no position'
              : quickSell === null
              ? 'no bid'
              : quickSellCount === null
              ? `@ ${quickSell}¢`
              : `½u · ${quickSellCount} @ ${quickSell}¢`
          }
          disabled={quickSell === null || quickSellCount === null || place.isPending || !canSell}
          title={sellDisabledReason}
          resetKey={`${side}:${quickSell}:${quickSellCount}`}
          onConfirm={() => {
            if (quickSell === null || quickSellCount === null || !canSell) return
            setPrice(quickSell)
            setCount(quickSellCount)
            holdPrice()
            submit({ action: 'sell', price_override: quickSell, count_override: quickSellCount })
          }}
        />
      </div>

      <div className="mb-2 border-t border-border pt-3 text-xs text-text-muted">
        Or place a limit at your own price:
      </div>

      <div className="mb-2 grid grid-cols-2 gap-3">
        <NumberField label="Count" value={count} onChange={setCount} min={1} />
        <NumberField
          label="Price (¢)"
          value={price}
          onChange={(v) => { setPrice(v); holdPrice() }}
          min={1}
          max={99}
        />
      </div>

      <UnitButtons priceBasis={price > 0 ? price : sideAsk} onPick={setCount} />

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
          resetKey={`${side}:${count}:${price}`}
          onConfirm={() => submit({ action: 'buy' })}
        />
        <PlaceButton
          label="Place Sell"
          price={price}
          disabled={
            place.isPending || previewSell.data?.verdict === 'hard_refuse' || !canSell
          }
          title={sellDisabledReason}
          tone="sell"
          resetKey={`${side}:${count}:${price}`}
          onConfirm={() => submit({ action: 'sell' })}
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
            // Re-submit at the price the warning described, not whatever the
            // book/state is now — the user confirmed *that* order.
            submit({
              action: loudReasons.action,
              acknowledged_loud: true,
              price_override: loudReasons.price,
            })
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

// Two-step confirm for order buttons: first click arms; then press-and-hold
// HOLD_MS to fire. Defeats both accidental single clicks and double-clicks —
// nothing submits without a deliberate sustained press. Releasing early, or
// not arming first, does nothing. `disabled` resets everything.
const HOLD_MS = 2000

function useHoldToConfirm(onConfirm: () => void, disabled: boolean, resetKey: string) {
  const [armed, setArmed] = useState(false)
  const [progress, setProgress] = useState(0) // 0..1 fill while holding
  const rafRef = useRef<number | null>(null)
  const startRef = useRef<number>(0)

  const cancelHold = () => {
    if (rafRef.current !== null) cancelAnimationFrame(rafRef.current)
    rafRef.current = null
    setProgress(0)
  }

  // Reset arm + hold whenever the button goes disabled (e.g. after submit).
  useEffect(() => {
    if (disabled) {
      cancelHold()
      setArmed(false)
    }
  }, [disabled])

  // Reset whenever what's about to be submitted changes (count, price, side).
  // An armed confirm must not carry over onto a different order than the user
  // saw when they armed it.
  useEffect(() => {
    cancelHold()
    setArmed(false)
  }, [resetKey])

  // Clean up any in-flight rAF on unmount.
  useEffect(() => cancelHold, [])

  const pressStart = () => {
    if (disabled) return
    if (!armed) {
      setArmed(true)
      return
    }
    startRef.current = performance.now()
    const tick = () => {
      const elapsed = performance.now() - startRef.current
      const p = Math.min(1, elapsed / HOLD_MS)
      setProgress(p)
      if (p >= 1) {
        cancelHold()
        setArmed(false)
        onConfirm()
      } else {
        rafRef.current = requestAnimationFrame(tick)
      }
    }
    rafRef.current = requestAnimationFrame(tick)
  }

  // Released before the hold completed — abort, stay armed so a re-press
  // continues without re-arming (feels responsive, still can't fire on a tap).
  const pressEnd = () => cancelHold()

  return { armed, progress, pressStart, pressEnd }
}

function QuickButton({
  label, subLabel, onConfirm, disabled, title, resetKey,
}: {
  label: string
  subLabel: string
  onConfirm: () => void
  disabled: boolean
  title?: string
  resetKey: string
}) {
  const { armed, progress, pressStart, pressEnd } = useHoldToConfirm(onConfirm, disabled, resetKey)
  return (
    <button
      type="button"
      onPointerDown={pressStart}
      onPointerUp={pressEnd}
      onPointerLeave={pressEnd}
      disabled={disabled}
      title={title}
      className="relative flex select-none flex-col items-center overflow-hidden rounded-md border border-action bg-bg px-3 py-2 text-action hover:bg-bg-hover disabled:cursor-not-allowed disabled:opacity-40"
    >
      {armed && (
        <span
          className="absolute inset-y-0 left-0 bg-action/20"
          style={{ width: `${progress * 100}%` }}
        />
      )}
      <span className="relative text-sm font-semibold">
        {armed ? 'Hold to confirm' : label}
      </span>
      <span className="relative text-xs font-mono tabular-nums text-text-muted">{subLabel}</span>
    </button>
  )
}

function PlaceButton({
  label, price, onConfirm, disabled, tone, title, resetKey,
}: {
  label: string
  price: number
  onConfirm: () => void
  disabled: boolean
  tone: 'buy' | 'sell'
  title?: string
  resetKey: string
}) {
  const { armed, progress, pressStart, pressEnd } = useHoldToConfirm(onConfirm, disabled, resetKey)
  // Use the dashboard's semantic palette: green for buy (gain side),
  // red for sell (loss side) — matches the rest of the app.
  const cls = tone === 'buy'
    ? 'border-gain bg-gain text-bg'
    : 'border-loss bg-loss text-bg'
  return (
    <button
      type="button"
      onPointerDown={pressStart}
      onPointerUp={pressEnd}
      onPointerLeave={pressEnd}
      disabled={disabled}
      title={title}
      className={`relative select-none overflow-hidden rounded-md border px-3 py-2 text-sm font-semibold hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40 ${cls}`}
    >
      {armed && (
        <span
          className="absolute inset-y-0 left-0 bg-bg/30"
          style={{ width: `${progress * 100}%` }}
        />
      )}
      <span className="relative">
        {armed ? 'Hold to confirm' : `${label} @ ${price}¢`}
      </span>
    </button>
  )
}

function UnitButtons({
  priceBasis,
  onPick,
}: {
  priceBasis: number | null
  onPick: (count: number) => void
}) {
  // .5 / 1 / 2 units → contract count at the current price basis (typed price,
  // or best ask when the box is empty). Disabled when we have no usable price.
  const options: Array<{ label: string; units: number }> = [
    { label: '½ unit', units: 0.5 },
    { label: '1 unit', units: 1 },
    { label: '2 units', units: 2 },
  ]
  return (
    <div className="mb-3 grid grid-cols-3 gap-2">
      {options.map((o) => {
        const n = contractsForUnits(o.units, priceBasis)
        return (
          <button
            key={o.label}
            type="button"
            disabled={n === null}
            onClick={() => n !== null && onPick(n)}
            className="rounded-md border border-border bg-bg px-2 py-1 text-xs text-text-muted hover:border-action hover:text-action disabled:cursor-not-allowed disabled:opacity-40"
          >
            <span className="font-semibold">{o.label}</span>
            {n !== null && <span className="ml-1 font-mono tabular-nums">{n}</span>}
          </button>
        )
      })}
    </div>
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
