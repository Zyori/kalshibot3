"""ESPN World Cup news ingestion.

ESPN's `fifa.world/news` endpoint publishes WC headlines for free — injuries,
squad calls, lineups, suspensions — each tagged with the teams it concerns.
That's the price-moving pre-match signal the original plan deferred as "paste
into chat manually"; turns out the source we already poll has it.

In-memory and ephemeral by design (mirrors the ESPN scoreboard snapshot): a
poller keeps the latest ~50 articles in memory, refreshed every few minutes.
News is reference context, not money — no DB, no dedup persistence; a restart
just re-fetches. Headline + description + team tags is the signal; we don't
fetch article bodies (ESPN gates those, and the headline carries the read).
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from src.core.logging import get_logger

log = get_logger(__name__)

NEWS_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/news"
# Adaptive cadence: news trickles in over hours most of the time, but confirmed
# XIs + late injury news drop in the hour before kickoff and move prices. Poll
# slow when no WC game is imminent, fast in the pre-kickoff window — same shape
# as the scoreboard poller. ESPN's news endpoint is free/public, so even the
# fast rate is cheap; the point is mostly to cut the dead pings off-match-day.
POLL_INTERVAL_IDLE_S = 1800   # 30 min — no WC kickoff soon
POLL_INTERVAL_HOT_S = 180     # 3 min — a WC game kicks off within the hour
HTTP_TIMEOUT_S = 8.0
MAX_ARTICLES = 50


@dataclass(frozen=True)
class NewsArticle:
    """One WC news item, normalized. `teams` are the team names ESPN tagged it
    with (matched against game teams to surface relevant news per-game)."""
    headline: str
    description: str
    published: datetime | None
    teams: tuple[str, ...]
    url: str | None


@dataclass
class NewsSnapshot:
    """The reader sees this; the poller swaps it in-place each cycle."""
    articles: list[NewsArticle] = field(default_factory=list)
    refreshed_at: datetime | None = None


def _parse_published(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _article_from_raw(raw: dict[str, Any]) -> NewsArticle | None:
    headline = raw.get("headline")
    if not headline:
        return None
    teams = tuple(
        c.get("description")
        for c in raw.get("categories") or []
        if c.get("type") == "team" and c.get("description")
    )
    url = ((raw.get("links") or {}).get("web") or {}).get("href")
    return NewsArticle(
        headline=str(headline),
        description=str(raw.get("description") or ""),
        published=_parse_published(raw.get("published")),
        teams=teams,
        url=url,
    )


class EspnNews:
    """Polls ESPN's WC news feed into an in-memory snapshot. One instance on the
    supervisor; the news route + partner context read `.snapshot`."""

    def __init__(self, kickoff_soon: Callable[[], bool] | None = None) -> None:
        self.snapshot = NewsSnapshot()
        self._stopped = False
        # Returns True when a WC game kicks off within the hour → poll fast.
        # None → always slow (e.g. tests). Supervisor wires this to the feed.
        self._kickoff_soon = kickoff_soon

    async def run(self) -> None:
        await self._refresh_once()
        while not self._stopped:
            await self._wait_next_poll()
            if self._stopped:
                break
            try:
                await self._refresh_once()
            except Exception:  # noqa: BLE001 — a bad poll never kills the loop
                log.exception("espn_news_refresh_failed")

    async def _wait_next_poll(self) -> None:
        """Sleep until the next poll. Hot now → one hot interval. Idle → sleep in
        hot-interval chunks up to the idle total, but return early the moment a
        kickoff enters the window. A flat idle sleep would commit to the full
        30 min and miss a game crossing the 1-hour horizon mid-sleep — exactly
        when pre-kickoff news (confirmed XI, late injuries) is most price-moving."""
        soon = self._kickoff_soon
        if soon is not None and soon():
            await asyncio.sleep(POLL_INTERVAL_HOT_S)
            return
        waited = 0
        while waited < POLL_INTERVAL_IDLE_S and not self._stopped:
            await asyncio.sleep(POLL_INTERVAL_HOT_S)
            waited += POLL_INTERVAL_HOT_S
            if soon is not None and soon():
                return  # kickoff entered the window — poll now

    async def stop(self) -> None:
        self._stopped = True

    async def _refresh_once(self) -> None:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S) as client:
            r = await client.get(NEWS_URL, params={"limit": MAX_ARTICLES})
        if r.status_code != 200:
            log.warning("espn_news_fetch_non_200", status=r.status_code)
            return
        raw_articles = r.json().get("articles", []) or []
        articles = [a for a in (_article_from_raw(x) for x in raw_articles) if a is not None]
        self.snapshot = NewsSnapshot(
            articles=articles,
            refreshed_at=datetime.now(timezone.utc),
        )
        log.info("espn_news_refreshed", articles=len(articles))

    def for_teams(self, team_names: set[str]) -> list[NewsArticle]:
        """Articles tagged with any of `team_names` (case-insensitive). Used to
        surface a game's relevant news to the partner. Newest first (ESPN
        returns newest-first; we preserve that order)."""
        wanted = {t.lower() for t in team_names}
        return [
            a for a in self.snapshot.articles
            if any(t.lower() in wanted for t in a.teams)
        ]
