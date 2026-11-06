"""OIDC relying-party id_token validation through Authlib."""

import json
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from src.services.oidc_rp import (
    MICROSOFT_CLAIMS_OPTIONS,
    IDTokenValidationError,
    OIDCRelyingParty,
    microsoft_issuer_is_valid,
)

ISSUER = "https://issuer.test"
CLIENT_ID = "client-1"
WORK_TENANT = "6f1d2c3b-0000-4000-8000-00000000abcd"
JwksProvider = Callable[[bool], Awaitable[Mapping[str, Any]]]


def _private_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwk(private_key, kid: str) -> dict[str, Any]:
    jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk.update({"kid": kid, "alg": "RS256", "use": "sig"})
    return jwk


def _claims(**overrides: Any) -> dict[str, Any]:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "sub": "user-1",
        "aud": CLIENT_ID,
        "exp": now + 300,
        "iat": now,
        "nonce": "nonce-1",
        "tid": WORK_TENANT,
    }
    claims.update(overrides)
    return claims


def _token(private_key, claims: Mapping[str, Any], kid: str = "key-1") -> str:
    return jwt.encode(
        dict(claims), private_key, algorithm="RS256", headers={"kid": kid}
    )


def _provider(
    first_key_set: Mapping[str, Any],
    *remaining_key_sets: Mapping[str, Any],
) -> tuple[JwksProvider, list[bool]]:
    calls: list[bool] = []

    async def provider(force: bool) -> Mapping[str, Any]:
        calls.append(force)
        return remaining_key_sets[0] if force and remaining_key_sets else first_key_set

    return provider, calls


def _rp(
    provider: JwksProvider,
    *,
    claims_options: Mapping[str, Any] | None = None,
    issuer: str = ISSUER,
) -> OIDCRelyingParty:
    return OIDCRelyingParty(
        client_id=CLIENT_ID,
        server_metadata={
            "issuer": issuer,
            "jwks_uri": f"{issuer}/jwks",
            "id_token_signing_alg_values_supported": ["RS256"],
        },
        claims_options=claims_options,
        jwks_provider=provider,
    )


async def _parse(rp: OIDCRelyingParty, token: str, nonce: str = "nonce-1") -> dict:
    return await rp.parse_verified_id_token(
        {"id_token": token, "access_token": "access-token"}, nonce
    )


@pytest.mark.asyncio
async def test_valid_id_token_is_accepted_with_verified_claims():
    key = _private_key()
    provider, calls = _provider({"keys": [_jwk(key, "key-1")]})
    rp = _rp(provider)

    claims = await _parse(rp, _token(key, _claims()))

    assert claims["sub"] == "user-1"
    assert claims["tid"] == WORK_TENANT
    assert calls == [False]


@pytest.mark.asyncio
async def test_bad_signature_is_rejected():
    signing_key, jwks_key = _private_key(), _private_key()
    provider, _ = _provider({"keys": [_jwk(jwks_key, "key-1")]})
    rp = _rp(provider)

    with pytest.raises(IDTokenValidationError, match="signature"):
        await _parse(rp, _token(signing_key, _claims()))


@pytest.mark.asyncio
async def test_wrong_issuer_is_rejected():
    key = _private_key()
    provider, _ = _provider({"keys": [_jwk(key, "key-1")]})
    rp = _rp(provider)

    with pytest.raises(IDTokenValidationError, match="iss"):
        await _parse(rp, _token(key, _claims(iss="https://evil.example")))


@pytest.mark.asyncio
async def test_wrong_audience_is_rejected():
    key = _private_key()
    provider, _ = _provider({"keys": [_jwk(key, "key-1")]})
    rp = _rp(provider)

    with pytest.raises(IDTokenValidationError, match="aud"):
        await _parse(rp, _token(key, _claims(aud="someone-else")))


@pytest.mark.asyncio
async def test_another_audience_cannot_pass_through_azp():
    """Authlib alone accepts aud=other+azp=client; the aud option must reject."""
    key = _private_key()
    provider, _ = _provider({"keys": [_jwk(key, "key-1")]})
    rp = _rp(provider)

    with pytest.raises(IDTokenValidationError, match="aud"):
        await _parse(
            rp, _token(key, _claims(aud="someone-else", azp=CLIENT_ID))
        )


@pytest.mark.asyncio
async def test_expired_id_token_is_rejected():
    key = _private_key()
    provider, _ = _provider({"keys": [_jwk(key, "key-1")]})
    rp = _rp(provider)

    with pytest.raises(IDTokenValidationError, match="exp"):
        await _parse(rp, _token(key, _claims(exp=int(time.time()) - 600)))


@pytest.mark.asyncio
async def test_nonce_mismatch_is_rejected():
    key = _private_key()
    provider, _ = _provider({"keys": [_jwk(key, "key-1")]})
    rp = _rp(provider)

    with pytest.raises(IDTokenValidationError, match="nonce"):
        await _parse(rp, _token(key, _claims()), nonce="other-nonce")


@pytest.mark.asyncio
async def test_unknown_kid_forces_one_jwks_refresh_then_succeeds():
    stale_key, current_key = _private_key(), _private_key()
    provider, calls = _provider(
        {"keys": [_jwk(stale_key, "old-key")]},
        {"keys": [_jwk(current_key, "current-key")]},
    )
    rp = _rp(provider)

    claims = await _parse(rp, _token(current_key, _claims(), kid="current-key"))

    assert claims["sub"] == "user-1"
    assert calls == [False, True]


@pytest.mark.asyncio
async def test_missing_id_token_is_rejected():
    provider, _ = _provider({"keys": []})
    rp = _rp(provider)

    with pytest.raises(IDTokenValidationError, match="id_token"):
        await rp.parse_verified_id_token({"access_token": "access-token"}, "nonce-1")


def test_microsoft_issuer_matches_the_tokens_own_tenant():
    claims = {"tid": WORK_TENANT}

    assert microsoft_issuer_is_valid(
        claims, f"https://login.microsoftonline.com/{WORK_TENANT}/v2.0"
    )
    assert microsoft_issuer_is_valid(
        claims, f"https://sts.windows.net/{WORK_TENANT}/"
    )
    assert not microsoft_issuer_is_valid(
        claims, "https://login.microsoftonline.com/other-tenant/v2.0"
    )
    assert not microsoft_issuer_is_valid(claims, "https://evil.example")


@pytest.mark.asyncio
async def test_microsoft_claims_use_the_tenant_issuer_validator():
    key = _private_key()
    provider, _ = _provider({"keys": [_jwk(key, "key-1")]})
    rp = _rp(provider)

    token = _token(
        key,
        _claims(iss=f"https://login.microsoftonline.com/{WORK_TENANT}/v2.0"),
    )
    # Without the Microsoft options the generic issuer check rejects the
    # per-tenant issuer; with them it is accepted.
    with pytest.raises(IDTokenValidationError):
        await _parse(rp, token)

    microsoft_rp = _rp(provider, claims_options=MICROSOFT_CLAIMS_OPTIONS)
    claims = await _parse(microsoft_rp, token)
    assert claims["tid"] == WORK_TENANT


@pytest.mark.asyncio
async def test_microsoft_issuer_hook_survives_repeated_parses():
    """Authlib pops validate hooks from the options dict it is given."""
    key = _private_key()
    provider, _ = _provider({"keys": [_jwk(key, "key-1")]})
    rp = _rp(provider, claims_options=MICROSOFT_CLAIMS_OPTIONS)

    good = _token(
        key, _claims(iss=f"https://login.microsoftonline.com/{WORK_TENANT}/v2.0")
    )
    claims = await _parse(rp, good)
    assert claims["tid"] == WORK_TENANT

    evil = _token(key, _claims(iss="https://evil.example"))
    with pytest.raises(IDTokenValidationError, match="iss"):
        await _parse(rp, evil)

    assert MICROSOFT_CLAIMS_OPTIONS == {
        "iss": {"validate": microsoft_issuer_is_valid}
    }


@pytest.mark.asyncio
async def test_missing_or_non_string_nonce_is_rejected():
    key = _private_key()
    provider, _ = _provider({"keys": [_jwk(key, "key-1")]})
    rp = _rp(provider)
    token = _token(key, _claims())

    for nonce in ("", None):
        with pytest.raises(IDTokenValidationError, match="nonce"):
            await rp.parse_verified_id_token(
                {"id_token": token, "access_token": "access-token"},
                nonce,  # type: ignore[arg-type]
            )
