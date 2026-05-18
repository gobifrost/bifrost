"""Tamper-proof ``state`` parameter for the MCP OAuth callback.

Thin wrapper over :mod:`src.services.oauth_state_core` that pins the audience
to MCP and validates the MCP-specific payload (``connection_id``,
``flow_type``, ``pkce_verifier``, optional ``user_id``).

This module is the single source of truth for the MCP encode/decode pair.
The ``/connect`` endpoints encode; the callback endpoint decodes and
verifies the nonce hasn't been seen before.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from typing import Any, Literal
from uuid import UUID

from src.services.oauth_state_core import (
    StateDecodeError,
    consume_nonce as _consume_nonce,
    decode_state_jwt,
    encode_state_jwt,
    remember_nonce as _remember_nonce,
)

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
    """Encode an MCP OAuth ``state`` token and return ``(token, nonce)``.

    The caller is expected to record the nonce in Redis (via
    :func:`remember_nonce`) so the callback can reject replays.

    Args:
        connection_id: The MCPConnection this flow targets.
        flow_type: Either ``"service"`` (admin connecting the shared
            service token) or ``"user"`` (per-user delegated token).
        pkce_verifier: The PKCE verifier we'll use at code exchange.
        user_id: Required when ``flow_type == "user"``; rejected when
            ``flow_type == "service"``.
        redirect_uri: The redirect_uri sent to the vendor.

    Returns:
        ``(jwt_token, nonce)``.
    """
    if flow_type == "user" and user_id is None:
        raise ValueError("user_id is required for flow_type='user'")
    if flow_type == "service" and user_id is not None:
        raise ValueError("user_id must be None for flow_type='service'")

    claims: dict[str, Any] = {
        "connection_id": str(connection_id),
        "flow_type": flow_type,
        "pkce_verifier": pkce_verifier,
        "code_challenge": pkce_challenge_for(pkce_verifier),
    }
    if user_id is not None:
        claims["user_id"] = str(user_id)
    if redirect_uri is not None:
        claims["redirect_uri"] = redirect_uri

    return encode_state_jwt(audience=_STATE_AUDIENCE, claims=claims)


def decode_state(token: str) -> dict[str, Any]:
    """Verify the MCP ``state`` JWT and return its payload.

    Raises:
        StateDecodeError: any signature/expiry/format issue, or missing
        MCP-specific fields.
    """
    payload = decode_state_jwt(token, audience=_STATE_AUDIENCE)

    for required in ("connection_id", "flow_type", "pkce_verifier"):
        if required not in payload:
            raise StateDecodeError(f"state missing required field: {required}")

    if payload["flow_type"] not in ("service", "user"):
        raise StateDecodeError(f"invalid flow_type: {payload['flow_type']}")

    if payload["flow_type"] == "user" and "user_id" not in payload:
        raise StateDecodeError("user flow_type missing user_id")

    return payload


async def remember_nonce(nonce: str) -> None:
    """Persist an MCP state nonce in Redis. Best-effort."""
    await _remember_nonce(nonce, audience=_STATE_AUDIENCE)


async def consume_nonce(nonce: str) -> bool:
    """Atomically check-and-delete an MCP state nonce. See core docstring."""
    return await _consume_nonce(nonce, audience=_STATE_AUDIENCE)


__all__ = [
    "FlowType",
    "StateDecodeError",
    "consume_nonce",
    "decode_state",
    "encode_state",
    "generate_pkce_verifier",
    "pkce_challenge_for",
    "remember_nonce",
]
