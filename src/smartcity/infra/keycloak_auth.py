"""Keycloak IAM integration (Identity and Access Management Generic Enabler).

This module is the thin, framework-agnostic bridge between the application and a
Keycloak realm. It implements the IAM building block described in the paper:
it authenticates operators and exposes the realm roles attached to a verified
identity, so that higher layers can enforce the authorization boundary between
plan generation and execution.

Design notes
------------
* This module is intentionally free of any ``smartcity.core`` import so the
  infrastructure layer never depends on domain models. It returns plain role
  strings; the mapping role -> permitted action lives in
  ``smartcity.core.security``.
* Access tokens are obtained via the OAuth2 *Resource Owner Password Credentials*
  grant (direct access grant). This keeps the PoC server-side and avoids browser
  redirect flows, while still delegating credential verification to Keycloak.
* Bearer tokens are validated locally against the realm JWKS (signature, expiry,
  issuer). When the ``cryptography`` backend is unavailable the module degrades
  gracefully and reports validation as unavailable rather than crashing.
"""

from __future__ import annotations

import os
import time
from threading import Lock
from typing import Any, Dict, List, Optional, Set

import requests

from .logging_utils import configure_logger

logger = configure_logger("keycloak_auth")

# ── Configuration ───────────────────────────────────────────────────────

KEYCLOAK_ENABLED = os.getenv("KEYCLOAK_ENABLED", "true").lower() == "true"
# Internal base URL of the Keycloak server (no trailing slash, no /realms).
KEYCLOAK_URL = os.getenv("KEYCLOAK_URL", "http://localhost:8090").rstrip("/")
KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM", "smartcity")
KEYCLOAK_CLIENT_ID = os.getenv("KEYCLOAK_CLIENT_ID", "smartcity-poc")
KEYCLOAK_CLIENT_SECRET = os.getenv("KEYCLOAK_CLIENT_SECRET", "smartcity-poc-secret")
KEYCLOAK_TIMEOUT_SECONDS = float(os.getenv("KEYCLOAK_TIMEOUT_SECONDS", "5"))
# Audiences accepted in validated tokens. Audience verification is OFF by
# default because Keycloak access tokens do not carry an `aud` claim unless an
# audience mapper is configured. Signature, issuer and expiry are always
# verified. Set KEYCLOAK_AUDIENCE (comma-separated) to enforce `aud` checks.
KEYCLOAK_AUDIENCES = [
    a.strip() for a in os.getenv("KEYCLOAK_AUDIENCE", "").split(",") if a.strip()
]


def _realm_base() -> str:
    return f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}"


def _token_endpoint() -> str:
    return f"{_realm_base()}/protocol/openid-connect/token"


def _jwks_endpoint() -> str:
    return f"{_realm_base()}/protocol/openid-connect/certs"


def is_enabled() -> bool:
    return KEYCLOAK_ENABLED


# ── Authentication (password grant) ───────────────────────────────────────


class AuthError(Exception):
    """Raised when authentication against Keycloak fails."""


def login(username: str, password: str) -> Dict[str, Any]:
    """Authenticate a user via the direct access grant.

    Returns the raw Keycloak token response (``access_token``, ``expires_in``,
    ``refresh_token`` ...). Raises :class:`AuthError` on invalid credentials or
    when Keycloak is unreachable.
    """
    data = {
        "grant_type": "password",
        "client_id": KEYCLOAK_CLIENT_ID,
        "client_secret": KEYCLOAK_CLIENT_SECRET,
        "username": username,
        "password": password,
        "scope": "openid",
    }
    try:
        resp = requests.post(
            _token_endpoint(), data=data, timeout=KEYCLOAK_TIMEOUT_SECONDS
        )
    except requests.RequestException as exc:
        logger.warning("Keycloak unreachable during login: %s", exc)
        raise AuthError("Keycloak unreachable") from exc

    if resp.status_code == 401:
        raise AuthError("Invalid username or password")
    if resp.status_code != 200:
        raise AuthError(f"Keycloak login failed (HTTP {resp.status_code})")
    return resp.json()


# ── Token validation (local JWKS) ─────────────────────────────────────────

_jwks_cache: Dict[str, Any] = {"keys": None, "fetched_at": 0.0}
_jwks_lock = Lock()
_JWKS_TTL_SECONDS = 300


def _get_jwks(force: bool = False) -> Optional[Any]:
    now = time.time()
    with _jwks_lock:
        fresh = _jwks_cache["keys"] is not None and (
            now - _jwks_cache["fetched_at"] < _JWKS_TTL_SECONDS
        )
        if fresh and not force:
            return _jwks_cache["keys"]
        try:
            resp = requests.get(_jwks_endpoint(), timeout=KEYCLOAK_TIMEOUT_SECONDS)
            resp.raise_for_status()
            _jwks_cache["keys"] = resp.json().get("keys", [])
            _jwks_cache["fetched_at"] = now
            return _jwks_cache["keys"]
        except requests.RequestException as exc:
            logger.warning("Could not fetch Keycloak JWKS: %s", exc)
            return _jwks_cache["keys"]  # may be stale or None


def validate_token(access_token: str) -> Optional[Dict[str, Any]]:
    """Validate a Keycloak access token and return its claims.

    Verifies the RS256 signature against the realm JWKS, the expiry, and the
    issuer. Returns the decoded claims on success, or ``None`` if the token is
    invalid or cannot be validated (e.g. crypto backend or JWKS unavailable).
    """
    if not access_token:
        return None
    try:
        import jwt
        from jwt import PyJWKClient  # noqa: F401  (import guarded for clarity)
    except Exception as exc:  # pragma: no cover - dependency guard
        logger.warning("PyJWT/cryptography unavailable, cannot validate token: %s", exc)
        return None

    keys = _get_jwks()
    if not keys:
        return None

    try:
        header = jwt.get_unverified_header(access_token)
    except Exception as exc:
        logger.info("Malformed token header: %s", exc)
        return None

    signing_key = _match_key(keys, header.get("kid"))
    if signing_key is None:
        # Key rotation: refresh once and retry.
        keys = _get_jwks(force=True)
        signing_key = _match_key(keys or [], header.get("kid"))
    if signing_key is None:
        logger.info("No matching JWKS key for token kid=%s", header.get("kid"))
        return None

    try:
        from jwt.algorithms import RSAAlgorithm

        public_key = RSAAlgorithm.from_jwk(signing_key)
        claims = jwt.decode(
            access_token,
            key=public_key,
            algorithms=["RS256"],
            audience=KEYCLOAK_AUDIENCES or None,
            issuer=_realm_base(),
            options={"verify_aud": bool(KEYCLOAK_AUDIENCES)},
        )
        return claims
    except Exception as exc:
        logger.info("Token validation failed: %s", exc)
        return None


def _match_key(keys: List[Dict[str, Any]], kid: Optional[str]) -> Optional[Dict[str, Any]]:
    import json

    for key in keys:
        if key.get("kid") == kid:
            return json.dumps(key)
    return None


# ── Claims helpers ────────────────────────────────────────────────────────


def realm_roles(claims: Dict[str, Any]) -> Set[str]:
    """Extract realm roles from validated token claims."""
    return set((claims.get("realm_access") or {}).get("roles") or [])


def username_from_claims(claims: Dict[str, Any]) -> str:
    return (
        claims.get("preferred_username")
        or claims.get("email")
        or claims.get("sub")
        or "unknown"
    )
