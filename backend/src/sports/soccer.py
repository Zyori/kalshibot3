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
    "KXDENSUPERLIGAGAME": "Danish Superliga",
    "KXSLGREECEGAME": "Greek Super League",
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
    "KXUSOPENCUPGAME": "US Open Cup",
    "KXLIGAMXGAME": "Liga MX",
    "KXARGPREMDIVGAME": "Argentina Primera División",
    "KXCOPADOBRASILGAME": "Copa do Brasil",
    "KXCONMEBOLLIBGAME": "Copa Libertadores",
    "KXCONMEBOLSUDGAME": "Copa Sudamericana",
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
    "KXCHNSLGAME": "Chinese Super League",
    "KXALEAGUEGAME": "A-League",
    "KXSAUDIPLGAME": "Saudi Pro League",
    # UEFA competitions
    "KXUCLGAME": "Champions League",
    "KXUELGAME": "Europa League",
    "KXUECLGAME": "Conference League",
    # FIFA / international
    "KXWCGAME": "World Cup",
    "KXCLUBWCGAME": "Club World Cup",
    "KXFIFAGAME": "FIFA Friendly",
    "KXFIFAUSPULLGAME": "FIFA US Pull Game",
    "KXINTLFRIENDLYGAME": "International Friendly",
}

# Tuple form for code that just needs to iterate the prefixes
# (market_discovery, is_soccer_ticker). Keys order is stable in Python 3.7+,
# so this preserves the discovery polling order from before this refactor.
SOCCER_GAME_SERIES: tuple[str, ...] = tuple(SOCCER_GAME_SERIES_NAMES.keys())


# Kalshi prefix → ESPN scoreboard path (after `soccer/`). None means
# ESPN doesn't publish a scoreboard for this league under any slug we
# could verify — callers fall back to Kalshi's occurrence_datetime.
#
# Slugs verified 2026-05-26 by hitting
# https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard
# and checking for 200 OK + non-empty event list. A 400 means ESPN does
# not recognize the slug; not a coverage gap we can paper over.
SOCCER_ESPN_SLUGS: dict[str, str | None] = {
    # Top 5 European leagues
    "KXEPLGAME": "eng.1",
    "KXLALIGAGAME": "esp.1",
    "KXSERIEAGAME": "ita.1",
    "KXBUNDESLIGAGAME": "ger.1",
    "KXLIGUE1GAME": "fra.1",
    # Other European leagues
    "KXLALIGA2GAME": "esp.2",
    "KXSERIEBGAME": "ita.2",
    "KXBUNDESLIGA2GAME": "ger.2",
    "KXEREDIVISIEGAME": "ned.1",
    "KXBELGIANPLGAME": "bel.1",
    "KXSCOTTISHPREMGAME": "sco.1",
    "KXSWISSLEAGUEGAME": "sui.1",
    "KXSUPERLIGGAME": "tur.1",
    "KXDENSUPERLIGAGAME": "den.1",
    "KXSLGREECEGAME": "gre.1",
    "KXEWSLGAME": "eng.w.1",
    # English domestic cups & second tier
    "KXEFLCHAMPIONSHIPGAME": "eng.2",
    "KXEFLL1GAME": "eng.3",
    "KXEFLCUPGAME": "eng.league_cup",
    "KXFACUPGAME": "eng.fa",
    "KXCOPADELREYGAME": "esp.copa_del_rey",
    "KXCOPPAITALIAGAME": "ita.coppa_italia",
    "KXDFBPOKALGAME": "ger.dfb_pokal",
    # Americas
    "KXMLSGAME": "usa.1",
    "KXNWSLGAME": "usa.nwsl",
    "KXUSLGAME": "usa.usl.1",
    "KXUSOPENCUPGAME": "usa.open",
    "KXLIGAMXGAME": "mex.1",
    "KXARGPREMDIVGAME": "arg.1",
    "KXCOPADOBRASILGAME": "bra.copa_do_brazil",
    "KXCONMEBOLLIBGAME": "conmebol.libertadores",
    "KXCONMEBOLSUDGAME": "conmebol.sudamericana",
    "KXCHLLDPGAME": "chi.1",
    "KXURYPDGAME": "uru.1",
    "KXPERLIGA1GAME": "per.1",
    "KXDIMAYORGAME": "col.1",
    "KXBOLPDIVGAME": "bol.1",
    "KXECULPGAME": "ecu.1",
    "KXVENFUTVEGAME": "ven.1",
    "KXAPFDDHGAME": "par.1",
    # Asia / Oceania / MENA
    "KXJLEAGUEGAME": "jpn.1",
    "KXCHNSLGAME": "chn.1",
    "KXALEAGUEGAME": "aus.1",
    "KXSAUDIPLGAME": "ksa.1",
    # UEFA competitions
    "KXUCLGAME": "uefa.champions",
    "KXUELGAME": "uefa.europa",
    "KXUECLGAME": "uefa.europa.conf",
    # FIFA / international
    "KXWCGAME": "fifa.world",
    "KXCLUBWCGAME": "fifa.cwc",
    "KXFIFAGAME": "fifa.friendly",
    "KXFIFAUSPULLGAME": None,
    "KXINTLFRIENDLYGAME": "fifa.friendly",
}


# Kalshi series prefix → path under kalshi.com/category/sports/soccer/ for
# the league's category page. The path is NOT derivable from the prefix or
# display name — Kalshi's slugs are inconsistent (some have a /game or /games
# suffix, some don't, World Cup nests two segments), so each is hand-verified
# from the live site. Populate as confirmed; an absent entry renders the
# league name as plain text (no link) rather than risk a broken URL.
KALSHI_CATEGORY_PATH: dict[str, str] = {
    "KXNWSLGAME": "nwsl/game",
    "KXUSLGAME": "usl-championship",
    "KXLALIGA2GAME": "la-liga-2",
    "KXINTLFRIENDLYGAME": "intl-friendlies",
    "KXWCGAME": "fifa-world-cup/world-cup/games",
}

_KALSHI_CATEGORY_BASE = "https://kalshi.com/category/sports/soccer"


def league_display_name(series: str | None) -> str | None:
    """Map a Kalshi series prefix to its display name. Unknown → None."""
    if series is None:
        return None
    return SOCCER_GAME_SERIES_NAMES.get(series)


def kalshi_category_url(series: str | None) -> str | None:
    """Full URL to the league's Kalshi category page, or None when we don't
    have a hand-verified path for this series (caller renders plain text)."""
    if series is None:
        return None
    path = KALSHI_CATEGORY_PATH.get(series)
    return f"{_KALSHI_CATEGORY_BASE}/{path}" if path else None


def espn_slug_for(series: str | None) -> str | None:
    """Map a Kalshi series prefix to its ESPN scoreboard slug. None means
    we don't have an ESPN source for this league (caller falls back to
    Kalshi's occurrence_datetime)."""
    if series is None:
        return None
    return SOCCER_ESPN_SLUGS.get(series)

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
