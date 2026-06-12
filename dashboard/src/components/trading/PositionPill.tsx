/**
 * The held-position pill shown in a market card header (moneyline child or
 * total-goals rung). Side + quantity + all-in entry + unrealized P&L. Single
 * source for this pill so every card that surfaces a held position reads the
 * same.
 */
import { formatPriceCents, formatSignedDollars } from '../../lib/format'
import type { ChildPosition } from '../../lib/types'

export default function PositionPill({ position: p }: { position: ChildPosition }) {
  const pnl = p.unrealized_pnl_cents
  const tone =
    pnl === null ? 'text-text-muted' : pnl >= 0 ? 'text-gain' : 'text-loss'
  // Profit/loss already banked on the shares sold so far (Kalshi's realized
  // PnL for this still-open position). Only shown once something's been sold —
  // a fresh, untouched position has realized 0 and would just be noise.
  const realized = p.realized_pnl_cents
  const realizedTone = realized !== null && realized >= 0 ? 'text-gain' : 'text-loss'
  // avg_entry_price is the fee-inclusive all-in cost basis (matches kalshi.com):
  // (cost + Kalshi fees) / quantity. It runs ~1-2¢ above the raw fill price on
  // taker fills, so we label it "all-in" and show the raw fill price + the
  // fee-inclusive basis on hover — otherwise a clean 31¢ fill reads as 32.4¢
  // and looks like a bad fill when it's just the taker fee folded in.
  const allIn = p.avg_entry_price ?? p.avg_entry_price_cents
  const rawFill = p.avg_entry_price_cents
  const feeNote =
    p.avg_entry_price != null && rawFill != null
      ? `Fill ${formatPriceCents(rawFill)} + Kalshi fee = ${formatPriceCents(p.avg_entry_price)} all-in`
      : undefined
  return (
    <span className="flex items-center gap-2 rounded-full bg-action/10 px-2 py-0.5 text-[11px] text-action">
      <span className="font-mono tabular-nums" title={feeNote}>
        {p.side.toUpperCase()} {p.quantity} @ {formatPriceCents(allIn)}
        <span className="ml-0.5 text-[9px] text-text-muted">all-in</span>
      </span>
      {pnl !== null && (
        <span className={`font-mono tabular-nums ${tone}`}>
          {formatSignedDollars(pnl)}
        </span>
      )}
      {realized !== null && realized !== 0 && (
        <span
          className={`font-mono tabular-nums ${realizedTone}`}
          title="Realized P&L — locked in on shares already sold"
        >
          {formatSignedDollars(realized)}
          <span className="ml-0.5 text-[9px] text-text-muted">locked</span>
        </span>
      )}
    </span>
  )
}
