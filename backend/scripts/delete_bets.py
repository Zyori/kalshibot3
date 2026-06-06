"""Delete specific bets by id (with their cascade children).

A targeted cleanup tool for removing individual ledger rows the user names
explicitly — e.g. tiny test bets that inflate the count. NOT a bulk/heuristic
delete (that's scrub_cancelled). The ids are passed on the command line so the
exact rows are visible and auditable.

Cascade behavior (defined on the models, enforced by foreign_keys=ON which the
app's connection sets):
  - combo_leg     ondelete=CASCADE   → deleted with the bet
  - trade_snapshot ondelete=CASCADE  → deleted with the bet
  - bet_fill      ondelete=SET NULL  → kept as an external-fill audit row,
                                       its bet_id nulled (never silently lost)

Refuses to delete a bet that another bet hedges against (parent_bet_id), since
that would orphan the hedge child.

Runs through the app session so the PRAGMAs + cascades apply. Dry-run by
default; pass --apply to commit.

Usage:
    uv run python -m scripts.delete_bets 24 25 26 27           # dry run
    uv run python -m scripts.delete_bets 24 25 26 27 --apply   # delete
"""

from __future__ import annotations

import sys

from sqlalchemy import delete, select

from src.core.db import get_session_factory
from src.models import Bet, BetFill, ComboLeg, Market, TradeSnapshot


async def main(ids: list[int], apply: bool) -> int:
    factory = get_session_factory()
    async with factory() as session:
        rows = (await session.execute(
            select(Bet, Market.kalshi_ticker)
            .join(Market, Market.id == Bet.market_id, isouter=True)
            .where(Bet.id.in_(ids))
        )).all()
        found = {b.id for b, _ in rows}
        missing = set(ids) - found
        if missing:
            print(f"WARNING: no such bet id(s): {sorted(missing)}")

        # Refuse any that something hedges against.
        hedged = (await session.execute(
            select(Bet.parent_bet_id).where(Bet.parent_bet_id.in_(ids))
        )).scalars().all()
        blocked = set(hedged)
        if blocked:
            print(f"REFUSING (hedged by another bet): {sorted(blocked)}")

        deletable = [(b, t) for b, t in rows if b.id not in blocked]
        if not deletable:
            print("Nothing to delete.")
            return 0

        print(f"Will delete {len(deletable)} bet(s):")
        for b, t in deletable:
            legs = await session.scalar(
                select(ComboLeg.bet_id).where(ComboLeg.bet_id == b.id).limit(1)
            )
            fills = (await session.execute(
                select(BetFill.id).where(BetFill.bet_id == b.id)
            )).scalars().all()
            snaps = (await session.execute(
                select(TradeSnapshot.id).where(TradeSnapshot.bet_id == b.id)
            )).scalars().all()
            print(
                f"  id={b.id}  {b.status}  {b.side} {b.quantity} @ {b.entry_price_cents}c"
                f"  {t}"
            )
            print(
                f"      → cascade: legs={'yes' if legs else 'no'}  "
                f"snapshots={len(snaps)} (deleted)  "
                f"fills={len(fills)} (bet_id set NULL, kept as audit)"
            )

        if not apply:
            print("\nDry run. Re-run with --apply to delete.")
            return 0

        ids_to_delete = [b.id for b, _ in deletable]
        await session.execute(delete(Bet).where(Bet.id.in_(ids_to_delete)))
        await session.commit()
        print(f"\nDeleted {len(ids_to_delete)} bet(s): {ids_to_delete}")
        return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    apply_flag = "--apply" in args
    id_args = [int(a) for a in args if a != "--apply"]
    if not id_args:
        print("Usage: python -m scripts.delete_bets <id> [<id> ...] [--apply]")
        raise SystemExit(2)
    import asyncio
    raise SystemExit(asyncio.run(main(id_args, apply_flag)))
