"""Token-request shape for each OAuth client authentication method."""

import base64
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs

import httpx as httpx_module
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from src.services import oauth_sso
from src.services.oauth_config_service import (
    OAuthConfigValidationError,
    _validate_rsa_private_key,
)
from src.services.oauth_sso import OAuthError, OAuthService

_TOKEN_URL = "https://idp.test/token"
_RealAsyncClient = httpx_module.AsyncClient


class _FakeRelyingParty:
    async def aclose(self):
        pass

    async def parse_verified_id_token(self, token, nonce):
        return {"sub": "user-1"}


def _service() -> OAuthService:
    return OAuthService(
        MagicMock(), oidc_client_factory=lambda **_kwargs: _FakeRelyingParty()
    )


def _config(method: str, **overrides) -> dict:
    config = {
        "client_id": "client-id",
        "client_secret": "client-secret",
        "token_url": _TOKEN_URL,
        "token_endpoint_auth_method": method,
        "private_key": None,
        "private_key_kid": None,
        "private_key_alg": "RS256",
    }
    config.update(overrides)
    return config


def _private_key_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def _capture(monkeypatch):
    captured: dict[str, object] = {}

    async def handler(request: httpx_module.Request) -> httpx_module.Response:
        captured["request"] = request
        return httpx_module.Response(
            200,
            json={
                "access_token": "access-token",
                "id_token": "id-token",
                "token_type": "Bearer",
            },
        )

    transport = httpx_module.MockTransport(handler)

    def factory(*_args, **_kwargs):
        return _RealAsyncClient(transport=transport)

    monkeypatch.setattr(oauth_sso.httpx, "AsyncClient", factory)
    return captured


async def _exchange(service: OAuthService, method: str, **overrides):
    service.get_provider_config = AsyncMock(return_value=_config(method, **overrides))
    return await service.exchange_code_for_tokens(
        "google", "auth-code", "https://app.test/callback", "verifier", "nonce-1"
    )


@pytest.mark.asyncio
async def test_client_secret_post_puts_the_secret_in_the_body(monkeypatch):
    captured = _capture(monkeypatch)

    await _exchange(_service(), "client_secret_post")

    request = captured["request"]
    body = parse_qs(request.content.decode())
    assert body["client_secret"] == ["client-secret"]
    assert body["client_id"] == ["client-id"]
    assert body["code"] == ["auth-code"]
    assert "authorization" not in {key.lower() for key in request.headers}


@pytest.mark.asyncio
async def test_client_secret_basic_uses_the_authorization_header(monkeypatch):
    captured = _capture(monkeypatch)

    await _exchange(_service(), "client_secret_basic")

    request = captured["request"]
    body = parse_qs(request.content.decode())
    authorization = request.headers["authorization"]
    assert authorization.startswith("Basic ")
    decoded = base64.b64decode(authorization.removeprefix("Basic ")).decode()
    assert decoded == "client-id:client-secret"
    assert "client_secret" not in body


@pytest.mark.asyncio
async def test_private_key_jwt_sends_a_client_assertion_without_the_secret(monkeypatch):
    captured = _capture(monkeypatch)
    pem = _private_key_pem()

    await _exchange(
        _service(),
        "private_key_jwt",
        private_key=pem,
        private_key_kid="key-1",
    )

    request = captured["request"]
    body = parse_qs(request.content.decode())
    assert body["client_assertion_type"] == [
        "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
    ]
    assert "client_secret" not in body
    assertion = body["client_assertion"][0]
    header = jwt.get_unverified_header(assertion)
    assert header["kid"] == "key-1"
    assert header["alg"] == "RS256"
    # Signed with the configured key, not a placeholder.
    public_pem = (
        serialization.load_pem_private_key(pem.encode(), password=None)
        .public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    claims = jwt.decode(
        assertion, public_pem, algorithms=["RS256"], audience=_TOKEN_URL
    )
    assert claims["iss"] == "client-id"
    assert claims["sub"] == "client-id"
    assert claims["aud"] == _TOKEN_URL
    assert claims["jti"]
    assert isinstance(claims["iat"], int)
    assert claims["exp"] > claims["iat"]


@pytest.mark.asyncio
async def test_private_key_jwt_requires_a_key():
    service = _service()

    with pytest.raises(OAuthError, match="private_key_jwt requires a private key"):
        await _exchange(service, "private_key_jwt")


def _public_key_pem(private_key) -> str:
    return private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()


def test_malformed_private_key_is_rejected():
    with pytest.raises(OAuthConfigValidationError, match="not a valid PEM"):
        _validate_rsa_private_key("not-a-pem", "RS256")


def test_public_only_private_key_is_rejected():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    with pytest.raises(OAuthConfigValidationError, match="not a valid PEM"):
        _validate_rsa_private_key(_public_key_pem(key), "RS256")


def test_password_protected_private_key_is_rejected():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(b"password"),
    ).decode()

    with pytest.raises(OAuthConfigValidationError, match="password-protected"):
        _validate_rsa_private_key(pem, "RS256")


def test_ec_private_key_is_rejected_as_an_alg_mismatch():
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()

    with pytest.raises(OAuthConfigValidationError, match="RSA private keys only"):
        _validate_rsa_private_key(pem, "RS256")
