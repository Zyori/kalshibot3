/** Shared types for the combo builder's market browser + slip. */

export type FeedMarket = {
  ticker: string
  event_ticker: string
  event_title: string
  yes_sub_title: string | null
  league: string | null
  status: string
  open_time: string | null
  yes_bid_cents: number | null
  yes_ask_cents: number | null
  bucket: 'live' | 'upcoming' | 'recent'
  espn_state: 'pre' | 'in' | 'post' | null
  espn_period: number | null
  espn_clock: string | null
  espn_status_detail: string | null
  home_name: string | null
  away_name: string | null
  home_score: number | null
  away_score: number | null
}

export type FeedResponse = {
  live: FeedMarket[]
  upcoming: FeedMarket[]
  recent: FeedMarket[]
  refreshed_at: string | null
}

/**
 * A leg the user picked from the browser. A leg is a YES on one outcome market
 * (you back the outcome happening). market_ticker/event_ticker/side go to the
 * API; title + price_cents are captured for the slip's display and estimate.
 */
export type SlipLeg = {
  market_ticker: string
  event_ticker: string
  side: 'yes'
  title: string
  price_cents: number | null
}

/** The materialized combo market returned by POST /api/combos/materialize. */
export type Materialized = {
  ticker: string
  event_ticker: string
  subtitle: string | null
  yes_bid_cents: number | null
  yes_ask_cents: number | null
  no_bid_cents: number | null
  no_ask_cents: number | null
  leg_count: number
}
