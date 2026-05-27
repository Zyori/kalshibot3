// Shared API contract types. One source of truth so pages don't drift on
// field additions (which silently shows different numbers per page — see
// the SportPortal gross vs Ledger net regression that motivated this).

export type Bet = {
  id: number
  sport: string
  ticker: string | null
  market_id: number
  kalshi_order_id: string | null
  side: 'yes' | 'no'
  entry_price_cents: number
  exit_price_cents: number | null
  quantity: number
  remaining_quantity: number
  stake_cents: number
  pnl_cents: number | null
  realized_pnl_cents: number | null
  entry_fees_cents: number
  exit_fees_cents: number
  fees_cents: number
  net_pnl_cents: number | null
  status: 'open' | 'won' | 'lost' | 'cancelled'
  exit_type: string | null
  source: string
  strategy: string
  confidence: string
  timing: string
  human_reasoning: string | null
  ai_reasoning: string | null
  placed_at: string | null
  settled_at: string | null
  created_at: string
}

export type LedgerStats = {
  total_bets: number
  by_status: Record<string, number>
  total_pnl_cents: number
  total_stake_cents: number
  total_fees_cents: number
  total_net_pnl_cents: number
  win_rate: number | null
  roi: number | null
  net_roi: number | null
  by_strategy: Array<{
    strategy: string
    count: number
    pnl_cents: number
    stake_cents: number
    fees_cents: number
    net_pnl_cents: number
    roi: number | null
    net_roi: number | null
  }>
}

export type BetFill = {
  id: number
  trade_id: string
  order_id: string
  ticker: string
  side: 'yes' | 'no'
  action: 'buy' | 'sell'
  price_cents: number
  quantity_centi: number
  quantity: number
  fee_cents: number | null
  is_taker: boolean | null
  fee_synced_at: string | null
  created_time: string | null
}

export type BetFillsResponse = { bet_id: number; fills: BetFill[] }
