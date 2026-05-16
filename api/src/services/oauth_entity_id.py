"""Capture entity_id from OAuth callback artifacts based on provider config.

Driven by `OAuthProvider.entity_id_source`, a JSON dict of shape:
    {"type": "url_param" | "id_token_claim" | "token_response_field", "key": "..."}

The `key` may be a dotted path (e.g. `team.id`) for nested fields.
"""

import base64
import binascii
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _lookup_dotted(d: dict[str, Any], key: str) -> Any:
    current: Any = d
    for part in key.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _decode_id_token_claims(id_token: str) -> dict[str, Any] | None:
    try:
        _, payload_b64, _ = id_token.split(".")
        pad = "=" * (-len(payload_b64) % 4)
        return json.loads(base64.urlsafe_b64decode(payload_b64 + pad))
    except (ValueError, json.JSONDecodeError, binascii.Error) as e:
        logger.warning(f"Failed to decode id_token claims: {e}")
        return None


def extract_entity_id(
    source: dict[str, Any] | None,
    callback_url_params: dict[str, str],
    token_response: dict[str, Any],
) -> str | None:
    """Return entity_id captured from the configured source, or None."""
    if not source:
        return None
    source_type = source.get("type")
    key = source.get("key")
    if not key:
        return None

    if source_type == "url_param":
        return callback_url_params.get(key)

    if source_type == "token_response_field":
        value = _lookup_dotted(token_response, key)
        return str(value) if value is not None else None

    if source_type == "id_token_claim":
        id_token = token_response.get("id_token")
        if not id_token:
            return None
        claims = _decode_id_token_claims(id_token)
        if not claims:
            return None
        value = _lookup_dotted(claims, key)
        return str(value) if value is not None else None

    logger.warning(f"Unknown entity_id_source type: {source_type}")
    return None
