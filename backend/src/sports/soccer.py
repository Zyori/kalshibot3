"""Soccer sport definition.

Lists the Kalshi series prefixes for every soccer competition we care
about, plus World Cup derivative markets (futures, awards, group winners)
the user might want to browse separately.

Single source of truth — when adding a league, add it here and the market
discovery service will start polling it on the next cycle.

Ported from V2 (Kalshi-Mean-Reversion-Bot/backend/src/ingestion/kalshi_rest.py).
"""

from __future__ import annotations

# Per-game (match-result) series — Kalshi prefix → human league name.
# The prefix matches event_tickers Kalshi publishes as
# "{PREFIX}-{date}{home}{away}" with markets for each side.
#
# Display names are what we show in the UI (header strip, feed cards, ledger).
# Keep them tight — single-line, no parenthetical noise.
SOCCER_GAME_SERIES_NAMES: dict[str, str] = {
    # Top 5 European leagues
    "KXEPLGAME": "Premier League",
    "KXLALIGAGAME": "La Liga",
    "KXSERIEAGAME": "Serie A",
    "KXBUNDESLIGAGAME": "Bundesliga",
    "KXLIGUE1GAME": "Ligue 1",
    # Other major European leagues
    "KXLALIGA2GAME": "La Liga 2",
    "KXSERIEBGAME": "Serie B",
    "KXBUNDESLIGA2GAME": "2. Bundesliga",
    "KXEREDIVISIEGAME": "Eredivisie",
    "KXBELGIANPLGAME": "Belgian Pro League",
    "KXSCOTTISHPREMGAME": "Scottish Premiership",
    "KXSWISSLEAGUEGAME": "Swiss Super League",
    "KXSUPERLIGGAME": "Süper Lig",
    "KXCZEFLGAME": "Czech First League",
    "KXEKSTRAKLASAGAME": "Ekstraklasa",
    "KXDENSUPERLIGAGAME": "Danish Superliga",
    "KXSLGREECEGAME": "Greek Super League",
    "KXHNLGAME": "Croatia HNL",
    "KXEWSLGAME": "Women's Super League",
    # English domestic cups & second tier
    "KXEFLCHAMPIONSHIPGAME": "EFL Championship",
    "KXEFLL1GAME": "EFL League One",
    "KXEFLCUPGAME": "EFL Cup",
    "KXFACUPGAME": "FA Cup",
    "KXCOPADELREYGAME": "Copa del Rey",
    "KXCOPPAITALIAGAME": "Coppa Italia",
    "KXDFBPOKALGAME": "DFB-Pokal",
    # Americas
    "KXMLSGAME": "MLS",
    "KXNWSLGAME": "NWSL",
    "KXUSLGAME": "USL Championship",
    "KXCANPLGAME": "Canadian Premier League",
    "KXUSOPENCUPGAME": "US Open Cup",
    "KXLIGAMXGAME": "Liga MX",
    "KXARGPREMDIVGAME": "Argentina Primera División",
    "KXCOPADOBRASILGAME": "Copa do Brasil",
    "KXCHLLDPGAME": "Chile Liga de Primera",
    "KXURYPDGAME": "Uruguay Primera División",
    "KXPERLIGA1GAME": "Peru Liga 1",
    "KXDIMAYORGAME": "Colombia Liga DIMAYOR",
    "KXBOLPDIVGAME": "Bolivia Premier Division",
    "KXECULPGAME": "Ecuador Liga Pro",
    "KXVENFUTVEGAME": "Venezuela Liga FUTVE",
    "KXAPFDDHGAME": "Paraguay División de Honor",
    # Asia / Oceania / MENA
    "KXJLEAGUEGAME": "J League",
    "KXKLEAGUEGAME": "K League",
    "KXCHNSLGAME": "Chinese Super League",
    "KXALEAGUEGAME": "A-League",
    "KXSAUDIPLGAME": "Saudi Pro League",
    "KXUAEPLGAME": "UAE Pro League",
    # UEFA competitions
    "KXUCLGAME": "Champions League",
    "KXUELGAME": "Europa League",
    "KXUECLGAME": "Conference League",
    "KXUCLWGAME": "Champions League (Women)",
    "KXUEFAGAME": "UEFA",
    # FIFA / international
    "KXWCGAME": "World Cup",
    "KXCLUBWCGAME": "Club World Cup",
    "KXFIFAGAME": "FIFA Friendly",
    "KXFIFAUSPULLGAME": "FIFA US Pull Game",
    "KXINTLFRIENDLYGAME": "International Friendly",
    # Other / niche
    "KXBALLERLEAGUEGAME": "Baller League",
}

# Tuple form for code that just needs to iterate the prefixes
# (market_discovery, is_soccer_ticker). Keys order is stable in Python 3.7+,
# so this preserves the discovery polling order from before this refactor.
SOCCER_GAME_SERIES: tuple[str, ...] = tuple(SOCCER_GAME_SERIES_NAMES.keys())


def league_display_name(series: str | None) -> str | None:
    """Map a Kalshi series prefix to its display name. Unknown → None."""
    if series is None:
        return None
    return SOCCER_GAME_SERIES_NAMES.get(series)

# World Cup derivative markets — tournament-level futures, awards, props.
# Not per-match; bookings here drive different strategy than match results.
WORLD_CUP_DERIVATIVE_SERIES: tuple[str, ...] = (
    "KXMENWORLDCUP",      # Men's World Cup winner
    "KXMWORLDCUP",        # Men's World Cup winner (alias)
    "KXWCGROUPWINNER",    # World Cup group winner
    "KXWCGROUPQUAL",      # World Cup group qualifier
    "KXWCGOALLEADER",     # World Cup top goalscorer
    "KXWCAWARD",          # World Cup awards (Golden Boot, etc.)
    "KXWCROUND",          # World Cup reach round
    "KXWCSTAGEOFELIM",    # World Cup stage of elimination
    "KXWCGROUPWIN",       # World Cup group to win
    "KXWCSQUAD",          # World Cup squad markets
    "KXWC1STTIMEWIN",     # World Cup first-time winner
    "KXWCIRAN",           # Country-specific WC markets (Iran example)
    "KXWCLOCATION",       # World Cup game location markets
    "KXWCMESSIRONALDO",   # Special: Messi & Ronaldo goal contributions
    "KXPLAYWC",           # Player World Cup props
)


def is_soccer_ticker(ticker: str) -> bool:
    """True if the ticker belongs to any soccer series we track.

    Used for cross-market isolation: a position or order whose ticker doesn't
    match any of our prefixes is somebody else's (politics, crypto, weather)
    and this app must not act on it.
    """
    return any(ticker.startswith(p) for p in SOCCER_GAME_SERIES) or any(
        ticker.startswith(p) for p in WORLD_CUP_DERIVATIVE_SERIES
    )
