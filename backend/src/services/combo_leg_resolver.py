"""Resolve which legs of a settled combo hit and which missed.

A combo settles as ONE binary market — Kalshi tells us the combo won or lost,
not which legs did. But every leg ticker is itself a real market with its own
`result` once its game finishes. So after a combo bet settles we look up each
leg's market result and record it on combo_leg.result, which the ledger renders
as a per-leg ✓/✗.

Shortcut: a WON combo means every leg hit (logical certainty) — we mark all legs
with their selected side without any per-leg network calls. A LOST combo needs
the per-leg lookups to find the miss(es).

Kept separate from bet_service.settle_bets_for_market on purpose: settlement is
a pure-DB transition with no network; this is the network-touching enrichment,
run by the settlement sweeper which already holds a KalshiRestClient.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logging import get_logger
from src.core.types import BetStatus
from src.kalshi.rest import KalshiRestClient
from src.models import Bet, ComboLeg

log = get_logger(__name__)


async def resolve_combo_legs(
    session: AsyncSession, client: KalshiRestClient, *, bet: Bet
) -> int:
    """Populate combo_leg.result for a settled combo bet. Returns the number of
    legs newly resolved. Idempotent — legs already resolved are skipped, so a
    re-run after a transient lookup failure fills only the gaps.

    The bet must be terminal (WON/LOST); OPEN bets are skipped (nothing to
    resolve yet)."""
    if bet.status not in (BetStatus.WON, BetStatus.LOST):
        return 0

    legs = (await session.execute(
        select(ComboLeg).where(ComboLeg.bet_id == bet.id)
    )).scalars().all()
    unresolved = [leg for leg in legs if leg.result is None]
    if not unresolved:
        return 0

    # WON combo: every leg resolved the way it was picked. No network needed.
    # Skip a leg with no recorded side — setting result=side=None would leave it
    # pending and re-trigger this branch every sweep. (Side is always set for
    # builder-placed and normally-parsed external combos; this is defensive.)
    if bet.status == BetStatus.WON:
        marked = 0
        for leg in unresolved:
            if leg.side is None:
                continue
            leg.result = leg.side
            marked += 1
        if marked:
            await session.flush()
            log.info("combo_legs_resolved_won", bet_id=bet.id, legs=marked)
        return marked

    # LOST combo: look up each leg's own market result to find the miss(es).
    resolved = 0
    for leg in unresolved:
        if not leg.leg_ticker:
            continue
        try:
            raw = await client.get_market(leg.leg_ticker)
        except Exception as e:  # noqa: BLE001 — a leg we can't fetch stays pending
            log.warning(
                "combo_leg_lookup_failed",
                bet_id=bet.id, leg_ticker=leg.leg_ticker, error=str(e)[:120],
            )
            continue
        market = raw.get("market", raw)
        result = market.get("result")
        # Only record a clean binary result; 'scalar'/'' leave the leg pending.
        if result in ("yes", "no"):
            leg.result = result
            resolved += 1
    if resolved:
        await session.flush()
        log.info("combo_legs_resolved_lost", bet_id=bet.id, legs=resolved)
    return resolved
