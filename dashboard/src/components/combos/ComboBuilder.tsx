import { useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { errorMessage, type ComboStrategy } from './ComboFields'
import ComboSlip from './ComboSlip'
import MarketBrowser from './MarketBrowser'
import type { Quote, SlipLeg } from './types'

// Accept no longer returns a bet — the order fills async once the maker
// confirms, and the bet appears in the ledger then.
type AcceptResult = {
  accepted: boolean
  side: 'yes' | 'no'
  count: number
  note: string
}

// The leg shape the API expects — SlipLeg minus the display-only fields.
function toApiLeg(l: SlipLeg) {
  return { market_ticker: l.market_ticker, event_ticker: l.event_ticker, side: l.side }
}

/**
 * Build a combo by browsing markets and clicking outcomes into a sticky slip,
 * then place it via Kalshi's RFQ flow: Request a quote → market makers respond
 * with live quotes → accept the best one (the human confirm). Combos fill
 * through RFQ, not a resting order book.
 */
export default function ComboBuilder() {
  const qc = useQueryClient()
  const [legs, setLegs] = useState<SlipLeg[]>([])
  const [strategy, setStrategy] = useState<ComboStrategy>('lock_parlay')
  const [count, setCount] = useState('')
  const [why, setWhy] = useState('')
  // The open RFQ: its id + the combo ticker, set when the user requests a quote.
  const [rfq, setRfq] = useState<{ id: string; ticker: string } | null>(null)

  const apiLegs = legs.map(toApiLeg)

  // Abandon the open RFQ on Kalshi (best effort) so it doesn't count toward the
  // open-RFQ cap. Fire-and-forget — the RFQ also expires on its own.
  function abandonRfq() {
    if (!rfq) return
    void fetch(`/api/combos/rfq/${rfq.id}`, { method: 'DELETE' }).catch(() => {})
    setRfq(null)
  }

  function toggleLeg(leg: SlipLeg) {
    setLegs((prev) =>
      prev.some((l) => l.market_ticker === leg.market_ticker)
        ? prev.filter((l) => l.market_ticker !== leg.market_ticker)
        : [...prev, leg],
    )
    abandonRfq() // changing legs invalidates any open RFQ
  }
  function removeLeg(marketTicker: string) {
    setLegs((prev) => prev.filter((l) => l.market_ticker !== marketTicker))
    abandonRfq()
  }

  // 1) Request a quote: materialize + create the RFQ.
  const requestQuote = useMutation<{ rfq_id: string; ticker: string }, Error>({
    mutationFn: async () => {
      const res = await fetch('/api/combos/rfq', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ legs: apiLegs, contracts: Number(count) }),
      })
      if (!res.ok) {
        const d = await res.json().catch(() => null)
        throw new Error(errorMessage(d?.detail, `Quote request failed (${res.status})`))
      }
      return res.json()
    },
    onSuccess: (r) => {
      accept.reset() // clear any prior "accepted" banner when starting fresh
      setRfq({ id: r.rfq_id, ticker: r.ticker })
    },
  })

  // 2) Poll quotes for the open RFQ (makers respond within seconds).
  const quotes = useQuery<{ quotes: Quote[] }>({
    queryKey: ['combo_quotes', rfq?.id],
    queryFn: async () => {
      const res = await fetch(`/api/combos/rfq/${rfq!.id}/quotes`)
      if (!res.ok) throw new Error(`quotes: ${res.status}`)
      return res.json()
    },
    enabled: !!rfq,
    refetchInterval: 1500, // makers quote in real time
  })

  // Synchronous double-accept guard: `accepting` (isPending) only flips on the
  // next render, so two clicks in one tick could both fire mutate(). This ref
  // blocks the second synchronously.
  const acceptInFlight = useRef(false)

  // 3) Accept a quote — the human-confirmed action. `ticker` is threaded
  // through the mutation variables (not read from the `rfq` closure, which a
  // prior onSuccess may have nulled).
  const accept = useMutation<
    AcceptResult, Error, { quote: Quote; side: 'yes' | 'no'; ticker: string }
  >({
    mutationFn: async ({ quote, side, ticker }) => {
      const price = side === 'yes' ? quote.yes_bid_cents : quote.no_bid_cents
      const res = await fetch('/api/combos/accept', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          quote_id: quote.quote_id,
          side,
          price_cents: price,
          count: Number(count),
          legs: apiLegs,
          ticker,
          strategy,
          human_reasoning: why.trim() || null,
          acknowledged: true, // accepting IS the human confirm
        }),
      })
      if (!res.ok) {
        const d = await res.json().catch(() => null)
        throw new Error(errorMessage(d?.detail, `Accept failed (${res.status})`))
      }
      return res.json()
    },
    onSuccess: () => {
      // The order fills async; the ledger/positions update when the fill lands.
      qc.invalidateQueries({ queryKey: ['ledger'] })
      qc.invalidateQueries({ queryKey: ['positions'] })
      setRfq(null)
      setLegs([])
      setCount('')
      setWhy('')
    },
    onSettled: () => {
      acceptInFlight.current = false
    },
  })

  function onAccept(quote: Quote, side: 'yes' | 'no') {
    if (acceptInFlight.current || !rfq) return
    acceptInFlight.current = true
    accept.mutate({ quote, side, ticker: rfq.ticker })
  }

  const selected = useMemo(() => new Set(legs.map((l) => l.market_ticker)), [legs])

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_360px]">
      <div className="min-w-0">
        <p className="mb-3 text-sm text-text-muted">
          Click outcomes to build your parlay — the slip shows the running
          estimate. Click a picked outcome again to remove it. Request a quote,
          then accept the best one market makers offer.
        </p>
        <MarketBrowser selected={selected} onToggleLeg={toggleLeg} />
      </div>
      <ComboSlip
        legs={legs}
        onRemove={removeLeg}
        strategy={strategy}
        setStrategy={setStrategy}
        count={count}
        setCount={setCount}
        why={why}
        setWhy={setWhy}
        rfqOpen={!!rfq}
        onRequestQuote={() => requestQuote.mutate()}
        requesting={requestQuote.isPending}
        requestError={requestQuote.isError ? requestQuote.error.message : null}
        quotes={rfq ? quotes.data?.quotes ?? [] : []}
        quotesLoading={!!rfq && quotes.isPending}
        onAccept={onAccept}
        accepting={accept.isPending}
        acceptError={accept.isError ? accept.error.message : null}
        accepted={accept.isSuccess ? accept.data : null}
      />
    </div>
  )
}
