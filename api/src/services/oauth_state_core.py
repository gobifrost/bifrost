"""Shared primitives for tamper-proof OAuth ``state`` parameters.

The ``state`` parameter carried through an OAuth authorize/callback round-trip
must survive a hostile browser without us trusting it. This module gives both
the MCP client OAuth flow and the per-integration-mapping OAuth flow a single,
production-grade implementation built on:

- JWT (HS256) signed with the application's ``BIFROST_SECRET_KEY``. The same
  primitive used for our user access tokens, so no extra secret to provision.
- An ``aud`` claim distinct per flow so a leaked access token can't masquerade
  as a state token (and vice versa). Callers pick their own audience.
- A nonce + Redis check for single-use enforcement. Best-effort: if Redis is
  unreachable on the write side the flow continues; if Redis is unreachable
  on the consume side the callback fail-safes to rejection.

Callers wrap these primitives with their own payload schema + validation
(see ``src/services/oauth_state.py`` and
``src/services/mcp_client/oauth_state.py``).
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from src.config import get_settings

logger = logging.getLogger(__name__)


_DEFAULT_TTL = timedelta(minutes=10)
_NONCE_BYTES = 16


class StateDecodeError(Exception):
    """Raised when a state JWT is malformed, expired, audience-mismatched, or forged."""


def encode_state_jwt(
    *,
    audience: str,
    claims: dict[str, Any],
    ttl: timedelta = _DEFAULT_TTL,
) -> tuple[str, str]:
    """Encode an OAuth state JWT with a fresh nonce and return ``(token, nonce)``.

    The caller is responsible for persisting the nonce (typically via
    :func:`remember_nonce`) so the callback can reject replays.

    Args:
        audience: The ``aud`` claim — pick something distinct per flow so
            leaked access tokens can't masquerade as state tokens.
        claims: Caller-specific payload merged into the JWT. ``iss``, ``aud``,
            ``iat``, ``exp``, and ``nonce`` are added by this function and
            should not be present in ``claims``.
        ttl: Token lifetime. Defaults to 10 minutes — the user has to consent
            in the popup, not walk away and come back.

    Returns:
        ``(jwt_token, nonce)``. Pass ``jwt_token`` as the ``state`` query
        parameter; persist ``nonce`` for the consume check at callback time.
    """
    nonce = secrets.token_urlsafe(_NONCE_BYTES)
    settings = get_settings()
    now = datetime.now(timezone.utc)

    payload: dict[str, Any] = {
        "iss": settings.jwt_issuer,
        "aud": audience,
        "iat": now,
        "exp": now + ttl,
        "nonce": nonce,
        **claims,
    }
    token = jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)
    return token, nonce


def decode_state_jwt(token: str, *, audience: str) -> dict[str, Any]:
    """Verify a state JWT's signature, issuer, audience, and expiry.

    Does NOT check the nonce against Redis — the caller does that, since
    Redis access requires async I/O and this helper is intentionally pure.

    Args:
        token: The JWT string from the ``state`` query parameter.
        audience: The expected ``aud`` claim. Must match what was passed to
            :func:`encode_state_jwt`.

    Raises:
        StateDecodeError: signature/expiry/audience/issuer/format issue.
    """
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.algorithm],
            issuer=settings.jwt_issuer,
            audience=audience,
        )
    except jwt.ExpiredSignatureError as exc:
        raise StateDecodeError("state expired") from exc
    except jwt.InvalidTokenError as exc:
        raise StateDecodeError(f"invalid state: {exc}") from exc

    if "nonce" not in payload:
        raise StateDecodeError("state missing nonce")

    return payload


def _nonce_key(audience: str, nonce: str) -> str:
    """Redis key for nonce tracking. Audience-scoped so different flows don't collide."""
    return f"oauth_state_nonce:{audience}:{nonce}"


async def remember_nonce(
    nonce: str,
    *,
    audience: str,
    ttl: timedelta = _DEFAULT_TTL,
) -> None:
    """Persist a nonce in Redis so :func:`consume_nonce` can reject replays.

    Best-effort: if Redis is unreachable the connect flow continues. The
    callback's :func:`consume_nonce` will fail-safe to rejection.
    """
    try:
        from src.core.redis_client import get_redis_client

        redis = get_redis_client()
        await redis.setex(_nonce_key(audience, nonce), int(ttl.total_seconds()), "1")
    except Exception as exc:  # noqa: BLE001 — best-effort by design
        logger.warning("OAuth state: failed to record nonce in Redis (%s)", exc)


async def consume_nonce(nonce: str, *, audience: str) -> bool:
    """Atomically check-and-delete a nonce.

    Returns:
        ``True`` if the nonce was present (first-use); ``False`` if it was
        already consumed (replay) OR if Redis is unreachable. The latter
        fail-safe prevents a Redis outage from letting forged callbacks
        through — the callback will reject with "state already used"
        rather than silently passing.
    """
    try:
        from src.core.redis_client import get_redis_client

        redis = get_redis_client()
        deleted = await redis.delete(_nonce_key(audience, nonce))
        return bool(deleted)
    except Exception as exc:  # noqa: BLE001 — best-effort by design
        logger.warning("OAuth state: failed to consume nonce in Redis (%s)", exc)
        return False
