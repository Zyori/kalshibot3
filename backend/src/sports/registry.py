"""Sport registry — map a Kalshi ticker to a Sport.

Display lookup only. Don't use this for cross-market isolation — that's
what `is_soccer_ticker` and the sport-specific guards in `services/` are
for. Returning `None` here means "we don't have a sport label for this
ticker," not "this ticker is safe to touch."

When a new sport lands:
  1. Add the enum value in core/types.Sport.
  2. Add its ticker-prefix tuple here.
  3. Register it in `_SPORT_PREFIXES` below.
"""

from __future__ import annotations

from src.core.types import Sport
from src.sports.soccer import SOCCER_GAME_SERIES, WORLD_CUP_DERIVATIVE_SERIES

_SPORT_PREFIXES: tuple[tuple[Sport, tuple[str, ...]], ...] = (
    (Sport.SOCCER, SOCCER_GAME_SERIES + WORLD_CUP_DERIVATIVE_SERIES),
    # NFL prefixes will land here when we add the sport.
)


def sport_for_ticker(ticker: str | None) -> Sport | None:
    """Return the Sport whose prefix list matches this ticker, or None."""
    if not ticker:
        return None
    for sport, prefixes in _SPORT_PREFIXES:
        if any(ticker.startswith(p) for p in prefixes):
            return sport
    return None
