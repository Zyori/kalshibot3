import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import { formatDollars } from '../lib/format'

// Parlay strategies — the two combo-relevant Strategy enum values.
const COMBO_STRATEGIES = ['lock_parlay', 'moon_parlay'] as const
const SIDES = ['yes', 'no'] as const

type ComboLogResult = {
  bet_id: number
  ticker: string
  side: 'yes' | 'no'
  entry_price_cents: number
  quantity: number
  stake_cents: number
  leg_count: number
  legs: { title: string | null; ticker: string | null; side: string | null }[]
  placed_at: string | null
}

/**
 * Log a combo (multivariate / parlay) placed on kalshi.com into the ledger.
 *
 * Paste the full combo ticker; the backend hydrates the legs, entry price, and
 * quantity from Kalshi (and back-links the real fee). You add the reflective
 * metadata — strategy, tags, and the "why". This is the logbook half of combo
 * support; placing combos from here is a later phase.
 */
export default function Combos() {
  const qc = useQueryClient()
  const [ticker, setTicker] = useState('')
  const [side, setSide] = useState<'yes' | 'no'>('yes')
  const [strategy, setStrategy] = useState<string>('lock_parlay')
  const [tags, setTags] = useState('')
  const [why, setWhy] = useState('')

  const mut = useMutation<ComboLogResult, Error>({
    mutationFn: async () => {
      const res = await fetch('/api/combos', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ticker: ticker.trim(),
          side,
          strategy,
          tags: tags
            ? tags.split(',').map((t) => t.trim()).filter(Boolean)
            : null,
          human_reasoning: why.trim() || null,
        }),
      })
      if (!res.ok) {
        const detail = await res.json().catch(() => null)
        throw new Error(detail?.detail ?? `Failed (${res.status})`)
      }
      return res.json()
    },
    onSuccess: () => {
      // New combo lands in the ledger; refresh it and the stats.
      qc.invalidateQueries({ queryKey: ['ledger'] })
      qc.invalidateQueries({ queryKey: ['ledger_stats'] })
      setTicker('')
      setTags('')
      setWhy('')
    },
  })

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <header>
        <h2 className="text-lg font-semibold text-text">Log a combo</h2>
        <p className="mt-1 text-sm text-text-muted">
          Paste the full Kalshi combo ticker. Legs, entry price, quantity, and
          fee are pulled from Kalshi automatically — you just add the strategy
          and your reasoning. Combos settle on their own through the ledger.
        </p>
      </header>

      <form
        onSubmit={(e) => {
          e.preventDefault()
          if (ticker.trim()) mut.mutate()
        }}
        className="space-y-4 rounded-md border border-border bg-bg-panel p-4"
      >
        <Field label="Combo ticker">
          <input
            value={ticker}
            onChange={(e) => setTicker(e.target.value)}
            placeholder="KXMVESPORTSMULTIGAMEEXTENDED-S…-…"
            spellCheck={false}
            className="w-full rounded border border-border bg-bg px-3 py-2 font-mono text-sm text-text outline-none focus:border-action"
          />
        </Field>

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
        </div>

        <Field label="Tags (comma-separated, optional)">
          <input
            value={tags}
            onChange={(e) => setTags(e.target.value)}
            placeholder="friendlies, 5-leg"
            className="w-full rounded border border-border bg-bg px-3 py-2 text-sm text-text outline-none focus:border-action"
          />
        </Field>

        <Field label="Why (optional)">
          <textarea
            value={why}
            onChange={(e) => setWhy(e.target.value)}
            rows={2}
            placeholder="The thinking behind this parlay…"
            className="w-full rounded border border-border bg-bg px-3 py-2 text-sm text-text outline-none focus:border-action"
          />
        </Field>

        <button
          type="submit"
          disabled={!ticker.trim() || mut.isPending}
          className="rounded bg-action px-4 py-2 text-sm font-semibold text-bg disabled:cursor-not-allowed disabled:opacity-40"
        >
          {mut.isPending ? 'Logging…' : 'Log combo'}
        </button>

        {mut.isError && (
          <div className="rounded border border-loss/40 bg-loss/10 px-3 py-2 text-xs text-loss">
            {mut.error.message}
          </div>
        )}
      </form>

      {mut.isSuccess && mut.data && <LoggedResult result={mut.data} />}
    </div>
  )
}

function LoggedResult({ result }: { result: ComboLogResult }) {
  return (
    <div className="rounded-md border border-gain/40 bg-gain/5 p-4">
      <div className="mb-2 text-sm font-semibold text-gain">
        Logged — bet #{result.bet_id}
      </div>
      <div className="mb-3 font-mono text-xs text-text-muted">
        {result.side.toUpperCase()} · {result.quantity} ×{' '}
        {result.entry_price_cents}¢ · stake {formatDollars(result.stake_cents)} ·{' '}
        {result.leg_count} legs
      </div>
      <ul className="divide-y divide-border rounded border border-border bg-bg-card">
        {result.legs.map((leg, i) => (
          <li
            key={i}
            className="flex items-center justify-between px-3 py-1.5 text-xs"
          >
            <span className="text-text">{leg.title ?? leg.ticker}</span>
            <span className="font-mono uppercase text-text-muted">{leg.side}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs uppercase tracking-wide text-text-muted">
        {label}
      </span>
      {children}
    </label>
  )
}

function Segmented({
  options,
  value,
  onChange,
}: {
  options: readonly string[]
  value: string
  onChange: (v: string) => void
}) {
  return (
    <div className="flex gap-1">
      {options.map((opt) => (
        <button
          key={opt}
          type="button"
          onClick={() => onChange(opt)}
          className={`flex-1 rounded border px-2 py-1.5 text-xs ${
            value === opt
              ? 'border-action bg-action/10 text-text'
              : 'border-border text-text-muted hover:bg-bg-hover'
          }`}
        >
          {opt.replace('_', ' ')}
        </button>
      ))}
    </div>
  )
}
