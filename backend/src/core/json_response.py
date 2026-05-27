"""Custom JSON response class that guarantees every datetime on the wire
is UTC and Z-suffixed.

Why: SQLite's DateTime(timezone=True) loses tz info on write — we read
naive datetimes back even when we wrote tz-aware UTC. Calling
`dt.isoformat()` on a naive datetime produces a string with no offset.
JavaScript's `Date()` parses those as LOCAL time, which silently shifts
every Ledger row by the browser's tz offset and we end up showing trades
4 hours into the future.

Setting `default_response_class=UTCJSONResponse` on FastAPI funnels every
route's JSON through this encoder. Routes can still serialize datetimes
manually via core.types.utc_iso() (preferred — type-checks at the call
site), but this is the safety net: anyone returning a raw dict with a
datetime value gets correct output too.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any

from fastapi.responses import JSONResponse


def _utc_z(dt: datetime) -> str:
    """Normalize a datetime to UTC and ISO-format with 'Z' suffix.

    Naive datetime: assume UTC (matches every site that writes
    datetime.now(timezone.utc) into the DB).
    Aware datetime: convert to UTC.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _default(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return _utc_z(obj)
    if isinstance(obj, date):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


class UTCJSONResponse(JSONResponse):
    """Drop-in JSONResponse that runs every datetime through _utc_z.

    FastAPI calls `render(content)` to serialize the route's return value.
    We bypass the default jsonable_encoder + json.dumps path with a single
    json.dumps that delegates unknown types to `_default`.
    """

    def render(self, content: Any) -> bytes:
        return json.dumps(
            content,
            ensure_ascii=False,
            allow_nan=False,
            indent=None,
            separators=(",", ":"),
            default=_default,
        ).encode("utf-8")
