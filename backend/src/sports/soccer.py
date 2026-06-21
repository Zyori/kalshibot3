"""Soccer sport definition.

Lists the Kalshi series prefixes for every soccer competition we care
about, plus World Cup derivative markets (futures, awards, group winners)
the user might want to browse separately.

Single source of truth — when adding a league, add it here and the market
discovery service will start polling it on the next cycle.

Ported from V2 (Kalshi-Mean-Reversion-Bot/backend/src/ingestion/kalshi_rest.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

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


# Per-game total-goals (Over/Under) series, keyed by the game series they pair
# with. Kalshi lists these as a SEPARATE series/event from the moneyline — same
# {date}{matchup} suffix, different prefix, with one market per threshold
# (Over 1.5/2.5/3.5/4.5, ticker suffix -1/-2/-3/-4). The names are NOT a clean
# "{GAME}→{TOTAL}" swap (KXWCTOTALGOAL is tournament-aggregate, not per-game;
# KXBRASILEIROTOTAL ≠ KXCOPADOBRASILGAME), so they are hand-verified like the
# ESPN slugs — add an entry only after confirming live markets exist for it.
#
# Verified 2026-06-01 with live markets (KXINTLFRIENDLYTOTAL-26JUN01COLCRI-N).
# World Cup per-game totals: KXWCGAME → KXWCTOTAL, same {DATE}{MATCHUP} suffix
# (KXWCGAME-26JUN11MEXRSA ↔ KXWCTOTAL-26JUN11MEXRSA). Verified 2026-06-11 against
# live markets on the WC opener (KXWCTOTAL-26JUN11MEXRSA-{1..6}, "Over N.5 goals
# scored"). NOT KXWCGAMEGOALS — that's the tournament "highest scoring match"
# aggregate, not a per-game O/U.
SOCCER_TOTAL_SERIES: dict[str, str] = {
    "KXINTLFRIENDLYGAME": "KXINTLFRIENDLYTOTAL",
    "KXWCGAME": "KXWCTOTAL",
}

# Tuple of total-series prefixes, for cross-market isolation. These are soccer
# markets and must pass is_soccer_ticker so orders/positions on them aren't
# refused as "not soccer".
SOCCER_TOTAL_SERIES_PREFIXES: tuple[str, ...] = tuple(SOCCER_TOTAL_SERIES.values())


# Per-game goal-spread (handicap) series. Kalshi lists a spread as its own
# series/event — same {date}{matchup} suffix as the moneyline, but each market
# is "{Favorite} wins by more than {line} goals?" with a ticker suffix of the
# favorite's 3-letter code + a slot digit (e.g. KXWCSPREAD-26JUN19USAAUS-USA2 =
# "USA wins by more than 1.5 goals"). Like the totals, the slot digit is only an
# index — the line lives in the market title, not the suffix — and the series
# name is NOT a clean swap off the game prefix, so each is hand-verified against
# live markets before being added here.
#
# Verified 2026-06-21 against live World Cup markets (KXWCSPREAD-26JUN19USAAUS-*,
# KXWCSPREAD-26JUN13HTISCO-*): one market per (favorite, line) slot.
SOCCER_SPREAD_SERIES_PREFIXES: tuple[str, ...] = ("KXWCSPREAD",)


def total_series_for_game(game_series: str) -> str | None:
    """The total-goals series paired with a game series, or None if we don't
    have (a confirmed) one. Discovery uses this to fetch the O/U event for a
    game it's already tracking."""
    return SOCCER_TOTAL_SERIES.get(game_series)


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
    return (
        any(ticker.startswith(p) for p in SOCCER_GAME_SERIES)
        or any(ticker.startswith(p) for p in SOCCER_TOTAL_SERIES_PREFIXES)
        or any(ticker.startswith(p) for p in SOCCER_SPREAD_SERIES_PREFIXES)
        or any(ticker.startswith(p) for p in WORLD_CUP_DERIVATIVE_SERIES)
    )


@dataclass(frozen=True)
class ParsedMarketTicker:
    """The structured pieces of a per-game market ticker, for the readable
    ledger label and future analysis. Codes are the 3-letter Kalshi team codes
    (home listed first), `selection` is the outcome the market is on
    (a team code, or TIE for the draw)."""
    series: str           # e.g. "KXINTLFRIENDLYGAME"
    home_code: str        # e.g. "AUT"
    away_code: str        # e.g. "TUN"
    selection_code: str   # e.g. "AUT" | "TUN" | "TIE"


# {SERIES}-{YYMONDD}{HOME}{AWAY}-{SELECTION}, where HOME/AWAY/SELECTION are
# 3-letter codes (TIE for the draw). Verified across all 23 distinct tickers in
# the DB: every matchup block is exactly two 3-char codes. The date is
# 2-digit-year + 3-letter-month + 2-digit-day.
_GAME_TICKER_RE = re.compile(
    r"^(?P<series>[A-Z0-9]+)-\d{2}[A-Z]{3}\d{2}(?P<home>[A-Z]{3})(?P<away>[A-Z]{3})-(?P<sel>[A-Z]{3})$"
)


def is_total_goals_ticker(ticker: str) -> bool:
    """Whether a ticker is a per-game total-goals (Over/Under) market — a
    presence check only, NOT the line. Use total_goals_line() for the number.

    The ticker suffix is an integer that does NOT have a fixed relationship to
    the Over/Under line: a game's market set can start at any line (one game
    lists Over 1.5/2.5/3.5, another 2.5/3.5/4.5), so suffix -2 means "Over 1.5"
    on one game and "Over 2.5" on another. The suffix is just a slot index, not
    the threshold — see total_goals_line()."""
    if not any(ticker.startswith(p) for p in SOCCER_TOTAL_SERIES_PREFIXES):
        return False
    return ticker.rsplit("-", 1)[-1].isdigit()


def is_spread_ticker(ticker: str) -> bool:
    """Whether a ticker is a per-game goal-spread (handicap) market — '{Favorite}
    wins by more than {line} goals'. Presence check only; the line lives in the
    title (see spread_line). The suffix is the favorite's 3-letter code + a slot
    digit (e.g. -USA2), so the matchup-block regex below — not the moneyline
    parser — identifies it."""
    if not any(ticker.startswith(p) for p in SOCCER_SPREAD_SERIES_PREFIXES):
        return False
    return _SPREAD_TICKER_RE.search(ticker) is not None


def is_per_game_soccer_ticker(ticker: str) -> bool:
    """Whether a ticker is a per-game soccer market we can record as a single
    bet: a match-result moneyline (parse_market_ticker matches), a per-game
    total-goals Over/Under, OR a goal-spread (handicap). Excludes combos (their
    own log flow) and futures (deci-cent priced — the whole-cent money core can't
    store them; the Futures board is read-only). The import path gates on this so
    a per-game totals/spread bet isn't dropped just because its ticker shape
    differs from a moneyline's."""
    return (
        parse_market_ticker(ticker) is not None
        or is_total_goals_ticker(ticker)
        or is_spread_ticker(ticker)
    )


_OVER_LINE_RE = re.compile(r"\bover\s+(\d+(?:\.\d+)?)\b", re.IGNORECASE)


def total_goals_line(yes_sub_title: str | None) -> float | None:
    """The Over/Under line from Kalshi's market sub-title ('Over 1.5 goals
    scored' → 1.5). Kalshi's label is the single source of truth for the line —
    the ticker suffix is only a slot index and varies per game (see
    is_total_goals_ticker). Returns None if the label is missing or unparseable;
    the caller orders/labels off the raw text in that rare case."""
    if not yes_sub_title:
        return None
    m = _OVER_LINE_RE.search(yes_sub_title)
    if m is None:
        return None
    return float(m.group(1))


def total_goals_label(yes_sub_title: str | None, event_title: str, *, negate: bool) -> str:
    """Side-aware totals label off Kalshi's sub-title: a YES hold is Over, a NO
    hold is Under. '{Home - Away} — Over 1.5 goals', falling back to a line-less
    'Over goals' when the sub-title doesn't carry the number. Shared by the
    ledger (importable picker) and the positions list so both read identically."""
    line = total_goals_line(yes_sub_title)
    ou = "Under" if negate else "Over"
    matchup = event_title.replace(" vs ", " - ")
    return f"{matchup} — {ou} {line:g} goals" if line is not None else f"{matchup} — {ou} goals"


# Spread ticker suffix: the favorite's 3-letter code + a slot digit, e.g.
# -USA2. The matchup block ({date}{HOME}{AWAY}) is identical to a game ticker;
# only the suffix shape (code+digit, not a bare 3-letter selection) differs,
# which is what tells a spread apart from a moneyline.
_SPREAD_TICKER_RE = re.compile(
    r"-\d{2}[A-Z]{3}\d{2}(?P<home>[A-Z]{3})(?P<away>[A-Z]{3})-(?P<fav>[A-Z]{3})\d+$"
)

# "USA wins by more than 1.5 goals" → 1.5. The favorite and line both come from
# the title — the ticker suffix digit is only a per-event slot index (like the
# totals), so the title is the single source of truth for the number.
_SPREAD_LINE_RE = re.compile(r"by\s+(?:more\s+than|over)\s+(\d+(?:\.\d+)?)", re.IGNORECASE)


def spread_line(yes_sub_title: str | None) -> float | None:
    """The handicap line from Kalshi's spread sub-title ('USA wins by more than
    1.5 goals' → 1.5), or None when the label is missing/unparseable."""
    if not yes_sub_title:
        return None
    m = _SPREAD_LINE_RE.search(yes_sub_title)
    return float(m.group(1)) if m else None


def spread_favorite_code(ticker: str) -> str | None:
    """The favored team's 3-letter code from a spread ticker (the suffix before
    the slot digit), or None if the ticker isn't a spread shape."""
    m = _SPREAD_TICKER_RE.search(ticker)
    return m.group("fav") if m else None


def spread_label(
    ticker: str, yes_sub_title: str | None, event_title: str | None, *, negate: bool
) -> str | None:
    """Side-aware spread label: a YES hold backs the favorite to cover, a NO hold
    fades it. '{Home - Away} — USA -1.5' / '... — USA +1.5 (NO)'. The favorite
    code comes from the ticker; the line from the title (None → line-less). The
    matchup prefers the event title's team names, falling back to the ticker
    codes. Shared by the importable picker and the ledger so both read the same."""
    fav = spread_favorite_code(ticker)
    if fav is None:
        return None
    line = spread_line(yes_sub_title)
    matchup = _spread_matchup(ticker, event_title)
    # YES = favorite covers (-line); NO = favorite fails to cover (the +line side).
    sign = "+" if negate else "-"
    handicap = f"{fav} {sign}{line:g}" if line is not None else f"{fav} {sign}spread"
    return f"{matchup} — {handicap}" if matchup else handicap


def _spread_matchup(ticker: str, event_title: str | None) -> str | None:
    """'Home - Away' for a spread, from the event title's names when present,
    else the ticker's 3-letter codes."""
    if event_title:
        return event_title.replace(" vs ", " - ")
    m = _SPREAD_TICKER_RE.search(ticker)
    return f"{m.group('home')} - {m.group('away')}" if m else None


def parse_market_ticker(ticker: str) -> ParsedMarketTicker | None:
    """Decode a per-game market ticker into series + home/away/selection codes.

    Returns None when the ticker doesn't match the per-game shape (futures,
    derivatives, or an unexpected format) — the caller falls back to showing the
    raw ticker. Pure: no I/O, no team-name resolution (names come from the live
    ESPN feed at the call site, when available)."""
    m = _GAME_TICKER_RE.match(ticker)
    if m is None:
        return None
    return ParsedMarketTicker(
        series=m.group("series"),
        home_code=m.group("home"),
        away_code=m.group("away"),
        selection_code=m.group("sel"),
    )
