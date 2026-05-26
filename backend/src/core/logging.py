"""Structured key-value logging.

Wraps the stdlib logger with a small adapter that renders extra kwargs as
`key=value` pairs after the message. Inspired by V2's pattern but kept thin.

Usage:
    log = get_logger(__name__)
    log.info("order_placed", ticker="KX-WC-...", side="yes", price_cents=42)
"""

from __future__ import annotations

import logging
import sys
from typing import Any

def configure_logging() -> None:
    """Configure the `src` namespace logger so structured logs reach journald.

    Called from FastAPI lifespan AFTER Alembic's fileConfig runs — Alembic
    silently disables existing loggers, so any handler attached at import
    time would be wiped.

    We attach a StreamHandler to `src` and reset its disabled bit. Output
    goes to stdout because under uvicorn + systemd the stderr capture path
    eats lines until the process exits.
    """
    pkg_logger = logging.getLogger("src")
    # Alembic's fileConfig sets logger.disabled=True on every non-listed logger.
    # Re-enable ours.
    pkg_logger.disabled = False
    # Re-enable every existing child too (loggers created via get_logger()
    # before configure_logging() ran got disabled by Alembic).
    for name, lg in logging.Logger.manager.loggerDict.items():
        if isinstance(lg, logging.Logger) and name.startswith("src"):
            lg.disabled = False

    # Idempotent handler attach.
    if not any(isinstance(h, logging.StreamHandler) for h in pkg_logger.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        pkg_logger.addHandler(handler)

    pkg_logger.setLevel(logging.INFO)
    pkg_logger.propagate = False  # avoid double-logging through root handler


def _configure_root() -> None:
    """Legacy entrypoint: ensures the src logger works even when called from
    get_logger() before configure_logging(). Kept thin — actual setup is
    deferred to configure_logging() which the lifespan calls post-Alembic."""
    pkg_logger = logging.getLogger("src")
    if pkg_logger.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    pkg_logger.setLevel(logging.INFO)
    pkg_logger.addHandler(handler)
    pkg_logger.propagate = False


class StructuredLogger:
    """Wraps logging.Logger so extra kwargs render as `k=v` pairs."""

    def __init__(self, logger: logging.Logger) -> None:
        self._log = logger

    def _format(self, event: str, **kwargs: Any) -> str:
        if not kwargs:
            return event
        pairs = " ".join(f"{k}={v!r}" for k, v in kwargs.items())
        return f"{event} {pairs}"

    def debug(self, event: str, **kwargs: Any) -> None:
        self._log.debug(self._format(event, **kwargs))

    def info(self, event: str, **kwargs: Any) -> None:
        self._log.info(self._format(event, **kwargs))

    def warning(self, event: str, **kwargs: Any) -> None:
        self._log.warning(self._format(event, **kwargs))

    def error(self, event: str, **kwargs: Any) -> None:
        self._log.error(self._format(event, **kwargs))

    def exception(self, event: str, **kwargs: Any) -> None:
        self._log.exception(self._format(event, **kwargs))


def get_logger(name: str) -> StructuredLogger:
    _configure_root()
    return StructuredLogger(logging.getLogger(name))
