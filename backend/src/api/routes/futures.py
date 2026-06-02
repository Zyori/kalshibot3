"""World Cup futures — read-only board.

Tournament-level markets (Winner, Golden Boot, group outcomes) that don't hang
off a game's event page. DISPLAY-ONLY by design: WC futures price in deci-cents
(0.1¢ steps — France 17.0/17.1¢), which the app's whole-cent money core can't
represent without mispricing. Rather than touch that safety-critical core for
markets the user trades rarely and holds for months, this surfaces the futures
board for reading (and for LUTZ context); actual futures trades go on
kalshi.com. If in-app futures trading is ever wanted, deci-cent support in the
money core is its own deliberate unit first.

Prices here are formatted display strings ("17.1¢") straight from Kalshi's
dollar strings — they never enter the integer-cents domain, so the hard
whole-cents rule is untouched.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from src.core.logging import get_logger
from src.kalshi.rest import KalshiRestClient

router = APIRouter()
log = get_logger(__name__)

# Headline WC futures: (series_ticker, section title). Order = display order.
# Verified live 2026-06-02 — each is one event-per-question, markets-per-option.
# Group Winner/Qualify span multiple events (one per group); the rest are single.
_FUTURES_SECTIONS: tuple[tuple[str, str], ...] = (
    ("KXMENWORLDCUP", "Tournament Winner"),
    ("KXWCGOALLEADER", "Golden Boot (Top Scorer)"),
    ("KXWCGROUPWINNER", "Group to Win the Cup"),
    ("KXWCGROUPQUAL", "Group Qualifiers"),
)


def _price_display(dollars: str | None) -> str | None:
    """Kalshi dollar string → a display price like '17.1¢', preserving the
    deci-cent precision the whole-cent converter would round away. None when
    Kalshi has no price. Pure display — never an integer-cents value."""
    if dollars is None:
        return None
    try:
        cents = float(dollars) * 100
    except (TypeError, ValueError):
        return None
    # Trim to one decimal, drop a trailing .0 so 17.0 reads "17", 17.1 reads "17.1".
    text = f"{cents:.1f}".rstrip("0").rstrip(".")
    return f"{text}¢"


def _market_row(m: dict[str, Any]) -> dict[str, Any]:
    """One option within a futures question (a team / player / group)."""
    return {
        "ticker": m.get("ticker"),
        "label": m.get("yes_sub_title"),  # "France", "Kylian Mbappe", "Group A"
        "yes_bid": _price_display(m.get("yes_bid_dollars")),
        "yes_ask": _price_display(m.get("yes_ask_dollars")),
        "last": _price_display(m.get("last_price_dollars")),
        "status": m.get("status"),
    }


def _ask_val(m: dict[str, Any]) -> float:
    """Sort key: favorites (highest YES ask) first. Raw float, sorting only."""
    try:
        return float(m.get("yes_ask_dollars") or 0.0)
    except (TypeError, ValueError):
        return 0.0


async def _fetch_section(client: KalshiRestClient, series: str) -> list[dict[str, Any]]:
    """All events under a futures series, each with its option rows, sorted by
    implied likelihood (highest ask first — favorites on top). Best-effort:
    a failed series yields no events, never breaks the board.

    Reads the RAW /events response (not the Market schema): futures prices are
    deci-cent dollar strings (yes_ask_dollars='0.1710'), and the schema's loose
    config drops those extras + rounds to whole cents. We want the strings."""
    events: list[dict[str, Any]] = []
    cursor: str | None = None
    try:
        while True:
            params: dict[str, Any] = {
                "series_ticker": series, "limit": 200, "with_nested_markets": "true",
            }
            if cursor:
                params["cursor"] = cursor
            data = await client._request("GET", "/events", params=params)
            for ev in data.get("events", []) or []:
                ordered = sorted(ev.get("markets") or [], key=_ask_val, reverse=True)
                events.append({
                    "event_ticker": ev.get("event_ticker"),
                    "title": ev.get("title"),
                    "options": [_market_row(m) for m in ordered],
                })
            cursor = data.get("cursor")
            if not cursor or not data.get("events"):
                break
    except Exception as e:  # noqa: BLE001
        log.warning("futures_section_fetch_failed", series=series, error=str(e)[:120])
        return events
    # Group-style series come as many events (one per group) — sort by title so
    # Group A, B, C… read in order.
    events.sort(key=lambda e: e["title"] or "")
    return events


@router.get("/futures")
async def get_futures() -> dict[str, Any]:
    """The World Cup futures board: headline series, each a section of one or
    more questions, each question a list of options with display prices.
    Read-only — no trading from here (see module docstring)."""
    sections: list[dict[str, Any]] = []
    async with KalshiRestClient() as client:
        for series, title in _FUTURES_SECTIONS:
            events = await _fetch_section(client, series)
            if events:
                sections.append({"series": series, "title": title, "events": events})
    return {"sections": sections}
