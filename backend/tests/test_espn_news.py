"""ESPN news parsing + per-team matching."""
from __future__ import annotations

from src.ingestion.espn_news import EspnNews, NewsSnapshot, _article_from_raw


def _raw(headline: str, teams: list[str], desc: str = "", published: str | None = "2026-06-01T22:00:00Z") -> dict:
    return {
        "headline": headline,
        "description": desc,
        "published": published,
        "categories": [{"type": "team", "description": t} for t in teams]
        + [{"type": "league", "description": "FIFA World Cup"}],
        "links": {"web": {"href": f"https://espn.com/{headline.replace(' ', '-')}"}},
    }


def test_article_parse():
    a = _article_from_raw(_raw("Saliba doubtful for France", ["France", "Arsenal"]))
    assert a is not None
    assert a.headline == "Saliba doubtful for France"
    assert a.teams == ("France", "Arsenal")
    assert a.url is not None
    assert a.published is not None


def test_article_without_headline_dropped():
    assert _article_from_raw({"categories": []}) is None


def test_article_no_teams():
    a = _article_from_raw(_raw("General WC story", []))
    assert a is not None and a.teams == ()


def test_for_teams_matches_case_insensitive():
    news = EspnNews()
    news.snapshot = NewsSnapshot(articles=[
        _article_from_raw(_raw("England lineup", ["England"])),       # type: ignore[list-item]
        _article_from_raw(_raw("France injury", ["France"])),         # type: ignore[list-item]
        _article_from_raw(_raw("Brazil squad", ["Brazil", "PSG"])),   # type: ignore[list-item]
    ])
    hits = news.for_teams({"england", "BRAZIL"})  # mixed case on purpose
    heads = {a.headline for a in hits}
    assert "England lineup" in heads
    assert "Brazil squad" in heads
    assert "France injury" not in heads


def test_for_teams_empty_when_no_match():
    news = EspnNews()
    news.snapshot = NewsSnapshot(articles=[
        _article_from_raw(_raw("England lineup", ["England"])),  # type: ignore[list-item]
    ])
    assert news.for_teams({"Uruguay"}) == []
