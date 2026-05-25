"""Kalshi RSA-PSS request signing.

Ported from V2 (Kalshi-Mean-Reversion-Bot/backend/src/core/auth.py) with one
addition required by the plan: PEM file permissions are checked at load time
and the app refuses to start if the key is more permissive than 0o600.

Kalshi auth is stateless — every request is independently signed with the key.
There is no token, no expiry, no refresh.
"""

from __future__ import annotations

import base64
import stat
import time
from collections.abc import Generator
from pathlib import Path

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

from src.core.exceptions import AuthenticationError

# 0o600 = read/write for owner only. The key file must be no more permissive.
_MAX_KEY_PERMS = 0o600


def _check_key_permissions(path: Path) -> None:
    """Refuse to load a key file with world- or group-readable permissions.

    Catches the "accidentally chmod 644" footgun before a private key ever sits
    in memory. The bits we care about are the lower 9 (rwxrwxrwx); higher bits
    (setuid, sticky) aren't a leakage risk so we ignore them.
    """
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & ~_MAX_KEY_PERMS:
        raise AuthenticationError(
            f"Kalshi key {path} has permissions {oct(mode)}; "
            f"must be {oct(_MAX_KEY_PERMS)} or stricter. "
            f"Run: chmod 600 {path}"
        )


def load_private_key(path: Path) -> RSAPrivateKey:
    """Load an RSA private key from disk, after verifying file permissions."""
    if not path.exists():
        raise AuthenticationError(f"Kalshi key not found: {path}")

    _check_key_permissions(path)

    try:
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    except Exception as e:  # noqa: BLE001 — wrap any cryptography error
        raise AuthenticationError(f"Failed to load Kalshi key {path}: {e}") from e

    if not isinstance(key, RSAPrivateKey):
        raise AuthenticationError(f"Kalshi key {path} is not an RSA key")

    return key


class KalshiAuth(httpx.Auth):
    """httpx auth flow that signs every Kalshi request."""

    def __init__(self, key_id: str, private_key_path: Path) -> None:
        if not key_id:
            raise AuthenticationError("KALSHI_KEY_ID is not set")
        self.key_id = key_id
        self.private_key = load_private_key(private_key_path)

    def _sign(self, timestamp_ms: str, method: str, path: str) -> str:
        message = f"{timestamp_ms}{method}{path}".encode()
        signature = self.private_key.sign(
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
            hashes.SHA256(),
        )
        return base64.b64encode(signature).decode("ascii")

    def auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response, None]:
        timestamp_ms = str(int(time.time() * 1000))
        # raw_path preserves the full path including the /trade-api/v2 prefix.
        # Kalshi signs the path-after-host portion, query string excluded.
        path = request.url.raw_path.decode("utf-8").split("?")[0]
        signature = self._sign(timestamp_ms, request.method, path)

        request.headers["KALSHI-ACCESS-KEY"] = self.key_id
        request.headers["KALSHI-ACCESS-SIGNATURE"] = signature
        request.headers["KALSHI-ACCESS-TIMESTAMP"] = timestamp_ms
        yield request
