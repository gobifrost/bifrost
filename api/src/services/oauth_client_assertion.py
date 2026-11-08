"""OAuth token-endpoint client assertions (RFC 7523).

This is **not** a Bifrost access-token minter. It signs the short-lived
``client_assertion`` used for ``private_key_jwt`` client authentication at an
identity provider's token endpoint, including EC keys (Authlib's client
assertion signer is RSA-only).

It is listed in the reviewed JWT allowlist in
``api/tests/unit/authorization/test_one_minter.py`` so the one-minter tripwire
stays accurate: access tokens are still minted only by
``src.services.authorization.tokens``.
"""

from __future__ import annotations

import secrets
import time

import jwt


def encode_ec_client_assertion(
    *,
    client_id: str,
    token_endpoint: str,
    private_key: str,
    alg: str,
    kid: str | None = None,
) -> str:
    """Sign an EC ``private_key_jwt`` assertion with PyJWT."""
    now = int(time.time())
    headers: dict[str, str] = {"alg": alg, "typ": "JWT"}
    if kid:
        headers["kid"] = kid
    claims = {
        "iss": client_id,
        "sub": client_id,
        "aud": token_endpoint,
        "jti": secrets.token_urlsafe(32),
        "iat": now,
        "exp": now + 300,
    }
    return jwt.encode(claims, private_key, algorithm=alg, headers=headers)
