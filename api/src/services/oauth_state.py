"""Signed, timestamped state tokens for OAuth authorize/callback round-trip.

Carries optional `mapping_id` so the callback can attribute the resulting
token to a specific IntegrationMapping. HMAC-signed against
`OAUTH_STATE_SECRET` so the callback can trust the payload without
storing nonces server-side.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

_DEFAULT_TTL = 600  # 10 minutes


class OAuthStateError(Exception):
    """Raised when state decoding fails (bad signature, expired, malformed)."""


def _secret() -> bytes:
    raw = os.environ.get("OAUTH_STATE_SECRET")
    if not raw:
        raise RuntimeError("OAUTH_STATE_SECRET env var must be set")
    return raw.encode()


def _b64url_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def encode_state(payload: dict, ttl_seconds: int = _DEFAULT_TTL) -> str:
    """Encode `payload` as a signed, timestamped state token.

    Adds `nonce` and `exp` automatically; do not pass them.
    """
    body = dict(payload)
    body["nonce"] = secrets.token_urlsafe(16)
    body["exp"] = int(time.time()) + ttl_seconds
    encoded_body = _b64url_encode(json.dumps(body, sort_keys=True).encode())
    sig = hmac.new(_secret(), encoded_body.encode(), hashlib.sha256).digest()
    return f"{encoded_body}.{_b64url_encode(sig)}"


def decode_state(token: str) -> dict:
    """Verify signature + expiry and return the decoded payload."""
    if "." not in token:
        raise OAuthStateError("malformed state token")
    encoded_body, encoded_sig = token.rsplit(".", 1)
    expected_sig = hmac.new(_secret(), encoded_body.encode(), hashlib.sha256).digest()
    try:
        actual_sig = _b64url_decode(encoded_sig)
    except Exception as e:
        raise OAuthStateError("malformed signature") from e
    if not hmac.compare_digest(expected_sig, actual_sig):
        raise OAuthStateError("invalid signature")
    try:
        payload = json.loads(_b64url_decode(encoded_body))
    except Exception as e:
        raise OAuthStateError("malformed payload") from e
    if payload.get("exp", 0) < int(time.time()):
        raise OAuthStateError("state token expired")
    return payload
