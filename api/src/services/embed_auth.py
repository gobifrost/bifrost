"""HMAC verification for embedded app authentication.

Two signing schemes are supported, selected per stored secret via the
`hmac_scheme` column:

1. ``shopify`` — hex-encoded HMAC-SHA256 over the sorted query parameters
   (`key=value&key=value`). All params are signed.
2. ``halopsa`` — base64-encoded HMAC-SHA256 over only the ``agent_id`` value.
   Because no other URL params are covered by the signature, requests using
   this scheme are accepted only when they contain ``agent_id`` and ``hmac``.
"""

import base64
import hashlib
import hmac as hmac_module

SCHEME_SHOPIFY = "shopify"
SCHEME_HALOPSA = "halopsa"
VALID_SCHEMES = frozenset({SCHEME_SHOPIFY, SCHEME_HALOPSA})


def compute_embed_hmac(params: dict[str, str], secret: str) -> str:
    """Compute Shopify-style HMAC-SHA256 over sorted query params.

    Args:
        params: Query parameters (excluding 'hmac' key).
        secret: Shared secret.

    Returns:
        Hex-encoded HMAC digest.
    """
    message = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hmac_module.new(
        secret.encode(), message.encode(), hashlib.sha256
    ).hexdigest()


def _verify_shopify(query_params: dict[str, str], secret: str) -> bool:
    received_hmac = query_params.get("hmac")
    if not received_hmac:
        return False
    remaining = {k: v for k, v in query_params.items() if k != "hmac"}
    expected = compute_embed_hmac(remaining, secret)
    return hmac_module.compare_digest(expected, received_hmac)


def _verify_halopsa(query_params: dict[str, str], secret: str) -> bool:
    if set(query_params) - {"agent_id", "hmac"}:
        return False

    received_hmac = query_params.get("hmac")
    agent_id = query_params.get("agent_id")
    if not received_hmac or not agent_id:
        return False
    digest = hmac_module.new(
        secret.encode(), agent_id.encode(), hashlib.sha256
    ).digest()
    expected = base64.b64encode(digest).decode()
    return hmac_module.compare_digest(expected, received_hmac)


def verify_embed_hmac(
    query_params: dict[str, str], secret: str, scheme: str
) -> bool:
    """Verify an embed URL's HMAC signature using the named scheme.

    Args:
        query_params: All query parameters including 'hmac'.
        secret: Shared secret.
        scheme: One of ``shopify`` or ``halopsa``.

    Returns:
        True if the HMAC is valid for the given scheme.

    Raises:
        ValueError: If ``scheme`` is not a recognized value.
    """
    if scheme == SCHEME_SHOPIFY:
        return _verify_shopify(query_params, secret)
    if scheme == SCHEME_HALOPSA:
        return _verify_halopsa(query_params, secret)
    raise ValueError(f"Unknown HMAC scheme: {scheme!r}")
