"""Tamper-proof ``state`` parameter for the MCP OAuth callback.

The ``state`` value passed through the vendor's authorize URL must:

- Survive a round trip through the user's browser without us trusting the
  browser. Reuse JWT signing (HS256 with the application secret) — we
  already have the primitives in ``src/core/security.py``.
- Carry the connection ID, the flow type ("service" vs "user"), the user
  ID for per-user flows, and the PKCE code verifier (the challenge sent
  to the vendor is derived from this — we can't keep the verifier on the
  vendor side or in the client).
- Be single-use. A nonce + a Redis check (``mcp_oauth_state:<nonce>``)
  catches replay attempts.
- Expire fast (10 minutes). The user has to consent in the popup, not
  walk away to lunch and come back.

This module is the single source of truth for the encode/decode pair.
The ``/connect`` endpoints encode; the callback endpoint decodes and
verifies the nonce hasn't been seen before.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import UUID

import jwt

from src.config import get_settings

logger = logging.getLogger(__name__)


_STATE_TTL = timedelta(minutes=10)
_NONCE_BYTES = 16
# Distinct from JWT access tokens — set ``aud`` so a leaked access token can't
# masquerade as a state token (and vice versa).
_STATE_AUDIENCE = "bifrost-mcp-oauth-state"


FlowType = Literal["service", "user"]


def generate_pkce_verifier() -> str:
    """RFC 7636: 43–128 chars of unreserved-set entropy.

    We use ``secrets.token_urlsafe(64)`` truncated to 128 chars; the
    base64url alphabet is a subset of the unreserved set.
    """
    return secrets.token_urlsafe(64)[:128]


def pkce_challenge_for(verifier: str) -> str:
    """RFC 7636 §4.2 S256 challenge: base64url(sha256(verifier)) without padding."""
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def encode_state(
    *,
    connection_id: UUID,
    flow_type: FlowType,
    pkce_verifier: str,
    user_id: UUID | None = None,
    redirect_uri: str | None = None,
) -> tuple[str, str]:
    """Encode an OAuth ``state`` token and return ``(token, nonce)``.

    The caller is expected to record the nonce in Redis so the callback
    can reject replays. We don't write to Redis here — the caller owns
    transactional ordering with the rest of the connect flow.

    Args:
        connection_id: The MCPConnection this flow targets.
        flow_type: Either ``"service"`` (admin connecting the shared
            service token) or ``"user"`` (per-user delegated token).
        pkce_verifier: The PKCE verifier we'll use at code exchange. The
            challenge sent to the vendor is derived from it via
            ``pkce_challenge_for``.
        user_id: Required when ``flow_type == "user"``; rejected when
            ``flow_type == "service"`` (service flow is not user-bound).
        redirect_uri: The redirect_uri sent to the vendor. Echoed back at
            code exchange so we don't have to look it up again — the
            vendor requires the same value on both legs.

    Returns:
        A tuple ``(jwt_token, nonce)``. Pass ``jwt_token`` as the
        ``state`` query parameter; persist ``nonce`` in Redis with TTL.
    """
    if flow_type == "user" and user_id is None:
        raise ValueError("user_id is required for flow_type='user'")
    if flow_type == "service" and user_id is not None:
        raise ValueError("user_id must be None for flow_type='service'")

    nonce = secrets.token_urlsafe(_NONCE_BYTES)
    settings = get_settings()
    now = datetime.now(timezone.utc)

    payload: dict[str, Any] = {
        "iss": settings.jwt_issuer,
        "aud": _STATE_AUDIENCE,
        "iat": now,
        "exp": now + _STATE_TTL,
        "nonce": nonce,
        "connection_id": str(connection_id),
        "flow_type": flow_type,
        "pkce_verifier": pkce_verifier,
        "code_challenge": pkce_challenge_for(pkce_verifier),
    }
    if user_id is not None:
        payload["user_id"] = str(user_id)
    if redirect_uri is not None:
        payload["redirect_uri"] = redirect_uri

    token = jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)
    return token, nonce


class StateDecodeError(Exception):
    """Raised when the ``state`` parameter is malformed, expired, or forged."""


def decode_state(token: str) -> dict[str, Any]:
    """Verify the ``state`` JWT and return its payload.

    Verifies the signature, the audience (``bifrost-mcp-oauth-state``),
    the issuer, and the expiration. Does NOT check the nonce against
    Redis — the caller does that, since Redis access requires async I/O
    and this helper is intentionally pure.

    Raises:
        StateDecodeError: any signature/expiry/format issue.
    """
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.algorithm],
            issuer=settings.jwt_issuer,
            audience=_STATE_AUDIENCE,
        )
    except jwt.ExpiredSignatureError as exc:
        raise StateDecodeError("state expired") from exc
    except jwt.InvalidTokenError as exc:
        raise StateDecodeError(f"invalid state: {exc}") from exc

    # Defensive type checks — JWT will accept anything that round-trips.
    for required in ("connection_id", "flow_type", "pkce_verifier", "nonce"):
        if required not in payload:
            raise StateDecodeError(f"state missing required field: {required}")

    if payload["flow_type"] not in ("service", "user"):
        raise StateDecodeError(f"invalid flow_type: {payload['flow_type']}")

    if payload["flow_type"] == "user" and "user_id" not in payload:
        raise StateDecodeError("user flow_type missing user_id")

    return payload


# =============================================================================
# Redis nonce tracking — single-use enforcement
# =============================================================================


def _nonce_key(nonce: str) -> str:
    """Redis key for nonce tracking."""
    return f"mcp_oauth_state:{nonce}"


async def remember_nonce(nonce: str) -> None:
    """Persist a nonce in Redis with TTL matching the state JWT.

    Best-effort: if Redis is unreachable the connect flow continues and
    the callback will fail-safe (replay protection degrades to
    "callback verifies signature but allows replay within TTL"). Logged
    so an operator can investigate.
    """
    try:
        from src.core.redis_client import get_redis_client

        redis = get_redis_client()
        await redis.setex(
            _nonce_key(nonce),
            int(_STATE_TTL.total_seconds()),
            "1",
        )
    except Exception as exc:
        logger.warning("MCP OAuth: failed to record state nonce in Redis (%s)", exc)


async def consume_nonce(nonce: str) -> bool:
    """Atomically check-and-delete a nonce.

    Returns True if the nonce was present (first-use), False if it was
    already consumed (replay) OR if Redis is unreachable. The latter
    fail-safe prevents a Redis outage from silently breaking the OAuth
    flow — the callback will reject the request with "state already
    used" rather than letting a forged callback through.
    """
    try:
        from src.core.redis_client import get_redis_client

        redis = get_redis_client()
        # Atomic check-and-delete: DEL returns the number of removed keys.
        deleted = await redis.delete(_nonce_key(nonce))
        return bool(deleted)
    except Exception as exc:
        logger.warning("MCP OAuth: failed to consume state nonce in Redis (%s)", exc)
        return False
