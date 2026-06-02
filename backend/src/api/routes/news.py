"""World Cup news — read-only board.

Recent WC headlines from ESPN's free news feed (see ingestion/espn_news.py):
injuries, squad calls, lineups, suspensions — the price-moving pre-match signal,
tagged by team. Read-only reference: a board on the site + relevant items piped
into the partner's per-game context.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from src.core.types import utc_iso

router = APIRouter()


def _article_to_dict(a: Any) -> dict[str, Any]:
    return {
        "headline": a.headline,
        "description": a.description,
        "published": utc_iso(a.published),
        "teams": list(a.teams),
        "url": a.url,
    }


@router.get("/news")
async def get_news(request: Request) -> dict[str, Any]:
    """Recent WC news, newest first. Empty until the first poll lands (or if the
    supervisor isn't up, e.g. tests)."""
    news = getattr(request.app.state, "espn_news", None)
    if news is None:
        return {"articles": [], "refreshed_at": None}
    snap = news.snapshot
    return {
        "articles": [_article_to_dict(a) for a in snap.articles],
        "refreshed_at": utc_iso(snap.refreshed_at),
    }
