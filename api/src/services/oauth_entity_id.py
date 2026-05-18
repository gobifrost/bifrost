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


# Deny-list for secret-bearing and protocol field names. Case-insensitive.
# Protocol fields (expires_in, scope, token_type) aren't secret but aren't
# useful for entity_id either — excluding them prevents noise and lets the
# caller decide "skip picker" when only protocol fields remain.
_PROTOCOL_FIELDS_EXACT = frozenset({
    "access_token", "refresh_token", "id_token", "code", "client_secret",
    "code_verifier", "assertion", "password", "state", "nonce",
    "expires_in", "scope", "token_type", "expires_at",
})

# Suffix matches (lowercased key endswith one of these). The bare variants
# (token/secret/...) catch camelCase forms like AccessToken; the underscored
# variants catch snake_case like access_token. A few common-English false
# positives (monkey, smokey) are accepted — this is the picker deny-list, not
# a security boundary, and the admin can still set entity_id_source via the
# provider config dialog if a useful field gets hidden.
_SCRUB_SUFFIXES = (
    "_token", "token",
    "_secret", "secret",
    "_key",
    "_password", "password",
    "_signature", "signature",
    "_hmac", "hmac",
)


def _is_scrubbed(key: str) -> bool:
    lower = key.lower()
    if lower in _PROTOCOL_FIELDS_EXACT:
        return True
    return any(lower.endswith(s) for s in _SCRUB_SUFFIXES)


def _walk_leaves(obj: Any, prefix: str = "") -> list[tuple[str, str]]:
    """Walk a dict, emitting (dotted_path, str_value) pairs for non-None leaves.
    Lists are not walked (entity_id is never inside a list in practice)."""
    out: list[tuple[str, str]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                out.extend(_walk_leaves(v, path))
            elif v is not None and not isinstance(v, (list, bytes)):
                out.append((path, str(v)))
    return out


def enumerate_candidate_fields(
    callback_url_params: dict[str, str],
    token_response: dict[str, Any],
) -> list[dict[str, str]]:
    """Enumerate possible entity_id sources from OAuth callback artifacts.

    Returns a list of {"type", "key", "value"} dicts the picker UI can render.
    Secret-bearing fields are scrubbed via _is_scrubbed. id_token is decoded
    and its claims are walked separately.
    """
    candidates: list[dict[str, str]] = []

    for key, value in callback_url_params.items():
        if _is_scrubbed(key) or value is None:
            continue
        candidates.append({"type": "url_param", "key": key, "value": str(value)})

    for key, value in _walk_leaves(
        {k: v for k, v in token_response.items() if k != "id_token"}
    ):
        if any(_is_scrubbed(seg) for seg in key.split(".")):
            continue
        candidates.append({"type": "token_response_field", "key": key, "value": value})

    id_token = token_response.get("id_token")
    if id_token:
        claims = _decode_id_token_claims(id_token)
        if claims:
            for key, value in _walk_leaves(claims):
                if any(_is_scrubbed(seg) for seg in key.split(".")):
                    continue
                candidates.append({"type": "id_token_claim", "key": key, "value": value})

    return candidates
