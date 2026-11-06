"""OAuthService id_token verification wiring, including generic OIDC."""

import json
import time
from unittest.mock import AsyncMock, MagicMock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from src.services.oauth_config_service import OAuthProviderConfig
from src.services.oauth_sso import OAuthError, OAuthService
from src.services.oidc_rp import MICROSOFT_CLAIMS_OPTIONS

CLIENT_ID = "client-1"
ISSUER = "https://issuer.test"
DISCOVERY_URL = f"{ISSUER}/.well-known/openid-configuration"
WORK_TENANT = "6f1d2c3b-0000-4000-8000-00000000abcd"


def _private_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwk(private_key, kid: str = "key-1") -> dict:
    jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk.update({"kid": kid, "alg": "RS256", "use": "sig"})
    return jwk


def _claims(**overrides) -> dict:
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


def _token(private_key, claims: dict | None = None) -> str:
    return jwt.encode(
        claims or _claims(),
        private_key,
        algorithm="RS256",
        headers={"kid": "key-1"},
    )


def _discovery() -> dict:
    return {
        "issuer": ISSUER,
        "authorization_endpoint": f"{ISSUER}/authorize",
        "token_endpoint": f"{ISSUER}/token",
        "userinfo_endpoint": f"{ISSUER}/userinfo",
        "jwks_uri": f"{ISSUER}/jwks",
        "id_token_signing_alg_values_supported": ["RS256"],
    }


def _jwks_provider(private_key):
    jwk_set = {"keys": [_jwk(private_key)]}

    async def provider(force: bool) -> dict:
        return jwk_set

    return provider


def _service(**kwargs) -> OAuthService:
    return OAuthService(MagicMock(), **kwargs)


async def _oidc_config(service: OAuthService) -> dict:
    service._config_service.get_provider_config = AsyncMock(
        return_value=OAuthProviderConfig(
            provider="oidc",
            client_id=CLIENT_ID,
            client_secret="client-secret",
            discovery_url=DISCOVERY_URL,
        )
    )
    service._fetch_oidc_discovery = AsyncMock(return_value=_discovery())
    return await service.get_provider_config("oidc")


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [False, True])
async def test_verification_closes_its_client_on_success_and_failure(invalid):
    client = MagicMock()
    client.parse_verified_id_token = AsyncMock(return_value={"sub": "user-1"})
    client.aclose = AsyncMock()
    if invalid:
        client.parse_verified_id_token.side_effect = ValueError("invalid token")
    service = _service(oidc_client_factory=MagicMock(return_value=client))

    if invalid:
        with pytest.raises(OAuthError, match="Could not verify"):
            await service._verify_id_token("google", {"client_id": CLIENT_ID}, {}, "nonce-1")
    else:
        claims = await service._verify_id_token(
            "google", {"client_id": CLIENT_ID}, {}, "nonce-1"
        )
        assert claims == {"sub": "user-1"}

    client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_generic_oidc_config_carries_discovery_and_verifies_id_token():
    key = _private_key()
    service = _service(jwks_provider=_jwks_provider(key))

    config = await _oidc_config(service)

    assert config["discovery_url"] == DISCOVERY_URL
    assert config["discovery"] == _discovery()

    claims = await service._verify_id_token(
        "oidc",
        config,
        {"id_token": _token(key), "access_token": "access-token"},
        "nonce-1",
    )

    assert claims["sub"] == "user-1"
    # Discovery was fetched once by get_provider_config, not again for id_token.
    service._fetch_oidc_discovery.assert_awaited_once_with(DISCOVERY_URL)


@pytest.mark.asyncio
async def test_generic_oidc_verification_works_with_only_discovery_url():
    """A config built before the discovery document was carried still works."""
    key = _private_key()
    service = _service(jwks_provider=_jwks_provider(key))
    service._fetch_oidc_discovery = AsyncMock(return_value=_discovery())

    claims = await service._verify_id_token(
        "oidc",
        {"client_id": CLIENT_ID, "discovery_url": DISCOVERY_URL},
        {"id_token": _token(key), "access_token": "access-token"},
        "nonce-1",
    )

    assert claims["sub"] == "user-1"


@pytest.mark.asyncio
async def test_microsoft_config_keeps_the_tenant():
    service = _service()
    service._config_service.get_provider_config = AsyncMock(
        return_value=OAuthProviderConfig(
            provider="microsoft",
            client_id=CLIENT_ID,
            client_secret="client-secret",
            tenant_id=WORK_TENANT,
        )
    )

    config = await service.get_provider_config("microsoft")
    metadata, claims_options = await service._id_token_metadata("microsoft", config)

    assert config["tenant_id"] == WORK_TENANT
    assert WORK_TENANT in metadata["jwks_uri"]
    assert claims_options is MICROSOFT_CLAIMS_OPTIONS


@pytest.mark.asyncio
async def test_discovery_failure_returns_a_stable_oauth_error():
    service = _service()
    service._fetch_oidc_discovery = AsyncMock(
        side_effect=RuntimeError("boom-internal-detail")
    )

    with pytest.raises(OAuthError) as excinfo:
        await service._verify_id_token(
            "oidc",
            {"client_id": CLIENT_ID, "discovery_url": DISCOVERY_URL},
            {"id_token": "unused"},
            "nonce-1",
        )

    message = str(excinfo.value)
    assert "boom-internal-detail" not in message
    assert "Could not verify" in message


@pytest.mark.asyncio
async def test_invalid_id_token_returns_a_stable_oauth_error():
    key = _private_key()
    service = _service(jwks_provider=_jwks_provider(key))

    with pytest.raises(OAuthError) as excinfo:
        await service._verify_id_token(
            "oidc",
            {"client_id": CLIENT_ID, "discovery": _discovery()},
            {"id_token": "not-a-jwt"},
            "nonce-1",
        )

    message = str(excinfo.value)
    assert "not-a-jwt" not in message
    assert "Could not verify" in message
