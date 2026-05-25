"""FastAPI application entrypoint.

Boot sequence (lifespan, added in chunk 6):
  1. Initialize SQLite (PRAGMAs, schema)
  2. Load Kalshi key, verify PEM permissions
  3. Verify Kalshi auth works (balance check)
  4. Start background tasks (supervisor)

For now this is a hello-world that proves the scaffold runs.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.config import get_settings

settings = get_settings()

app = FastAPI(
    title="kalshibot3",
    version="0.1.0",
    description="Personal Kalshi sports-betting workbook.",
)

# CORS: dashboard runs on localhost:5173 in dev. Explicit whitelist, never "*".
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["*"],
)


@app.get("/")
def root() -> dict[str, str]:
    """Liveness probe. Real /api/health route lands in chunk 6."""
    return {
        "app": "kalshibot3",
        "environment": settings.environment.value,
        "status": "alive",
    }
