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

// === Event API (one page per game) ===

export type TeamLiveStats = {
  score: number | null
  shots: number | null
  shots_on_target: number | null
  possession_pct: number | null
  corners: number | null
  fouls: number | null
  yellow_cards: number
  red_cards: number
}

export type MatchEvent = {
  kind: 'goal' | 'yellow' | 'red' | 'other'
  minute: string | null
  player: string | null
  side: 'home' | 'away' | null
  text: string
}

export type LiveSnapshot = {
  home_name: string | null
  away_name: string | null
  home: TeamLiveStats
  away: TeamLiveStats
  last_event: MatchEvent | null
}

export type ChildPosition = {
  side: 'yes' | 'no'
  quantity: number
  avg_entry_price_cents: number | null
  current_price_cents: number | null
  unrealized_pnl_cents: number | null
}

export type ChildMarket = {
  ticker: string
  yes_sub_title: string | null
  market_title: string | null
  status: string
  yes_bid_cents: number | null
  yes_ask_cents: number | null
  no_bid_cents: number | null
  no_ask_cents: number | null
  position: ChildPosition | null
}

export type EventDetail = {
  event_ticker: string
  event_title: string | null
  series: string
  league: string | null
  open_time: string | null
  close_time: string | null
  bucket: 'live' | 'upcoming' | 'recent'
  espn_state: 'pre' | 'in' | 'post' | null
  espn_period: number | null
  espn_clock: string | null
  espn_status_detail: string | null
  live: LiveSnapshot | null
  markets: ChildMarket[]
}
