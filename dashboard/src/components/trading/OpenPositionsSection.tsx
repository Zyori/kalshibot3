import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router'

import InlineError from '../InlineError'
import Skeleton from '../Skeleton'
import { SportBadge } from '../ledger/SportBadge'
import { badgeSport } from '../../lib/sport'
import { formatPriceCents, formatSignedDollars } from '../../lib/format'

type Position = {
  ticker: string
  label: string | null
  sport: string
  leg_sport: string | null
  side: 'yes' | 'no'
  quantity: number
  avg_entry_price_cents: number | null
  avg_entry_price: number | null
  current_price_cents: number | null
  unrealized_pnl_cents: number | null
}

type PositionsResponse = { positions: Position[] }

/**
 * Open positions at a glance, on the overview. One card per position with its
 * current price highlighted and unrealized P&L color-coded. Shares the
 * ['positions'] query key, so the WS `position_synced` event (which invalidates
 * it) refreshes these the moment a fill/settlement reconciles — the 15s poll is
 * a backstop, not the primary path.
 *
 * Combos and soccer singles both appear; a combo card links to /combos, a
 * soccer card to its market page.
 */
export default function OpenPositionsSection() {
  const { data, isPending, isError, error } = useQuery<PositionsResponse>({
    queryKey: ['positions'],
    queryFn: async () => {
      const res = await fetch('/api/positions')
      if (!res.ok) throw new Error(`/api/positions: ${res.status}`)
      return res.json()
    },
    refetchInterval: 15_000,
  })

  if (isError) {
    return (
      <section>
        <SectionHeader count={null} />
        <InlineError message="Couldn't load positions." detail={error} />
      </section>
    )
  }

  if (isPending) {
    return (
      <section>
        <SectionHeader count={null} />
        <div className="grid grid-cols-2 gap-2 lg:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} height={92} />
          ))}
        </div>
      </section>
    )
  }

  const positions = data.positions
  if (positions.length === 0) return null

  return (
    <section>
      <SectionHeader count={positions.length} />
      <div className="grid grid-cols-2 gap-2 lg:grid-cols-3">
        {positions.map((p) => (
          <PositionCardItem key={`${p.ticker}:${p.side}`} position={p} />
        ))}
      </div>
    </section>
  )
}

function SectionHeader({ count }: { count: number | null }) {
  return (
    <div className="mb-2 flex items-baseline justify-between">
      <h3 className="text-sm font-semibold text-text">Open positions</h3>
      {count !== null && (
        <span className="text-xs text-text-muted">
          {count} {count === 1 ? 'position' : 'positions'}
        </span>
      )}
    </div>
  )
}

function PositionCardItem({ position: p }: { position: Position }) {
  const pnl = p.unrealized_pnl_cents
  const pnlTone =
    pnl === null ? 'text-text-muted' : pnl >= 0 ? 'text-gain' : 'text-loss'
  const href =
    p.sport === 'combo' ? '/combos' : `/market/${encodeURIComponent(p.ticker)}`
  const title = p.label ?? p.ticker

  return (
    <Link
      to={href}
      className="block rounded-lg border border-border bg-bg-card p-3 transition-colors hover:bg-bg-hover"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 items-center gap-1.5">
          <SportBadge sport={badgeSport(p.sport, p.leg_sport)} compact />
          <span className="min-w-0 truncate text-sm text-text" title={title}>
            {title}
          </span>
        </div>
        <span
          className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${
            p.side === 'yes' ? 'text-gain' : 'text-loss'
          }`}
        >
          {p.side}
        </span>
      </div>

      <div className="mt-2 flex items-baseline justify-between gap-2 text-xs">
        <span className="text-text-muted">
          {p.quantity} @ {formatPriceCents(p.avg_entry_price ?? p.avg_entry_price_cents)}
        </span>
        {/* Current price — the highlighted figure. */}
        <span className="font-mono tabular-nums text-sm font-semibold text-text">
          {formatPriceCents(p.current_price_cents)}
        </span>
      </div>

      <div className="mt-1 text-right">
        <span className={`font-mono tabular-nums text-sm ${pnlTone}`}>
          {pnl === null ? '—' : formatSignedDollars(pnl)}
        </span>
      </div>
    </Link>
  )
}
