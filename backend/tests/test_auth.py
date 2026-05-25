"""Tests for src/core/auth.py.

The PEM permission check is one of the plan's critical safety rails — without it
a world-readable private key can sit on disk indefinitely without complaint.
These tests prove it actually fires.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from src.core.auth import KalshiAuth, load_private_key
from src.core.exceptions import AuthenticationError


def _write_rsa_key(path: Path, mode: int) -> None:
    """Write a fresh RSA private key to `path` with the given permissions."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path.write_bytes(pem)
    os.chmod(path, mode)


def test_load_private_key_rejects_world_readable(tmp_path: Path) -> None:
    """A 0o644 key file must be refused."""
    key_path = tmp_path / "loose.pem"
    _write_rsa_key(key_path, 0o644)
    with pytest.raises(AuthenticationError, match="must be 0o600 or stricter"):
        load_private_key(key_path)


def test_load_private_key_rejects_group_readable(tmp_path: Path) -> None:
    """A 0o640 key file must be refused — group leak is still a leak."""
    key_path = tmp_path / "group.pem"
    _write_rsa_key(key_path, 0o640)
    with pytest.raises(AuthenticationError, match="must be 0o600"):
        load_private_key(key_path)


def test_load_private_key_accepts_0o600(tmp_path: Path) -> None:
    """The canonical-correct permission must load successfully."""
    key_path = tmp_path / "ok.pem"
    _write_rsa_key(key_path, 0o600)
    key = load_private_key(key_path)
    assert key is not None
    # Verify it's the perms we set (paranoid double-check the test itself).
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600


def test_load_private_key_accepts_0o400(tmp_path: Path) -> None:
    """Read-only-by-owner is even safer; must also load."""
    key_path = tmp_path / "ro.pem"
    _write_rsa_key(key_path, 0o400)
    assert load_private_key(key_path) is not None


def test_load_private_key_missing_file(tmp_path: Path) -> None:
    with pytest.raises(AuthenticationError, match="not found"):
        load_private_key(tmp_path / "does-not-exist.pem")


def test_load_private_key_garbage_pem(tmp_path: Path) -> None:
    key_path = tmp_path / "garbage.pem"
    key_path.write_bytes(b"not a real PEM file")
    os.chmod(key_path, 0o600)
    with pytest.raises(AuthenticationError, match="Failed to load"):
        load_private_key(key_path)


def test_kalshi_auth_requires_key_id(tmp_path: Path) -> None:
    key_path = tmp_path / "ok.pem"
    _write_rsa_key(key_path, 0o600)
    with pytest.raises(AuthenticationError, match="KALSHI_KEY_ID"):
        KalshiAuth(key_id="", private_key_path=key_path)


def test_kalshi_auth_signs_request(tmp_path: Path) -> None:
    """End-to-end sanity: signer attaches all three required headers."""
    import httpx

    key_path = tmp_path / "ok.pem"
    _write_rsa_key(key_path, 0o600)
    auth = KalshiAuth(key_id="test-key", private_key_path=key_path)

    req = httpx.Request("GET", "https://demo-api.kalshi.co/trade-api/v2/portfolio/balance")
    flow = auth.auth_flow(req)
    signed = next(flow)

    assert signed.headers["KALSHI-ACCESS-KEY"] == "test-key"
    assert "KALSHI-ACCESS-SIGNATURE" in signed.headers
    assert "KALSHI-ACCESS-TIMESTAMP" in signed.headers
    # Timestamp is millis-since-epoch, must be a recent integer.
    ts = int(signed.headers["KALSHI-ACCESS-TIMESTAMP"])
    assert ts > 1_700_000_000_000  # well past 2023; sanity bound
