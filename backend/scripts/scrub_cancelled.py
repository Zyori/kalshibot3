"""Scrub clear-bid-then-cancel noise from the ledger.

Deletes a `bet` row iff ALL of the following hold:
  1. status = 'cancelled'
  2. zero rows in `bet_fill` reference it (no real fills)
  3. no other `bet` row references it as parent_bet_id (no hedge points
     at it)

If any condition fails the row stays — it's no longer pure noise.
`suggestion.bet_id` does not exist; the bet -> suggestion FK uses
ON DELETE SET NULL on the bet side, so suggestions are not at risk.

Re-runnable. Always runs the dry-run pass first and prints what it
will delete. Pass `--apply` to actually delete; without it the script
is read-only.

Usage:
    uv run python -m scripts.scrub_cancelled            # dry run
    uv run python -m scripts.scrub_cancelled --apply    # delete
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import delete, func, select

from src.core.db import get_session
from src.models import Bet, BetFill


async def find_safe_to_delete(session) -> list[int]:
    """Return ids of cancelled bets with no fills and no hedge children."""
    # Cancelled with zero fills.
    fill_count = (
        select(BetFill.bet_id, func.count(BetFill.id).label("n"))
        .group_by(BetFill.bet_id)
        .subquery()
    )
    no_fill_rows = (
        await session.execute(
            select(Bet.id)
            .outerjoin(fill_count, fill_count.c.bet_id == Bet.id)
            .where(Bet.status == "cancelled")
            .where(func.coalesce(fill_count.c.n, 0) == 0)
        )
    ).scalars().all()

    if not no_fill_rows:
        return []

    # Strip out any that something else hedged against.
    hedge_targets = (
        await session.execute(
            select(Bet.parent_bet_id)
            .where(Bet.parent_bet_id.in_(no_fill_rows))
        )
    ).scalars().all()
    referenced = set(hedge_targets)
    return [bid for bid in no_fill_rows if bid not in referenced]


async def main(apply: bool) -> int:
    async for session in get_session():
        ids = await find_safe_to_delete(session)
        if not ids:
            print("Nothing to scrub.")
            return 0

        # Show what we'd delete so the user can sanity-check before --apply.
        rows = (
            await session.execute(
                select(
                    Bet.id,
                    Bet.status,
                    Bet.side,
                    Bet.entry_price_cents,
                    Bet.quantity,
                    Bet.placed_at,
                ).where(Bet.id.in_(ids))
            )
        ).all()
        print(f"Found {len(rows)} cancelled bet(s) safe to delete:")
        for r in rows:
            print(
                f"  id={r[0]}  {r[1]}  {r[2]} {r[4]} @ {r[3]}¢  placed={r[5]}"
            )

        if not apply:
            print("\nDry run. Re-run with --apply to delete.")
            return 0

        await session.execute(delete(Bet).where(Bet.id.in_(ids)))
        await session.commit()
        print(f"\nDeleted {len(rows)} row(s).")
        return 0
    return 1


if __name__ == "__main__":
    apply_flag = "--apply" in sys.argv[1:]
    raise SystemExit(asyncio.run(main(apply_flag)))
