/**
 * Top-of-book derivations for Kalshi binary markets.
 *
 * IMPORTANT: Kalshi's "yes" and "no" arrays both hold BIDS — people
 * offering to BUY that side at the listed price. There is no native ask
 * side. The ask on one side is always derived from the opposite side's
 * bids: if someone bids 17¢ for NO, that same trade viewed from YES is
 * offering to SELL YES at 83¢ (= 100 - 17).
 *
 * Re-deriving these correctly is load-bearing: the obvious-looking
 * `min(book.yes)` gives you the cheapest YES *bid*, not the YES ask.
 * That's how we ended up showing "ask 1¢" when there was a 1¢ YES bid
 * sitting in the long tail of the book.
 */

import type { BookSide, MarketBook } from '../contexts/WebSocketProvider'

type Side = 'yes' | 'no'

function maxKey(side: BookSide): number | null {
  const keys = Object.keys(side).map(Number)
  return keys.length ? Math.max(...keys) : null
}

/** Highest price someone is willing to pay for this side. */
export function bestBid(book: MarketBook | undefined, side: Side): number | null {
  if (!book) return null
  return maxKey(side === 'yes' ? book.yes : book.no)
}

/**
 * Lowest price someone would sell this side at — derived from the
 * opposite side's highest bid. Returns null if the opposite side is empty.
 */
export function bestAsk(book: MarketBook | undefined, side: Side): number | null {
  if (!book) return null
  const oppBid = maxKey(side === 'yes' ? book.no : book.yes)
  return oppBid === null ? null : 100 - oppBid
}
