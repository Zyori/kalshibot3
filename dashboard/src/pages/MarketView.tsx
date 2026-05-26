import { Link, useParams } from 'react-router'
import { useQuery } from '@tanstack/react-query'
import type { MarketBook } from '../contexts/WebSocketProvider'

type MarketDetail = {
  ticker: string
  status: string
  yes: Array<{ price: number; qty: number }>
  no: Array<{ price: number; qty: number }>
  yes_best_bid: number | null
  yes_best_ask: number | null
  no_best_bid: number | null
  no_best_ask: number | null
  last_update_ago_s: number | null
}

/**
 * Placeholder market page. Chunk 11 builds the full live book + chart +
 * order panel here; for now we just confirm the cold-bootstrap REST call
 * works and show whatever LiveState has on file.
 */
export default function MarketView() {
  const { ticker = '' } = useParams<{ ticker: string }>()
  const decoded = decodeURIComponent(ticker)

  // Cold bootstrap from REST. The WS provider will populate the ['book',
  // ticker] cache as deltas arrive; chunk 11 reads from there instead.
  const { data, isPending, isError, error } = useQuery<MarketDetail>({
    queryKey: ['market', decoded],
    queryFn: async () => {
      const res = await fetch(`/api/markets/${encodeURIComponent(decoded)}`)
      if (!res.ok) {
        const body = await res.text()
        throw new Error(`${res.status}: ${body}`)
      }
      return res.json()
    },
  })

  // Read the live book from the WS cache too — once chunk 11 lands this
  // becomes the primary path.
  const liveBook = useQuery<MarketBook | undefined>({
    queryKey: ['book', decoded],
    queryFn: async () => undefined, // never fetched; populated by WS
    enabled: false,
    staleTime: Infinity,
  }).data

  return (
    <div className="space-y-4">
      <header>
        <Link to="/" className="text-xs text-text-muted hover:text-text">← Markets</Link>
        <h2 className="mt-2 font-mono text-lg text-text">{decoded}</h2>
      </header>

      {isPending && <div className="text-sm text-text-muted">Loading…</div>}
      {isError && (
        <div className="rounded-md border border-loss bg-bg-card p-4 text-sm text-loss">
          {String(error)}
        </div>
      )}

      {data && (
        <div className="grid gap-4 md:grid-cols-2">
          <BookCard title="YES side" levels={liveBook ? sideToList(liveBook.yes) : data.yes} />
          <BookCard title="NO side"  levels={liveBook ? sideToList(liveBook.no)  : data.no}  />
        </div>
      )}

      <div className="rounded-md border border-border bg-bg-card p-4 text-sm text-text-muted">
        Order panel + chart land in chunk 11. For now: this confirms the live
        book is reaching the browser. Watch the YES/NO levels update — once
        the WS pipes a delta, the lists rerender without a refetch.
      </div>
    </div>
  )
}

function sideToList(side: Record<number, number>): Array<{ price: number; qty: number }> {
  return Object.entries(side)
    .map(([p, q]) => ({ price: Number(p), qty: q }))
    .sort((a, b) => b.price - a.price)
}

function BookCard({
  title,
  levels,
}: {
  title: string
  levels: Array<{ price: number; qty: number }>
}) {
  return (
    <div className="rounded-md border border-border bg-bg-card p-4">
      <h3 className="mb-2 text-sm font-semibold text-text">{title}</h3>
      {levels.length === 0 ? (
        <p className="text-xs text-text-muted">No resting liquidity.</p>
      ) : (
        <ul className="space-y-1 font-mono text-xs tabular-nums text-text">
          {levels.slice(0, 8).map((l) => (
            <li key={l.price} className="flex justify-between">
              <span>{l.price}¢</span>
              <span className="text-text-muted">{l.qty.toLocaleString()}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
