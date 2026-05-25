"""Database engine, session factory, and SQLite hardening.

SQLite's defaults are wrong for a financial app:
  - foreign_keys is OFF, so FK constraints are decorative
  - rollback journal serializes all writes, producing SQLITE_BUSY under async load
  - synchronous=FULL is conservative; NORMAL is safe with WAL and much faster

The connect event listener fixes all of that on every connection (including pooled
reconnects), so the rest of the codebase can trust the pragmas are in effect.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from src.config import get_settings


class Base(DeclarativeBase):
    """SQLAlchemy declarative base. Every model in src/models/ inherits this."""


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _set_sqlite_pragmas(dbapi_connection: object, _connection_record: object) -> None:
    """Apply SQLite hardening pragmas on every new connection.

    Why each pragma:
      journal_mode=WAL — concurrent readers + a single writer without lock storms.
      busy_timeout=5000 — wait up to 5s for a contended write instead of erroring.
      synchronous=NORMAL — safe with WAL, big throughput win vs. default FULL.
      foreign_keys=ON — without this, FK constraints in the schema do NOTHING.
      temp_store=MEMORY — temp tables/indexes in RAM, not on disk.
    """
    cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA temp_store=MEMORY")
    cursor.close()


def get_engine() -> AsyncEngine:
    """Create-once async engine pointed at the configured SQLite file."""
    global _engine, _session_factory
    if _engine is not None:
        return _engine

    settings = get_settings()
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)

    url = f"sqlite+aiosqlite:///{settings.database_path}"
    _engine = create_async_engine(
        url,
        echo=False,
        future=True,
    )

    # The event listener fires on every new DBAPI connection, including reconnects
    # from the pool. Attach to the sync_engine (the underlying SQLAlchemy core
    # engine) — that's the layer the connect event lives at.
    event.listen(_engine.sync_engine, "connect", _set_sqlite_pragmas)

    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Session factory. Engine is created lazily on first call."""
    if _session_factory is None:
        get_engine()
    assert _session_factory is not None
    return _session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency that yields an AsyncSession scoped to the request."""
    factory = get_session_factory()
    async with factory() as session:
        yield session


async def dispose_engine() -> None:
    """Shutdown hook. Tears down the connection pool."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
