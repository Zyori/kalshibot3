"""Kalshi multivariate-event (combo / parlay) recognition.

A combo is ONE Kalshi market with a normal ticker that bundles several
single-market "legs" into one atomic binary YES/NO contract — it pays out
only if every leg resolves the selected way. Kalshi calls these multivariate
events (MVE); the user calls them parlays. Examples observed on the account:

    KXMVESPORTSMULTIGAMEEXTENDED-S202662EADA40D40-43319250880   (sports combo)
    KXMVECROSSCATEGORY-S20269999ECC4A83-86BE7C7F888             (cross-category)

The legs live in the market's `mve_selected_legs` field; the combo settles as
one binary market, so once recognized it flows through the normal order /
fill / settlement pipeline unchanged.

This module exists separately from soccer.py on purpose: a combo must pass the
cross-market isolation firewall (the app may act on it) but is NOT soccer — it
must never receive a soccer league label or sport='soccer'. See
sports/tradeable.py for the combined firewall check.
"""

from __future__ import annotations

# Kalshi multivariate series prefixes. All begin with the `KXMVE` family stem;
# we match the stem so new MVE series are recognized without a code change,
# but keep the known concrete prefixes documented for readers.
#   KXMVESPORTSMULTIGAMEEXTENDED — multi-game sports parlays (the common case)
#   KXMVECROSSCATEGORY           — cross-category combos
_COMBO_FAMILY_STEM = "KXMVE"


_CROSS_CATEGORY_PREFIX = "KXMVECROSSCATEGORY"


def is_combo_ticker(ticker: str) -> bool:
    """True if the ticker is a Kalshi multivariate-event (combo/parlay) market.

    Matches the whole `KXMVE…` family by stem so a newly-listed MVE series is
    recognized automatically — the alternative (an explicit allow-list) silently
    drops combos from a series we haven't enumerated yet, which on the isolation
    firewall means the app stops acting on a real position of the user's.
    """
    return ticker.startswith(_COMBO_FAMILY_STEM)


def is_cross_category_ticker(ticker: str) -> bool:
    """True for a KXMVECROSSCATEGORY combo — one that can bundle legs across
    categories (a sports leg + a politics/weather leg). The PLACE path refuses
    these: we can't guarantee per-leg isolation when the client supplies the
    legs and a non-sports leg may be hidden in the materialized market. (We
    still RECOGNIZE them as combos for the ledger/firewall — recognition is not
    permission to place.)"""
    return ticker.startswith(_CROSS_CATEGORY_PREFIX)


# Sports series that may appear as combo LEGS. A leg is a single-game/prop
# market in one of these series. This is the per-leg isolation guard for combo
# PLACEMENT: every leg the builder sends must be one of these before we
# materialize + place, so a crafted out-of-scope leg (politics/weather/crypto)
# is refused app-side rather than trusted to Kalshi. Derived from the series
# actually present in Kalshi's sports combo collections (NBA/MLB/NHL/NFL/UFC
# plus soccer). Extend when a new sport's legs appear.
_SPORTS_LEG_PREFIXES: tuple[str, ...] = (
    "KXNBA", "KXNFL", "KXNHL", "KXMLB", "KXUFC", "KXWNBA", "KXNCAA",
    # Soccer per-game / total series share the KX…GAME / KX…TOTAL shapes but
    # aren't all KX-prefixed uniformly; soccer legs are validated via
    # is_soccer_ticker at the call site, so they don't need listing here.
)


def is_sports_leg_ticker(ticker: str) -> bool:
    """True if `ticker` is a non-soccer sports market that may be a combo leg.

    Soccer legs are checked separately via is_soccer_ticker; this covers the
    other sports (NBA/NFL/NHL/MLB/UFC/…). Used by the combo placement path to
    refuse legs that aren't sports markets at all."""
    return ticker.startswith(_SPORTS_LEG_PREFIXES)
