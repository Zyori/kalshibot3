"""UTCJSONResponse: every datetime on the wire is UTC + 'Z'-suffixed."""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta

from src.core.json_response import UTCJSONResponse


def _render(content) -> dict:
    body = UTCJSONResponse(content).body
    return json.loads(body)


def test_naive_datetime_assumed_utc():
    """SQLite reads naive datetimes; we assume UTC (that's what we wrote)."""
    naive = datetime(2026, 5, 27, 1, 3, 46)
    out = _render({"placed_at": naive})
    assert out["placed_at"] == "2026-05-27T01:03:46Z"


def test_aware_utc_datetime_keeps_value():
    aware = datetime(2026, 5, 27, 1, 3, 46, tzinfo=timezone.utc)
    out = _render({"placed_at": aware})
    assert out["placed_at"] == "2026-05-27T01:03:46Z"


def test_aware_non_utc_converted_to_utc():
    """An EDT-aware datetime gets normalized to UTC on the wire."""
    edt = timezone(timedelta(hours=-4))
    dt_edt = datetime(2026, 5, 26, 21, 3, 46, tzinfo=edt)
    out = _render({"placed_at": dt_edt})
    assert out["placed_at"] == "2026-05-27T01:03:46Z"


def test_nested_datetimes_work():
    aware = datetime(2026, 5, 27, 1, 3, 46, tzinfo=timezone.utc)
    out = _render({"bets": [{"placed_at": aware}, {"placed_at": aware}]})
    assert out["bets"][0]["placed_at"] == "2026-05-27T01:03:46Z"
    assert out["bets"][1]["placed_at"] == "2026-05-27T01:03:46Z"


def test_none_passes_through():
    out = _render({"placed_at": None})
    assert out["placed_at"] is None


def test_microseconds_preserved():
    naive = datetime(2026, 5, 27, 1, 3, 46, 447393)
    out = _render({"placed_at": naive})
    assert out["placed_at"] == "2026-05-27T01:03:46.447393Z"
