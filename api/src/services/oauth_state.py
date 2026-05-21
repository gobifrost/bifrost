"""Tamper-proof ``state`` parameter for per-mapping integration OAuth.

Thin wrapper over :mod:`src.services.oauth_state_core` that pins the audience
to integration OAuth and validates the payload shape (``provider_id``,
``mapping_id``).

The per-mapping authorize endpoint encodes; the callback decodes and
verifies the nonce hasn't been seen before. Replay protection is critical
because the same state can otherwise be replayed for the full TTL window.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from src.services.oauth_state_core import (
    StateDecodeError,
    consume_nonce as _consume_nonce,
    decode_state_jwt,
    encode_state_jwt,
    remember_nonce as _remember_nonce,
)

# Distinct from MCP state tokens AND from JWT access tokens so neither can
# masquerade as the other.
_STATE_AUDIENCE = "bifrost-integration-oauth-state"


# Backwards-compat alias for existing callers / tests that imported the old name.
OAuthStateError = StateDecodeError


def encode_state(
    *,
    provider_id: UUID,
    mapping_id: UUID,
) -> tuple[str, str]:
    """Encode a per-mapping OAuth ``state`` token and return ``(token, nonce)``.

    The caller is expected to record the nonce via :func:`remember_nonce`
    so the callback can reject replays.
    """
    claims: dict[str, Any] = {
        "provider_id": str(provider_id),
        "mapping_id": str(mapping_id),
    }
    return encode_state_jwt(audience=_STATE_AUDIENCE, claims=claims)


def decode_state(token: str) -> dict[str, Any]:
    """Verify the per-mapping ``state`` JWT and return its payload.

    Raises:
        OAuthStateError: any signature/expiry/format issue, or missing
        per-mapping fields.
    """
    payload = decode_state_jwt(token, audience=_STATE_AUDIENCE)

    for required in ("provider_id", "mapping_id"):
        if required not in payload:
            raise OAuthStateError(f"state missing required field: {required}")

    return payload


async def remember_nonce(nonce: str) -> None:
    """Persist a per-mapping state nonce in Redis. Best-effort."""
    await _remember_nonce(nonce, audience=_STATE_AUDIENCE)


async def consume_nonce(nonce: str) -> bool:
    """Atomically check-and-delete a per-mapping state nonce. See core docstring."""
    return await _consume_nonce(nonce, audience=_STATE_AUDIENCE)


__all__ = [
    "OAuthStateError",
    "consume_nonce",
    "decode_state",
    "encode_state",
    "remember_nonce",
]
