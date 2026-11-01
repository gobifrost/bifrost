"""Browser transport for the same signed access JWT used by bearer clients.

Large tokens are split into bounded HttpOnly cookies, never replaced by an
opaque authority lookup. The manifest binds the complete ordered bytes; the
normal signature, schema and epoch validators still authenticate the result.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Mapping

from fastapi import Response

from shared.browser_impersonation import IMPERSONATION_COOKIE_NAME
from src.config import get_settings
from src.core.security import decode_token, generate_csrf_token

_CHUNK_BYTES = 3600
_CHUNK_PREFIX = "access_token."
_MANIFEST_PREFIX = "chunks."


def read_access_cookie(cookies: Mapping[str, str]) -> str | None:
    """Reassemble a complete cookie token; malformed manifests fail closed."""
    value = cookies.get("access_token")
    if value is None or not value.startswith(_MANIFEST_PREFIX):
        return value
    parts = value.split(".")
    if len(parts) != 3 or not parts[1].isdigit() or len(parts[1]) > 8:
        return None
    count = int(parts[1])
    if count < 2 or count > len(cookies) or len(parts[2]) != 64:
        return None
    chunks = [cookies.get(f"{_CHUNK_PREFIX}{index}") for index in range(count)]
    if any(chunk is None or not chunk or len(chunk) > _CHUNK_BYTES for chunk in chunks):
        return None
    if any(len(chunk) != _CHUNK_BYTES for chunk in chunks[:-1] if chunk is not None):
        return None
    token = "".join(chunk for chunk in chunks if chunk is not None)
    if not hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), parts[2]):
        return None
    return token


def _max_age(token: str, token_type: str) -> int:
    payload = decode_token(token, expected_type=token_type)
    if payload is None or not isinstance(payload.get("exp"), (int, float)):
        raise ValueError("Authentication cookies require a valid expiring token")
    return max(0, int(payload["exp"] - time.time()))


def set_auth_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    """Set byte-bounded access cookies and actual JWT expiry on every cookie."""
    secure = get_settings().is_production
    access_age = _max_age(access_token, "access")
    refresh_age = _max_age(refresh_token, "refresh")
    response.delete_cookie(IMPERSONATION_COOKIE_NAME, path="/")
    def set_part(name: str, value: str) -> None:
        response.set_cookie(
            name, value, httponly=True, secure=secure, samesite="lax", max_age=access_age, path="/",
        )
    # JWTs are ASCII, so character count is byte count. The chunk budget leaves
    # ample room for the full cookie name, attributes, and expiry date.
    if len(access_token) <= _CHUNK_BYTES:
        set_part("access_token", access_token)
    else:
        chunks = [access_token[start:start + _CHUNK_BYTES] for start in range(0, len(access_token), _CHUNK_BYTES)]
        digest = hashlib.sha256(access_token.encode()).hexdigest()
        set_part("access_token", f"{_MANIFEST_PREFIX}{len(chunks)}.{digest}")
        for index, chunk in enumerate(chunks):
            set_part(f"{_CHUNK_PREFIX}{index}", chunk)
    response.set_cookie(
        "refresh_token", refresh_token, httponly=True, secure=secure,
        samesite="lax", max_age=refresh_age, path="/",
    )
    response.set_cookie(
        "csrf_token", generate_csrf_token(), httponly=False, secure=secure,
        samesite="strict", max_age=access_age, path="/",
    )


def clear_auth_cookies(response: Response, cookies: Mapping[str, str] | None = None) -> None:
    """Remove the credential manifest and any received fragments on logout."""
    for name in ("access_token", "refresh_token", "csrf_token", IMPERSONATION_COOKIE_NAME):
        response.delete_cookie(name, path="/")
    # Only a complete manifest can name fragments. Full-size preceding chunks
    # bound deletion work by the incoming bytes; arbitrary cookie names cannot
    # amplify a small Cookie header into hundreds of deletion headers.
    if cookies is not None:
        manifest = cookies.get("access_token", "")
        if manifest.startswith(_MANIFEST_PREFIX) and read_access_cookie(cookies) is not None:
            for index in range(int(manifest.split(".")[1])):
                response.delete_cookie(f"{_CHUNK_PREFIX}{index}", path="/")
