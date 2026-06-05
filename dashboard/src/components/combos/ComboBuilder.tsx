import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import { formatDollars } from '../../lib/format'
import { COMBO_STRATEGIES, errorMessage, Field, Segmented, SIDES } from './ComboFields'

type LegDraft = { market_ticker: string; event_ticker: string; side: 'yes' | 'no' }

type Materialized = {
  ticker: string
  event_ticker: string
  subtitle: string | null
  yes_bid_cents: number | null
  yes_ask_cents: number | null
  no_bid_cents: number | null
  no_ask_cents: number | null
  leg_count: number
}

type PlaceResult = {
  bet_id: number
  ticker: string
  side: 'yes' | 'no'
  entry_price_cents: number
  quantity: number
  stake_cents: number
  leg_count: number
}

const EMPTY_LEG: LegDraft = { market_ticker: '', event_ticker: '', side: 'yes' }

/**
 * Build a combo from legs and place it on Kalshi. Two steps, mirroring every
 * order: STAGE (materialize the combo market — idempotent, no order) then
 * CONFIRM (place a limit at your price). A fresh combo is illiquid, so you set
 * a deliberate limit price.
 */
export default function ComboBuilder() {
  const qc = useQueryClient()
  const [legs, setLegs] = useState<LegDraft[]>([{ ...EMPTY_LEG }, { ...EMPTY_LEG }])
  const [side, setSide] = useState<'yes' | 'no'>('yes')
  const [strategy, setStrategy] = useState<string>('lock_parlay')
  const [price, setPrice] = useState('')
  const [count, setCount] = useState('')
  const [why, setWhy] = useState('')
  const [staged, setStaged] = useState<Materialized | null>(null)

  const filledLegs = legs.filter((l) => l.market_ticker.trim() && l.event_ticker.trim())
  const canStage = filledLegs.length >= 2

  const stage = useMutation<Materialized, Error>({
    mutationFn: async () => {
      const res = await fetch('/api/combos/materialize', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ legs: filledLegs }),
      })
      if (!res.ok) {
        const d = await res.json().catch(() => null)
        throw new Error(errorMessage(d?.detail, `Stage failed (${res.status})`))
      }
      return res.json()
    },
    onSuccess: (m) => setStaged(m),
  })

  const place = useMutation<PlaceResult, Error>({
    mutationFn: async () => {
      const res = await fetch('/api/combos/place', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          legs: filledLegs,
          side,
          price_cents: Number(price),
          count: Number(count),
          strategy,
          human_reasoning: why.trim() || null,
          acknowledged: true, // this mutation IS the confirm action
        }),
      })
      if (!res.ok) {
        const d = await res.json().catch(() => null)
        throw new Error(errorMessage(d?.detail, `Place failed (${res.status})`))
      }
      return res.json()
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['ledger'] })
      qc.invalidateQueries({ queryKey: ['ledger_stats'] })
      qc.invalidateQueries({ queryKey: ['positions'] })
      setStaged(null)
      setLegs([{ ...EMPTY_LEG }, { ...EMPTY_LEG }])
      setPrice('')
      setCount('')
      setWhy('')
    },
  })

  function updateLeg(i: number, patch: Partial<LegDraft>) {
    setLegs((prev) => prev.map((l, j) => (j === i ? { ...l, ...patch } : l)))
    setStaged(null) // editing legs invalidates the staged combo
  }

  const priceN = Number(price)
  const countN = Number(count)
  const stakeCents = priceN > 0 && countN > 0 ? priceN * countN : 0
  const canPlace = staged && priceN >= 1 && priceN <= 99 && countN >= 1

  return (
    <div className="space-y-6">
      <p className="text-sm text-text-muted">
        Build a parlay leg by leg, stage it to see the combo market, then confirm
        to place a limit order on Kalshi. Every order is human-confirmed.
      </p>

      <div className="space-y-3 rounded-md border border-border bg-bg-panel p-4">
        <div className="text-xs uppercase tracking-wide text-text-muted">
          Legs ({filledLegs.length})
        </div>
        {legs.map((leg, i) => (
          <div key={i} className="grid grid-cols-[1fr_1fr_auto_auto] items-end gap-2">
            <Field label={i === 0 ? 'Market ticker' : ''}>
              <input
                value={leg.market_ticker}
                onChange={(e) => updateLeg(i, { market_ticker: e.target.value.trim() })}
                placeholder="KXINTLFRIENDLYGAME-…-FRA"
                spellCheck={false}
                className="w-full rounded border border-border bg-bg px-2 py-1.5 font-mono text-xs text-text outline-none focus:border-action"
              />
            </Field>
            <Field label={i === 0 ? 'Event ticker' : ''}>
              <input
                value={leg.event_ticker}
                onChange={(e) => updateLeg(i, { event_ticker: e.target.value.trim() })}
                placeholder="KXINTLFRIENDLYGAME-…"
                spellCheck={false}
                className="w-full rounded border border-border bg-bg px-2 py-1.5 font-mono text-xs text-text outline-none focus:border-action"
              />
            </Field>
            <div className="w-20">
              <Segmented
                options={SIDES as readonly string[]}
                value={leg.side}
                onChange={(v) => updateLeg(i, { side: v as 'yes' | 'no' })}
              />
            </div>
            <button
              type="button"
              onClick={() => {
                setLegs((prev) => prev.filter((_, j) => j !== i))
                setStaged(null)
              }}
              disabled={legs.length <= 2}
              className="mb-0.5 rounded border border-border px-2 py-1.5 text-xs text-text-muted hover:bg-bg-hover disabled:opacity-30"
              title="Remove leg"
            >
              ×
            </button>
          </div>
        ))}
        {legs.length < 8 && (
          <button
            type="button"
            onClick={() => setLegs((prev) => [...prev, { ...EMPTY_LEG }])}
            className="text-xs text-action hover:underline"
          >
            + Add leg
          </button>
        )}

        <button
          type="button"
          onClick={() => stage.mutate()}
          disabled={!canStage || stage.isPending}
          className="w-full rounded border border-action bg-action/10 px-4 py-2 text-sm font-semibold text-text disabled:cursor-not-allowed disabled:opacity-40"
        >
          {stage.isPending ? 'Staging…' : 'Stage combo'}
        </button>
        {stage.isError && (
          <div className="rounded border border-loss/40 bg-loss/10 px-3 py-2 text-xs text-loss">
            {stage.error.message}
          </div>
        )}
      </div>

      {staged && (
        <div className="space-y-4 rounded-md border border-action/40 bg-action/5 p-4">
          <div>
            <div className="text-xs uppercase tracking-wide text-text-muted">
              Staged combo — {staged.leg_count} legs
            </div>
            <div className="mt-1 text-sm text-text">{staged.subtitle ?? staged.ticker}</div>
            <div className="mt-1 font-mono text-[11px] text-text-muted">{staged.ticker}</div>
            <div className="mt-1 font-mono text-xs text-text-muted">
              book: yes {staged.yes_bid_cents ?? '—'}/{staged.yes_ask_cents ?? '—'} · no{' '}
              {staged.no_bid_cents ?? '—'}/{staged.no_ask_cents ?? '—'}
              {staged.yes_bid_cents === null && staged.yes_ask_cents === null && (
                <span className="ml-2 text-action">(no book yet — set a limit)</span>
              )}
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <Field label="Side">
              <Segmented
                options={SIDES as readonly string[]}
                value={side}
                onChange={(v) => setSide(v as 'yes' | 'no')}
              />
            </Field>
            <Field label="Strategy">
              <Segmented
                options={COMBO_STRATEGIES as readonly string[]}
                value={strategy}
                onChange={setStrategy}
              />
            </Field>
            <Field label="Limit price (¢)">
              <input
                value={price}
                onChange={(e) => setPrice(e.target.value)}
                inputMode="numeric"
                placeholder="17"
                className="w-full rounded border border-border bg-bg px-3 py-2 font-mono text-sm text-text outline-none focus:border-action"
              />
            </Field>
            <Field label="Contracts">
              <input
                value={count}
                onChange={(e) => setCount(e.target.value)}
                inputMode="numeric"
                placeholder="50"
                className="w-full rounded border border-border bg-bg px-3 py-2 font-mono text-sm text-text outline-none focus:border-action"
              />
            </Field>
          </div>

          <Field label="Why (optional)">
            <textarea
              value={why}
              onChange={(e) => setWhy(e.target.value)}
              rows={2}
              className="w-full rounded border border-border bg-bg px-3 py-2 text-sm text-text outline-none focus:border-action"
            />
          </Field>

          {stakeCents > 0 && (
            <div className="font-mono text-xs text-text-muted">
              Stake: {formatDollars(stakeCents)} ({countN} × {priceN}¢{' '}
              {side.toUpperCase()})
            </div>
          )}

          <button
            type="button"
            onClick={() => place.mutate()}
            disabled={!canPlace || place.isPending}
            className="w-full rounded bg-action px-4 py-2 text-sm font-semibold text-bg disabled:cursor-not-allowed disabled:opacity-40"
          >
            {place.isPending ? 'Placing…' : `Confirm & place ${side.toUpperCase()}`}
          </button>
          {place.isError && (
            <div className="rounded border border-loss/40 bg-loss/10 px-3 py-2 text-xs text-loss">
              {place.error.message}
            </div>
          )}
        </div>
      )}

      {place.isSuccess && place.data && (
        <div className="rounded-md border border-gain/40 bg-gain/5 p-4 text-sm text-gain">
          Placed — bet #{place.data.bet_id}: {place.data.quantity} ×{' '}
          {place.data.entry_price_cents}¢, stake {formatDollars(place.data.stake_cents)}
        </div>
      )}
    </div>
  )
}
