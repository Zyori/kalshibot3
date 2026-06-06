"""Import open parlays (MVE combos) held on Kalshi into the ledger.

A one-shot logbook backfill for combos placed directly on kalshi.com that
aren't in our `bet` table yet. The user asked for these specific positions to
be recorded — this is the sanctioned "log what the user asks" path, NOT auto
fill-reconciliation (see feedback_no_external_fill_reconciliation).

What it does, per MVE position with a nonzero size on the account:
  1. Skip it if a bet already exists for the combo (record_external_combo is
     idempotent on order_id / synthetic key, so re-running is safe).
  2. Hydrate exactly like POST /api/combos: legs from the market's
     mve_selected_legs + yes_sub_title, entry/qty/order_id from the user's own
     fills on that ticker.
  3. record_external_combo → source=EXTERNAL, settles on its own via the sweeper.

The held side comes from the signed Kalshi position (positive = YES, negative =
NO), not from the fills hydration's side filter — the position is authoritative
on what you hold right now.

Re-runnable. Dry-run by default; pass --apply to commit.

Usage:
    uv run python -m scripts.import_open_combos            # dry run
    uv run python -m scripts.import_open_combos --apply    # commit
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone

from sqlalchemy import select

from src.core.db import get_session_factory
from src.core.types import BetSide
from src.kalshi.rest import KalshiRestClient
from src.models import Bet, Market
from src.services.bet_service import record_external_combo
from src.sports.combo import is_combo_ticker

# The route's hydration helpers are the single source of truth for how a combo
# log entry is built — reuse them rather than re-deriving the leg/entry parsing.
from src.api.routes.combos import _hydrate_entry_from_fills, _parse_legs


async def _already_logged(session, ticker: str) -> bool:
    """True if a bet already exists for this combo ticker (any status)."""
    market_id = await session.scalar(
        select(Market.id).where(Market.kalshi_ticker == ticker)
    )
    if market_id is None:
        return False
    bet_id = await session.scalar(
        select(Bet.id).where(Bet.market_id == market_id).limit(1)
    )
    return bet_id is not None


async def main(apply: bool) -> int:
    # 1) Discover MVE positions with a nonzero size on the account.
    async with KalshiRestClient() as client:
        held: list[tuple[str, int]] = []
        cursor: str | None = None
        while True:
            resp = await client.get_positions(cursor=cursor)
            for p in resp.market_positions:
                if p.position != 0 and is_combo_ticker(p.ticker):
                    held.append((p.ticker, p.position))
            cursor = resp.cursor
            if not cursor:
                break

        if not held:
            print("No open combo positions on the account.")
            return 0

        factory = get_session_factory()
        to_import: list[dict] = []
        skipped: list[str] = []

        async with factory() as session:
            for ticker, signed in held:
                if await _already_logged(session, ticker):
                    skipped.append(ticker)
                    continue

                side = BetSide.YES if signed > 0 else BetSide.NO
                raw = await client.get_market(ticker)
                market = raw.get("market", raw)
                legs = _parse_legs(market)
                entry, qty, order_id = await _hydrate_entry_from_fills(
                    client, ticker, side
                )
                if entry is None or qty is None or qty < 1:
                    # No usable fill on the held side — can't hydrate entry/qty.
                    # Surface it; the user can log it by hand via POST /api/combos
                    # with explicit entry_price_cents/quantity if needed.
                    print(
                        f"  SKIP (no fill on {side.value}): {ticker} "
                        f"signed={signed} entry={entry} qty={qty}"
                    )
                    continue

                to_import.append({
                    "ticker": ticker,
                    "side": side,
                    "entry_price_cents": entry,
                    "quantity": qty,
                    "legs": legs,
                    "order_id": order_id,
                })

            print(f"Held combo positions: {len(held)}")
            if skipped:
                print(f"Already in ledger ({len(skipped)}):")
                for t in skipped:
                    print(f"  - {t}")

            if not to_import:
                print("\nNothing new to import.")
                return 0

            print(f"\nWill import {len(to_import)} combo(s):")
            for item in to_import:
                print(
                    f"  + {item['ticker']}  {item['side'].value} "
                    f"{item['quantity']} @ {item['entry_price_cents']}¢  "
                    f"legs={len(item['legs'])}  order_id={item['order_id']}"
                )

            if not apply:
                print("\nDry run. Re-run with --apply to commit.")
                return 0

            for item in to_import:
                bet = await record_external_combo(
                    session,
                    ticker=item["ticker"],
                    side=item["side"],
                    entry_price_cents=item["entry_price_cents"],
                    quantity=item["quantity"],
                    legs=item["legs"],
                    placed_at=datetime.now(timezone.utc),
                    order_id=item["order_id"],
                )
                print(f"  recorded bet_id={bet.id}  {item['ticker']}")
            await session.commit()
            print(f"\nImported {len(to_import)} combo(s).")
            return 0


if __name__ == "__main__":
    apply_flag = "--apply" in sys.argv[1:]
    raise SystemExit(asyncio.run(main(apply_flag)))
