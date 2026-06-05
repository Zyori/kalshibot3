"""The cross-market isolation firewall: may this app act on this ticker?

`is_soccer_ticker` historically did two jobs — the isolation firewall AND the
"is this soccer, for league labels / ESPN / 3-way expansion" classifier. Those
are different questions once combos exist: a combo ticker must pass the firewall
(the app places, fills, and settles it) but is NOT soccer (it must never get a
soccer label or sport='soccer').

`is_tradeable_ticker` is the firewall. Use it at every gate that asks "is this
one of OURS, vs a politics/crypto/other position we must never touch." Keep
`is_soccer_ticker` only where the question is genuinely soccer-domain.
"""

from __future__ import annotations

from src.sports.combo import is_combo_ticker
from src.sports.soccer import is_soccer_ticker


def is_tradeable_ticker(ticker: str) -> bool:
    """True if this ticker belongs to a market the app is allowed to act on:
    a soccer market we track, or a combo (multivariate) market. Everything
    else (politics, crypto, weather, other sports we don't handle) is somebody
    else's position and the cross-market isolation rule forbids touching it."""
    return is_soccer_ticker(ticker) or is_combo_ticker(ticker)
