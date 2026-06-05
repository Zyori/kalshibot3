import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import { errorMessage, type ComboStrategy } from './ComboFields'
import ComboSlip from './ComboSlip'
import MarketBrowser from './MarketBrowser'
import type { SlipLeg } from './types'

type Materialized = {
  ticker: string
  subtitle: string | null
  yes_bid_cents: number | null
  yes_ask_cents: number | null
  leg_count: number
}

type PlaceResult = {
  bet_id: number
  quantity: number
  entry_price_cents: number
  stake_cents: number
}

// The leg shape the API expects — SlipLeg minus the display-only fields.
function toApiLeg(l: SlipLeg) {
  return { market_ticker: l.market_ticker, event_ticker: l.event_ticker, side: l.side }
}

/**
 * Build a combo by browsing markets and clicking outcomes into a sticky slip.
 * The slip shows a live estimate as the parlay grows; Stage materializes the
 * real Kalshi combo, then Confirm places a limit order. Mirrors every order's
 * human-confirmed flow.
 */
export default function ComboBuilder() {
  const qc = useQueryClient()
  const [legs, setLegs] = useState<SlipLeg[]>([])
  const [strategy, setStrategy] = useState<ComboStrategy>('lock_parlay')
  const [price, setPrice] = useState('')
  const [count, setCount] = useState('')
  const [why, setWhy] = useState('')
  const [staged, setStaged] = useState<Materialized | null>(null)

  const apiLegs = legs.map(toApiLeg)

  function addLeg(leg: SlipLeg) {
    setLegs((prev) =>
      prev.some((l) => l.market_ticker === leg.market_ticker) ? prev : [...prev, leg],
    )
    setStaged(null) // changing legs invalidates the staged combo
  }
  function removeLeg(marketTicker: string) {
    setLegs((prev) => prev.filter((l) => l.market_ticker !== marketTicker))
    setStaged(null)
  }

  const stage = useMutation<Materialized, Error>({
    mutationFn: async () => {
      const res = await fetch('/api/combos/materialize', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ legs: apiLegs }),
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
          legs: apiLegs,
          side: 'yes',
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
      setLegs([])
      setPrice('')
      setCount('')
      setWhy('')
    },
  })

  const selected = new Set(legs.map((l) => l.market_ticker))

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_340px]">
      <div className="min-w-0">
        <p className="mb-3 text-sm text-text-muted">
          Click outcomes to build your parlay — the slip on the right shows the
          running estimate. Stage to see the real Kalshi price, then confirm.
        </p>
        <MarketBrowser selected={selected} onAddLeg={addLeg} />
      </div>
      <ComboSlip
        legs={legs}
        onRemove={removeLeg}
        strategy={strategy}
        setStrategy={setStrategy}
        price={price}
        setPrice={setPrice}
        count={count}
        setCount={setCount}
        why={why}
        setWhy={setWhy}
        staged={staged}
        onStage={() => stage.mutate()}
        staging={stage.isPending}
        stageError={stage.isError ? stage.error.message : null}
        onPlace={() => place.mutate()}
        placing={place.isPending}
        placeError={place.isError ? place.error.message : null}
        placed={place.isSuccess ? place.data : null}
      />
    </div>
  )
}
