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

_CONFIGURED = False


def _configure_root() -> None:
    """Set up the root logger exactly once."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers = [handler]
    _CONFIGURED = True


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
