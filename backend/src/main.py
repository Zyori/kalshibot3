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

from src.api import ws as ws_endpoint
from src.api.routes import (
    events,
    futures,
    health,
    ledger,
    markets,
    orders,
    partner,
    positions,
    settings,
)
from src.config import get_settings
from src.core.db import dispose_engine, get_engine
from src.core.exceptions import AuthenticationError, KalshiError
from src.core.json_response import UTCJSONResponse
from src.core.logging import configure_logging, get_logger
from src.kalshi.rest import KalshiRestClient
from src.supervisor import Supervisor

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
    # Alembic's env.py calls fileConfig() which disables existing loggers
    # unless told otherwise. We configure our own logging AFTER this so it
    # survives. (Both Alembic and our logger end up reachable.)
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    await _run_migrations()

    configure_logging()

    # 2. Prime the SQLAlchemy engine — the connect-event listener that sets
    # PRAGMAs needs to run before any session does work.
    get_engine()

    # 3. Probe Kalshi auth (non-fatal).
    await _probe_kalshi_auth(app)

    # 4. Spin up the supervisor: Kalshi WS consumer + browser broadcaster.
    # Only start if auth probe succeeded — without auth, the WS connect
    # would just reconnect-loop forever logging errors.
    app.state.supervisor = Supervisor()
    app.state.live_state = app.state.supervisor.live_state
    app.state.broadcast = app.state.supervisor.broadcast
    app.state.price_history = app.state.supervisor.price_history
    app.state.supervisor.app_state = app.state
    if app.state.kalshi_auth_ok:
        await app.state.supervisor.start()
    else:
        log.warning("supervisor_skipped_kalshi_auth_failed")

    log.info(
        "startup_complete",
        environment=settings.environment.value,
        kalshi_auth_ok=app.state.kalshi_auth_ok,
    )

    yield

    await app.state.supervisor.stop()
    await dispose_engine()
    log.info("shutdown_complete")


app = FastAPI(
    title="kalshibot3",
    version="0.1.0",
    description="Personal Kalshi sports-betting workbook.",
    lifespan=lifespan,
    default_response_class=UTCJSONResponse,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api")
app.include_router(markets.router, prefix="/api")
app.include_router(orders.router, prefix="/api")
app.include_router(positions.router, prefix="/api")
app.include_router(ledger.router, prefix="/api")
app.include_router(events.router, prefix="/api")
app.include_router(settings.router, prefix="/api")
app.include_router(partner.router, prefix="/api")
app.include_router(futures.router, prefix="/api")
app.include_router(ws_endpoint.router)


@app.get("/")
def root() -> dict[str, str]:
    """Bare liveness — present so a plain GET / can tell the process is up.
    Real status lives at /api/health."""
    return {"app": "kalshibot3", "status": "alive"}
