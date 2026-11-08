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
    _validate_private_key,
    _validate_scopes,
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
        _validate_private_key("not-a-pem", "RS256")


def test_public_only_private_key_is_rejected():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    with pytest.raises(OAuthConfigValidationError, match="not a valid PEM"):
        _validate_private_key(_public_key_pem(key), "RS256")


def test_password_protected_private_key_is_rejected():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(b"password"),
    ).decode()

    with pytest.raises(OAuthConfigValidationError, match="password-protected"):
        _validate_private_key(pem, "RS256")


def test_alg_and_key_type_must_match():
    from cryptography.hazmat.primitives.asymmetric import ec

    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    rsa_pem = rsa_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    ec_key = ec.generate_private_key(ec.SECP256R1())
    ec_pem = ec_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()

    _validate_private_key(rsa_pem, "RS256")
    _validate_private_key(ec_pem, "ES256")

    with pytest.raises(OAuthConfigValidationError, match="requires an EC private key"):
        _validate_private_key(rsa_pem, "ES256")
    with pytest.raises(OAuthConfigValidationError, match="requires an RSA private key"):
        _validate_private_key(ec_pem, "RS256")
    with pytest.raises(OAuthConfigValidationError, match="secp384r1"):
        _validate_private_key(ec_pem, "ES384")


def test_scopes_keep_defaults_dedupe_and_require_openid():
    assert _validate_scopes("google", None) == ["openid", "email", "profile"]
    assert _validate_scopes(
        "google", ["openid", "email", "email", "groups"]
    ) == ["openid", "email", "groups"]
    with pytest.raises(OAuthConfigValidationError, match="openid"):
        _validate_scopes("google", ["email"])
    with pytest.raises(OAuthConfigValidationError, match="empty"):
        _validate_scopes("google", ["openid", ""])
    with pytest.raises(OAuthConfigValidationError, match="scope tokens"):
        _validate_scopes("google", ["openid", "email profile"])


def test_microsoft_scopes_require_graph_profile_access():
    with pytest.raises(OAuthConfigValidationError, match="User.Read"):
        _validate_scopes("microsoft", ["openid", "email", "profile"])


@pytest.mark.parametrize("scope", ["User.Read", "https://graph.microsoft.com/User.Read"])
def test_microsoft_custom_scopes_keep_graph_profile_access(scope):
    assert _validate_scopes("microsoft", ["openid", scope]) == ["openid", scope]


def test_microsoft_default_scopes_keep_graph_profile_access():
    assert _validate_scopes("microsoft", None) == [
        "openid", "email", "profile", "User.Read",
    ]


def _ec_pem(curve) -> str:
    from cryptography.hazmat.primitives.asymmetric import ec

    return (
        ec.generate_private_key(curve)
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("alg", "curve_name"),
    [("ES256", "SECP256R1"), ("ES384", "SECP384R1"), ("ES512", "SECP521R1")],
)
async def test_private_key_jwt_ec_assertion_is_signed_for_each_curve(
    monkeypatch, alg, curve_name
):
    from cryptography.hazmat.primitives.asymmetric import ec

    captured = _capture(monkeypatch)
    pem = _ec_pem(getattr(ec, curve_name)())

    await _exchange(
        _service(),
        "private_key_jwt",
        private_key=pem,
        private_key_kid="ec-key",
        private_key_alg=alg,
    )

    request = captured["request"]
    body = parse_qs(request.content.decode())
    assertion = body["client_assertion"][0]
    header = jwt.get_unverified_header(assertion)
    assert header["alg"] == alg
    assert header["kid"] == "ec-key"
    public_pem = (
        serialization.load_pem_private_key(pem.encode(), password=None)
        .public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    claims = jwt.decode(
        assertion, public_pem, algorithms=[alg], audience=_TOKEN_URL
    )
    assert claims["iss"] == "client-id"
    assert claims["sub"] == "client-id"
