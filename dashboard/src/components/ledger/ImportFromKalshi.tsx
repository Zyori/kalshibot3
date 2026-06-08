import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import InlineError from '../InlineError'
import { formatET, formatPriceCents, formatSignedDollars } from '../../lib/format'

// One unlinked Kalshi position — all buys + closing sells on a (ticker, held
// side) folded together (matches the backend ImportablePosition model).
type ImportablePosition = {
  key: string
  ticker: string
  label: string | null
  side: 'yes' | 'no'
  bought_quantity: number
  held_quantity: number
  entry_price_cents: number
  exit_price_cents: number | null
  realized_pnl_cents: number | null
  placed_at: string | null
  resolved: boolean
  result: 'yes' | 'no' | null
}

type ImportedRow = {
  bet_id: number
  ticker: string
  side: string
  quantity: number
  held_quantity: number
  status: string
  realized_pnl_cents: number | null
}

// Did this resolved position win, from the holder's perspective? YES holder
// wins on a "yes" result, NO holder on "no". Null when the outcome isn't known
// (unresolved, or resolved without a result) — drives the ✓/✗ on a settled row.
function won(p: ImportablePosition): boolean | null {
  if (!p.resolved || p.result === null) return null
  return p.side === p.result
}

// Three-way P&L color, matching the rest of the ledger: green gain, red loss,
// muted breakeven (0 is not a gain).
function pnlTone(cents: number): string {
  if (cents > 0) return 'text-gain'
  if (cents < 0) return 'text-loss'
  return 'text-text-muted'
}

export default function ImportFromKalshi({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient()
  const [selected, setSelected] = useState<Set<string>>(new Set())

  const importable = useQuery<{ positions: ImportablePosition[] }>({
    queryKey: ['ledger_importable'],
    queryFn: async () => {
      const res = await fetch('/api/ledger/importable')
      if (!res.ok) throw new Error(`/api/ledger/importable: ${res.status}`)
      return res.json()
    },
    // A fresh scan every time the modal opens. The list reflects what's on
    // Kalshi vs the ledger right now — both move (you place bets, you import) —
    // so a cached result from earlier in the session is misleading (and, across
    // a deploy, can even be the wrong response shape). Don't serve a stale one.
    staleTime: 0,
    refetchOnMount: 'always',
    refetchOnWindowFocus: false,
  })

  const importMut = useMutation<{ imported: ImportedRow[]; skipped: string[] }>({
    mutationFn: async () => {
      const res = await fetch('/api/ledger/import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ keys: [...selected] }),
      })
      if (!res.ok) throw new Error(`/api/ledger/import: ${res.status}`)
      return res.json()
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['ledger'] })
      qc.invalidateQueries({ queryKey: ['ledger_stats'] })
      // The imported positions are no longer importable — drop the cached scan
      // so the next open re-fetches without them.
      qc.invalidateQueries({ queryKey: ['ledger_importable'] })
    },
  })

  const positions = importable.data?.positions ?? []
  const toggle = (key: string) =>
    setSelected((s) => {
      const next = new Set(s)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })

  const result = importMut.data

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-bg/80 p-4">
      <div className="flex max-h-[80vh] w-full max-w-2xl flex-col rounded-lg border border-border bg-bg-card shadow-2xl">
        <header className="flex items-start justify-between border-b border-border p-5">
          <div>
            <h3 className="text-base font-semibold text-text">Import from Kalshi</h3>
            <p className="mt-1 text-xs text-text-muted">
              Match bets you placed on kalshi.com in the last 2 weeks that aren't
              on your ledger yet — buys and any sells you closed with, folded into
              one position each. Pick the ones to add.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-border bg-bg-hover px-2.5 py-1 text-sm text-text-muted hover:text-text"
          >
            Close
          </button>
        </header>

        <div className="flex-1 overflow-y-auto p-5">
          {result ? (
            <ImportResult result={result} />
          ) : importable.isError ? (
            <InlineError message="Couldn't scan Kalshi." detail={importable.error} />
          ) : importable.isPending ? (
            <p className="py-8 text-center text-sm text-text-muted">Scanning…</p>
          ) : positions.length === 0 ? (
            <p className="py-8 text-center text-sm text-text-muted">
              Nothing to import — every recent Kalshi match bet is already on your
              ledger.
            </p>
          ) : (
            <ul className="space-y-2">
              {positions.map((p) => (
                <PositionRow
                  key={p.key}
                  position={p}
                  checked={selected.has(p.key)}
                  onToggle={() => toggle(p.key)}
                />
              ))}
            </ul>
          )}
        </div>

        {!result && positions.length > 0 && (
          <footer className="flex items-center justify-between border-t border-border p-5">
            <span className="text-xs text-text-muted">{selected.size} selected</span>
            <div className="flex gap-2">
              {importMut.isError && (
                <span className="self-center text-xs text-loss">
                  Import failed — try again.
                </span>
              )}
              <button
                type="button"
                disabled={selected.size === 0 || importMut.isPending}
                onClick={() => importMut.mutate()}
                className="rounded-md border border-action bg-action px-3 py-1.5 text-sm font-semibold text-bg disabled:opacity-40"
              >
                {importMut.isPending ? 'Adding…' : `Add ${selected.size || ''} to ledger`}
              </button>
            </div>
          </footer>
        )}

        {result && (
          <footer className="flex justify-end border-t border-border p-5">
            <button
              type="button"
              onClick={onClose}
              className="rounded-md border border-border bg-bg-hover px-3 py-1.5 text-sm text-text"
            >
              Done
            </button>
          </footer>
        )}
      </div>
    </div>
  )
}

function PositionRow({
  position: p,
  checked,
  onToggle,
}: {
  position: ImportablePosition
  checked: boolean
  onToggle: () => void
}) {
  const w = won(p)
  // How the position stands: fully closed (held 0), partly closed, or still open.
  const closed = p.held_quantity === 0
  const partlyClosed = !closed && p.bought_quantity > p.held_quantity
  return (
    <li>
      <label className="flex cursor-pointer items-center gap-3 rounded-md border border-border bg-bg p-3 hover:bg-bg-hover">
        <input
          type="checkbox"
          checked={checked}
          onChange={onToggle}
          className="accent-action"
        />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm text-text">{p.label ?? p.ticker}</span>
            <span className="shrink-0 text-xs uppercase text-text-muted">{p.side}</span>
            {w !== null && (
              <span
                className={`shrink-0 text-xs font-semibold ${w ? 'text-gain' : 'text-loss'}`}
              >
                {w ? '✓ won' : '✗ lost'}
              </span>
            )}
          </div>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-2 font-mono text-xs tabular-nums text-text-muted">
            <span>
              {p.bought_quantity} @ {formatPriceCents(p.entry_price_cents)}
            </span>
            {p.exit_price_cents !== null && (
              <span>→ sold {formatPriceCents(p.exit_price_cents)}</span>
            )}
            {closed && <span className="text-text">closed</span>}
            {partlyClosed && <span>{p.held_quantity} still held</span>}
            {p.realized_pnl_cents !== null && (
              <span className={pnlTone(p.realized_pnl_cents)}>
                {formatSignedDollars(p.realized_pnl_cents)}
              </span>
            )}
            {p.placed_at && <span>· {formatET(p.placed_at)}</span>}
          </div>
        </div>
      </label>
    </li>
  )
}

function ImportResult({
  result,
}: {
  result: { imported: ImportedRow[]; skipped: string[] }
}) {
  return (
    <div className="space-y-3">
      <p className="text-sm text-text">
        Added {result.imported.length} position
        {result.imported.length === 1 ? '' : 's'} to the ledger.
      </p>
      {result.skipped.length > 0 && (
        <p className="text-xs text-text-muted">
          Skipped {result.skipped.length} (no matching match-market buy found).
        </p>
      )}
      <ul className="space-y-1.5">
        {result.imported.map((r) => (
          <li
            key={r.bet_id}
            className="flex items-center justify-between gap-2 rounded-md border border-border bg-bg px-3 py-2 text-sm"
          >
            <span className="truncate text-text">{r.ticker}</span>
            <span className="flex shrink-0 items-center gap-2">
              {r.realized_pnl_cents !== null && (
                <span
                  className={`font-mono tabular-nums text-xs ${pnlTone(r.realized_pnl_cents)}`}
                >
                  {formatSignedDollars(r.realized_pnl_cents)}
                </span>
              )}
              <span className="text-xs uppercase text-text-muted">{r.status}</span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}
