"""Optional stateless token authentication (OFF by default).

When ``auth_enabled`` is False (the default) the whole module is a no-op:
``is_enabled()`` returns False and callers should leave every route open. No
behaviour changes unless the operator explicitly turns auth on.

Design goals:
  * stdlib only (hmac, hashlib, base64, os, time, json) -- no extra deps
  * stateless HMAC-signed tokens -- no server-side session store
  * never raise -- any malformed input yields None
  * safe to import even before Settings gains the auth_* fields (getattr)

Token format::

    payload = base64url(json{"u": <username>, "exp": <unix_ts + TTL>})
    sig     = base64url(hmac_sha256(secret, payload))
    token   = payload + "." + sig
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Optional

from . import db
from .config import settings

# Token lifetime: 7 days.
TOKEN_TTL = 7 * 24 * 3600

_KV_SECRET_KEY = "auth_secret"


# --------------------------------------------------------------------------- #
# base64url helpers (no padding, never raise)
# --------------------------------------------------------------------------- #
def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


# --------------------------------------------------------------------------- #
# Config-driven state
# --------------------------------------------------------------------------- #
def is_enabled() -> bool:
    """True only when the operator explicitly enabled auth."""
    return bool(getattr(settings, "auth_enabled", False))


def accounts() -> dict:
    """Parse ``auth_accounts`` ("u:p,u2:p2") into {username: password}.

    Malformed entries (missing colon, empty username) are silently ignored.
    """
    raw = getattr(settings, "auth_accounts", "") or ""
    out: dict[str, str] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry or ":" not in entry:
            continue
        user, _, pw = entry.partition(":")
        user = user.strip()
        if not user:
            continue
        out[user] = pw.strip()
    return out


def _secret() -> str:
    """Return the HMAC signing secret.

    Prefers an explicit ``auth_secret`` from Settings. Otherwise lazily
    generates and persists a stable per-install secret in the kv table so
    tokens stay valid across restarts.
    """
    configured = getattr(settings, "auth_secret", "") or ""
    if configured:
        return configured
    try:
        existing = db.kv_get(_KV_SECRET_KEY)
        if existing:
            return existing
        generated = _b64e(os.urandom(32))
        db.kv_set(_KV_SECRET_KEY, generated)
        return generated
    except Exception:
        # If the kv store is unavailable, fall back to an ephemeral secret so
        # signing still works for the lifetime of the process (never raise).
        return _b64e(os.urandom(32))


# --------------------------------------------------------------------------- #
# Token sign / verify
# --------------------------------------------------------------------------- #
def _sign(payload_b64: str) -> str:
    mac = hmac.new(
        _secret().encode("utf-8"),
        payload_b64.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return _b64e(mac)


def _make_token(username: str, exp: int) -> str:
    payload = {"u": username, "exp": int(exp)}
    payload_b64 = _b64e(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    return payload_b64 + "." + _sign(payload_b64)


def login(username: str, password: str) -> Optional[str]:
    """Verify credentials against ``accounts()``; return a signed token or None."""
    try:
        accs = accounts()
        expected = accs.get(username)
        if expected is None:
            return None
        # Constant-time compare to avoid leaking password length/content.
        if not hmac.compare_digest(str(expected), str(password)):
            return None
        return _make_token(username, int(time.time()) + TOKEN_TTL)
    except Exception:
        return None


def verify(token: str) -> Optional[str]:
    """Return the username if the token is well-formed, signed, and unexpired."""
    try:
        if not token or "." not in token:
            return None
        payload_b64, _, sig_b64 = token.partition(".")
        if not payload_b64 or not sig_b64:
            return None
        # Recompute and compare the signature in constant time.
        expected_sig = _sign(payload_b64)
        if not hmac.compare_digest(expected_sig, sig_b64):
            return None
        payload = json.loads(_b64d(payload_b64).decode("utf-8"))
        username = payload.get("u")
        exp = payload.get("exp")
        if not username or exp is None:
            return None
        if int(time.time()) >= int(exp):
            return None
        return username
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Request helper
# --------------------------------------------------------------------------- #
def check_request_token(
    authorization_header: Optional[str],
    query_token: Optional[str] = None,
) -> Optional[str]:
    """Extract a token from the ``Authorization: Bearer <t>`` header (or fall
    back to ``query_token``) and return the verified username, else None."""
    try:
        token: Optional[str] = None
        if authorization_header:
            header = authorization_header.strip()
            parts = header.split(None, 1)
            if len(parts) == 2 and parts[0].lower() == "bearer":
                token = parts[1].strip()
            elif len(parts) == 1:
                # Tolerate a bare token without the "Bearer " prefix.
                token = parts[0].strip()
        if not token and query_token:
            token = query_token.strip()
        if not token:
            return None
        return verify(token)
    except Exception:
        return None
