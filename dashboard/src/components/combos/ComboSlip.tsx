import { formatDollars } from '../../lib/format'
import { estimateComboPriceCents } from '../../lib/combo-odds'
import {
  COMBO_STRATEGIES,
  type ComboStrategy,
  Field,
  Segmented,
} from './ComboFields'
import type { Quote, SlipLeg } from './types'

/**
 * The sticky bet slip. Shows the picked legs + a live ESTIMATE, then the RFQ
 * flow: set a size, Request a quote, and accept the best quote market makers
 * offer. Accepting is the human-confirmed action that places the order.
 * Pure presentational — the shell owns state + mutations.
 */
export default function ComboSlip({
  legs,
  onRemove,
  strategy,
  setStrategy,
  count,
  setCount,
  why,
  setWhy,
  rfqOpen,
  onRequestQuote,
  requesting,
  requestError,
  quotes,
  quotesLoading,
  onAccept,
  accepting,
  acceptError,
  accepted,
}: {
  legs: SlipLeg[]
  onRemove: (marketTicker: string) => void
  strategy: ComboStrategy
  setStrategy: (s: ComboStrategy) => void
  count: string
  setCount: (s: string) => void
  why: string
  setWhy: (s: string) => void
  rfqOpen: boolean
  onRequestQuote: () => void
  requesting: boolean
  requestError: string | null
  quotes: Quote[]
  quotesLoading: boolean
  onAccept: (quote: Quote, side: 'yes' | 'no') => void
  accepting: boolean
  acceptError: string | null
  accepted: { accepted: boolean; side: 'yes' | 'no'; count: number; note: string } | null
}) {
  const estPrice = estimateComboPriceCents(legs.map((l) => l.price_cents))
  const countN = Number(count)
  const canRequest = legs.length >= 2 && countN >= 1

  return (
    <div className="sticky top-6 self-start rounded-md border border-border bg-bg-panel p-4">
      <div className="mb-3 text-sm font-semibold text-text">Your parlay</div>

      {legs.length === 0 ? (
        <p className="rounded border border-dashed border-border px-3 py-6 text-center text-xs text-text-muted">
          Click an outcome on the left to add a leg.
        </p>
      ) : (
        <ul className="mb-3 divide-y divide-border rounded border border-border bg-bg-card">
          {legs.map((leg) => (
            <li
              key={leg.market_ticker}
              className="flex items-center justify-between gap-2 px-3 py-1.5 text-xs"
            >
              <span className="min-w-0 truncate text-text">{leg.title}</span>
              <span className="flex shrink-0 items-baseline gap-2">
                <span className="font-mono tabular-nums text-text-muted">
                  {leg.price_cents !== null ? `${leg.price_cents}¢` : '—'}
                </span>
                <button
                  type="button"
                  onClick={() => onRemove(leg.market_ticker)}
                  className="text-text-muted hover:text-loss"
                  title="Remove leg"
                >
                  ×
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}

      {legs.length >= 2 && estPrice !== null && (
        <div className="mb-3 flex items-baseline justify-between rounded border border-border bg-bg-card px-3 py-2 text-xs">
          <span className="text-text-muted">Est. price</span>
          <span className="font-mono tabular-nums text-text">
            ~{estPrice}¢
            <span className="ml-1 text-[10px] text-text-muted">(real on quote)</span>
          </span>
        </div>
      )}

      <div className="space-y-3">
        <Field label="Strategy">
          <Segmented options={COMBO_STRATEGIES} value={strategy} onChange={setStrategy} />
        </Field>
        <Field label="Contracts">
          <input
            value={count}
            onChange={(e) => setCount(e.target.value)}
            inputMode="numeric"
            placeholder="10"
            disabled={rfqOpen}
            className="w-full rounded border border-border bg-bg px-3 py-2 font-mono text-sm text-text outline-none focus:border-action disabled:opacity-50"
          />
        </Field>
        <Field label="Why (optional)">
          <textarea
            value={why}
            onChange={(e) => setWhy(e.target.value)}
            rows={2}
            disabled={rfqOpen}
            className="w-full rounded border border-border bg-bg px-3 py-2 text-sm text-text outline-none focus:border-action disabled:opacity-50"
          />
        </Field>

        {!rfqOpen ? (
          <button
            type="button"
            onClick={onRequestQuote}
            disabled={!canRequest || requesting}
            className="w-full rounded bg-action px-4 py-2 text-sm font-semibold text-bg disabled:cursor-not-allowed disabled:opacity-40"
          >
            {requesting ? 'Requesting…' : `Request quote (${legs.length} legs)`}
          </button>
        ) : (
          <QuotesPanel
            quotes={quotes}
            loading={quotesLoading}
            countN={countN}
            onAccept={onAccept}
            accepting={accepting}
          />
        )}

        {requestError && <ErrBox>{requestError}</ErrBox>}
        {acceptError && <ErrBox>{acceptError}</ErrBox>}
        {accepted && (
          <div className="rounded border border-gain/40 bg-gain/5 px-3 py-2 text-xs text-gain">
            Accepted {accepted.count} {accepted.side.toUpperCase()}. {accepted.note}
          </div>
        )}
      </div>
    </div>
  )
}

function QuotesPanel({
  quotes,
  loading,
  countN,
  onAccept,
  accepting,
}: {
  quotes: Quote[]
  loading: boolean
  countN: number
  onAccept: (quote: Quote, side: 'yes' | 'no') => void
  accepting: boolean
}) {
  // Best YES = lowest cost to back the parlay; best NO = back against it.
  const sorted = [...quotes].sort((a, b) => {
    const ay = a.yes_bid_cents || 999
    const by = b.yes_bid_cents || 999
    return ay - by
  })
  return (
    <div className="rounded border border-action/40 bg-action/5 p-2">
      <div className="mb-2 flex items-center justify-between px-1 text-xs">
        <span className="font-semibold text-text">
          {quotes.length > 0 ? `${quotes.length} quotes` : 'Waiting for quotes…'}
        </span>
        <span className="text-text-muted">live</span>
      </div>
      {loading && quotes.length === 0 && (
        <div className="h-12 animate-pulse rounded bg-bg-card" />
      )}
      {!loading && quotes.length === 0 && (
        <p className="px-1 py-2 text-[11px] text-text-muted">
          No quotes yet — market makers usually respond within seconds.
        </p>
      )}
      <ul className="space-y-1">
        {sorted.map((q) => (
          <li
            key={q.quote_id}
            className="flex items-center justify-between gap-2 rounded border border-border bg-bg-card px-2 py-1.5 text-xs"
          >
            <div className="flex gap-3 font-mono tabular-nums">
              {q.yes_bid_cents > 0 && (
                <button
                  type="button"
                  disabled={accepting}
                  onClick={() => onAccept(q, 'yes')}
                  className="rounded border border-gain/50 px-2 py-0.5 text-gain hover:bg-gain/10 disabled:opacity-40"
                  title={`Buy YES at ${q.yes_bid_cents}¢ · ${formatDollars(q.yes_bid_cents * countN)}`}
                >
                  YES {q.yes_bid_cents}¢
                </button>
              )}
              {q.no_bid_cents > 0 && (
                <button
                  type="button"
                  disabled={accepting}
                  onClick={() => onAccept(q, 'no')}
                  className="rounded border border-loss/50 px-2 py-0.5 text-loss hover:bg-loss/10 disabled:opacity-40"
                  title={`Buy NO at ${q.no_bid_cents}¢ · ${formatDollars(q.no_bid_cents * countN)}`}
                >
                  NO {q.no_bid_cents}¢
                </button>
              )}
            </div>
          </li>
        ))}
      </ul>
      <p className="mt-2 px-1 text-[10px] text-text-muted">
        Click a price to accept — that places the order ({countN} contracts).
      </p>
    </div>
  )
}

function ErrBox({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded border border-loss/40 bg-loss/10 px-3 py-2 text-xs text-loss">
      {children}
    </div>
  )
}
