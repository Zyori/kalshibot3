import { formatDollars } from '../../lib/format'
import {
  estimateComboPriceCents,
  estimatePayoutCents,
  estimateProfitCents,
} from '../../lib/combo-odds'
import {
  COMBO_STRATEGIES,
  type ComboStrategy,
  Field,
  Segmented,
} from './ComboFields'
import type { Materialized, SlipLeg } from './types'

/**
 * The sticky bet slip. Shows the legs the user picked, a live ESTIMATE of the
 * combo price/payout (real price arrives on Stage), then the limit price +
 * count + Stage→Confirm flow. Pure presentational — the shell owns state and
 * the stage/place mutations.
 */
export default function ComboSlip({
  legs,
  onRemove,
  strategy,
  setStrategy,
  price,
  setPrice,
  count,
  setCount,
  why,
  setWhy,
  staged,
  onStage,
  staging,
  stageError,
  onPlace,
  placing,
  placeError,
  placed,
}: {
  legs: SlipLeg[]
  onRemove: (marketTicker: string) => void
  strategy: ComboStrategy
  setStrategy: (s: ComboStrategy) => void
  price: string
  setPrice: (s: string) => void
  count: string
  setCount: (s: string) => void
  why: string
  setWhy: (s: string) => void
  staged: Materialized | null
  onStage: () => void
  staging: boolean
  stageError: string | null
  onPlace: () => void
  placing: boolean
  placeError: string | null
  placed: { bet_id: number; quantity: number; entry_price_cents: number; stake_cents: number } | null
}) {
  const estPrice = estimateComboPriceCents(legs.map((l) => l.price_cents))
  const countN = Number(count)
  const priceN = Number(price)
  const stakeCents = priceN > 0 && countN > 0 ? priceN * countN : 0
  const canStage = legs.length >= 2
  const canPlace = staged && priceN >= 1 && priceN <= 99 && countN >= 1

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

      {/* Live ESTIMATE — replaced by Kalshi's real price on Stage. */}
      {legs.length >= 2 && (
        <div className="mb-3 rounded border border-border bg-bg-card px-3 py-2 text-xs">
          {staged ? (
            <div>
              <div className="text-text-muted">Staged price (Kalshi)</div>
              <div className="font-mono tabular-nums text-text">
                yes {staged.yes_bid_cents ?? '—'}/{staged.yes_ask_cents ?? '—'}¢
                {staged.yes_bid_cents === null && (
                  <span className="ml-1 text-action">no book — set a limit</span>
                )}
              </div>
            </div>
          ) : (
            <div>
              <div className="flex items-baseline justify-between">
                <span className="text-text-muted">Est. price</span>
                <span className="font-mono tabular-nums text-text">
                  {estPrice !== null ? `~${estPrice}¢` : '—'}
                </span>
              </div>
              {estPrice !== null && countN >= 1 && (
                <div className="mt-0.5 flex items-baseline justify-between">
                  <span className="text-text-muted">Est. payout if it hits</span>
                  <span className="font-mono tabular-nums text-gain">
                    {formatDollars(estimatePayoutCents(countN))}
                    <span className="ml-1 text-[10px] text-text-muted">
                      (+{formatDollars(estimateProfitCents(priceN > 0 ? priceN : estPrice, countN))})
                    </span>
                  </span>
                </div>
              )}
              <div className="mt-1 text-[10px] text-text-muted">
                estimate — real price shown when you stage
              </div>
            </div>
          )}
        </div>
      )}

      <div className="space-y-3">
        <Field label="Strategy">
          <Segmented options={COMBO_STRATEGIES} value={strategy} onChange={setStrategy} />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Limit price (¢)">
            <input
              value={price}
              onChange={(e) => setPrice(e.target.value)}
              inputMode="numeric"
              placeholder={estPrice !== null ? String(estPrice) : '5'}
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
        {stakeCents > 0 && (
          <div className="font-mono text-xs text-text-muted">
            Stake: {formatDollars(stakeCents)} ({countN} × {priceN}¢)
          </div>
        )}
        <Field label="Why (optional)">
          <textarea
            value={why}
            onChange={(e) => setWhy(e.target.value)}
            rows={2}
            className="w-full rounded border border-border bg-bg px-3 py-2 text-sm text-text outline-none focus:border-action"
          />
        </Field>

        {!staged ? (
          <button
            type="button"
            onClick={onStage}
            disabled={!canStage || staging}
            className="w-full rounded border border-action bg-action/10 px-4 py-2 text-sm font-semibold text-text disabled:cursor-not-allowed disabled:opacity-40"
          >
            {staging ? 'Staging…' : `Stage parlay (${legs.length} legs)`}
          </button>
        ) : (
          <button
            type="button"
            onClick={onPlace}
            disabled={!canPlace || placing}
            className="w-full rounded bg-action px-4 py-2 text-sm font-semibold text-bg disabled:cursor-not-allowed disabled:opacity-40"
          >
            {placing ? 'Placing…' : 'Confirm & place'}
          </button>
        )}

        {stageError && (
          <div className="rounded border border-loss/40 bg-loss/10 px-3 py-2 text-xs text-loss">
            {stageError}
          </div>
        )}
        {placeError && (
          <div className="rounded border border-loss/40 bg-loss/10 px-3 py-2 text-xs text-loss">
            {placeError}
          </div>
        )}
        {placed && (
          <div className="rounded border border-gain/40 bg-gain/5 px-3 py-2 text-xs text-gain">
            Placed — bet #{placed.bet_id}: {placed.quantity} × {placed.entry_price_cents}¢,
            stake {formatDollars(placed.stake_cents)}
          </div>
        )}
      </div>
    </div>
  )
}
