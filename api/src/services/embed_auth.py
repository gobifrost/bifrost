"""HMAC verification for embedded app authentication.

Two signing schemes are supported, selected per stored secret via the
`hmac_scheme` column:

1. ``shopify`` — hex-encoded HMAC-SHA256 over the sorted query parameters
   (`key=value&key=value`). All params are signed.
2. ``halopsa`` — base64-encoded HMAC-SHA256 over only the ``agent_id`` value.
   Other URL params are NOT covered by the signature and must be treated as
   untrusted user input.
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
    received_hmac = query_params.get("hmac")
    agent_id = query_params.get("agent_id")
    if not received_hmac or not agent_id:
        return False
    digest = hmac_module.new(
        secret.encode(), agent_id.encode(), hashlib.sha256
    ).digest()
    expected = base64.b64encode(digest).decode()
    return hmac_module.compare_digest(expected, received_hmac)


def verified_embed_context(
    query_params: dict[str, str], secret: str, scheme: str
) -> dict[str, str] | None:
    """Verify an embed signature and return only the fields it covers.

    A successful Shopify signature covers every query field except ``hmac``.
    A successful HaloPSA signature covers only ``agent_id``; additional query
    fields remain untrusted and therefore cannot enter an embed context.

    Returns:
        The signature-covered query fields, or ``None`` when verification fails.

    Raises:
        ValueError: If ``scheme`` is not a recognized value.
    """
    if scheme == SCHEME_SHOPIFY:
        if not _verify_shopify(query_params, secret):
            return None
        return {key: value for key, value in query_params.items() if key != "hmac"}
    if scheme == SCHEME_HALOPSA:
        if not _verify_halopsa(query_params, secret):
            return None
        return {"agent_id": query_params["agent_id"]}
    raise ValueError(f"Unknown HMAC scheme: {scheme!r}")
