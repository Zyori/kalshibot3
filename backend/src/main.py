"""FastAPI application entrypoint.

Boot sequence (lifespan):
  1. Ensure DB directory exists, dispose any stale engine, run Alembic to head
  2. Probe Kalshi auth (non-fatal — store result in app.state for /api/health)
  3. (Future) start supervisor background tasks
On shutdown: dispose the SQLAlchemy engine cleanly.

Binding is enforced in start.sh (127.0.0.1 only); CORS is whitelisted to the
dashboard's dev origin. There is no user authentication — localhost binding IS
the authentication mechanism. CLAUDE.md hard rule 4.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

from alembic import command
from alembic.config import Config as AlembicConfig
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes import health
from src.config import get_settings
from src.core.db import dispose_engine, get_engine
from src.core.exceptions import AuthenticationError, KalshiError
from src.core.logging import get_logger
from src.kalshi.rest import KalshiRestClient

log = get_logger(__name__)


def _run_migrations_sync() -> None:
    """Run Alembic upgrade head. Synchronous; call via asyncio.to_thread()."""
    ini_path = Path(__file__).resolve().parent.parent / "alembic.ini"
    cfg = AlembicConfig(str(ini_path))
    command.upgrade(cfg, "head")


async def _run_migrations() -> None:
    """Run Alembic in a worker thread so its own asyncio.run() doesn't collide
    with FastAPI's running event loop.

    Alembic's async env.py spins up its own event loop via asyncio.run().
    Calling that from inside an active loop raises "asyncio.run cannot be
    called from a running event loop." Pushing it to a thread isolates the
    two loops cleanly.
    """
    await asyncio.to_thread(_run_migrations_sync)
    log.info("db_migrations_applied")


async def _probe_kalshi_auth(app: FastAPI) -> None:
    """Try one balance call against Kalshi. Cache the outcome on app.state.

    Failure here is non-fatal: the dashboard and ledger still work, the health
    endpoint reports the auth status, and the user can fix credentials + restart.
    """
    app.state.kalshi_auth_ok = False
    app.state.kalshi_auth_error = None
    app.state.kalshi_balance_cents = None
    app.state.kalshi_auth_checked_at = datetime.now(timezone.utc).isoformat()

    try:
        async with KalshiRestClient() as client:
            balance = await client.get_balance()
        app.state.kalshi_auth_ok = True
        app.state.kalshi_balance_cents = balance.balance
        log.info("kalshi_auth_ok", balance_cents=balance.balance)
    except AuthenticationError as e:
        app.state.kalshi_auth_error = f"auth setup: {e}"
        log.warning("kalshi_auth_setup_failed", error=str(e))
    except KalshiError as e:
        app.state.kalshi_auth_error = f"kalshi: {e}"
        log.warning("kalshi_auth_request_failed", error=str(e))
    except Exception as e:  # noqa: BLE001 — startup must never crash on this
        app.state.kalshi_auth_error = f"unexpected: {e}"
        log.exception("kalshi_auth_unexpected", error=str(e))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Run once at startup; run cleanup on shutdown."""
    settings = get_settings()
    app.state.settings = settings

    # 1. Ensure the SQLite parent directory exists, then run migrations.
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    await _run_migrations()

    # 2. Prime the SQLAlchemy engine — the connect-event listener that sets
    # PRAGMAs needs to run before any session does work.
    get_engine()

    # 3. Probe Kalshi auth (non-fatal).
    await _probe_kalshi_auth(app)

    log.info(
        "startup_complete",
        environment=settings.environment.value,
        kalshi_auth_ok=app.state.kalshi_auth_ok,
    )

    yield

    await dispose_engine()
    log.info("shutdown_complete")


app = FastAPI(
    title="kalshibot3",
    version="0.1.0",
    description="Personal Kalshi sports-betting workbook.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api")


@app.get("/")
def root() -> dict[str, str]:
    """Bare liveness — present so a plain GET / can tell the process is up.
    Real status lives at /api/health."""
    return {"app": "kalshibot3", "status": "alive"}
