import PriceCell from './PriceCell'
import type { MarketBook } from '../../contexts/WebSocketProvider'
import { bestAsk, bestBid } from '../../lib/book'

/**
 * Big top-of-book strip. Shows YES bid/ask and NO bid/ask, with flashes
 * on price change. This is the data the user looks at when deciding what
 * to do — make it scannable and unambiguous.
 *
 * Spread is shown small under each side as a sanity-check at a glance.
 */
export default function TopOfBook({ book }: { book: MarketBook | undefined }) {
  const yesBid = bestBid(book, 'yes')
  const yesAsk = bestAsk(book, 'yes')
  const noBid = bestBid(book, 'no')
  const noAsk = bestAsk(book, 'no')

  return (
    <div className="grid gap-3 md:grid-cols-2">
      <Side label="YES" bid={yesBid} ask={yesAsk} />
      <Side label="NO" bid={noBid} ask={noAsk} />
    </div>
  )
}

function Side({
  label,
  bid,
  ask,
}: {
  label: 'YES' | 'NO'
  bid: number | null
  ask: number | null
}) {
  const spread = bid !== null && ask !== null && ask > bid ? ask - bid : null
  return (
    <div className="rounded-lg border border-border bg-bg-card p-4">
      <div className="mb-2 flex items-baseline justify-between">
        <span className="text-xs font-semibold tracking-wide text-text-muted">
          {label}
        </span>
        {spread !== null && (
          <span className="text-xs text-text-muted">spread {spread}¢</span>
        )}
      </div>
      <div className="grid grid-cols-2 gap-4">
        <Field label="Bid" value={bid} large />
        <Field label="Ask" value={ask} large />
      </div>
    </div>
  )
}

function Field({
  label,
  value,
  large,
}: {
  label: string
  value: number | null
  large?: boolean
}) {
  return (
    <div>
      <div className="text-xs text-text-muted">{label}</div>
      <PriceCell value={value} className={large ? 'text-2xl' : 'text-base'} />
    </div>
  )
}

