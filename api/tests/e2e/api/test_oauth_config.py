"""Live API coverage for platform OAuth login preferences."""

from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from src.services import oauth_sso
from src.services.oauth_sso import OAuthService


def _rsa_pem() -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    return (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode()
    )


class _FakeRelyingParty:
    async def aclose(self):
        pass

    async def parse_verified_id_token(self, token, nonce):
        return {"sub": "wire-user"}


@pytest.mark.e2e
class TestOAuthLoginPreference:
    def test_preferred_provider_lifecycle(
        self,
        e2e_client,
        platform_admin,
    ) -> None:
        headers = platform_admin.headers

        try:
            response = e2e_client.put(
                "/api/settings/oauth/microsoft",
                headers=headers,
                json={
                    "client_id": "preferred-login-client",
                    "client_secret": "preferred-login-secret",
                    "tenant_id": "organizations",
                },
            )
            assert response.status_code == 200

            response = e2e_client.put(
                "/api/settings/oauth/login-preference",
                headers=headers,
                json={
                    "auto_redirect_to_sso": True,
                    "default_sso_provider": "microsoft",
                },
            )
            assert response.status_code == 200

            response = e2e_client.get("/api/settings/oauth", headers=headers)
            assert response.status_code == 200
            preference = response.json()["login_preference"]
            excluded_user_ids = preference.pop("excluded_user_ids")
            assert str(platform_admin.user_id) in excluded_user_ids
            assert preference == {
                "auto_redirect_to_sso": True,
                "default_sso_provider": "microsoft",
                "password_login_enabled": True,
                "passkey_login_enabled": True,
                "excluded_organization_ids": [],
            }

            response = e2e_client.get("/auth/status")
            assert response.status_code == 200
            assert response.json()["auto_redirect_to_sso"] is True
            assert response.json()["default_sso_provider"] == "microsoft"

            response = e2e_client.delete(
                "/api/settings/oauth/microsoft",
                headers=headers,
            )
            assert response.status_code == 409
            assert "Disable preferred SSO redirect" in response.json()["detail"]

            response = e2e_client.put(
                "/api/settings/oauth/login-preference",
                headers=headers,
                json={
                    "auto_redirect_to_sso": False,
                    "default_sso_provider": "microsoft",
                },
            )
            assert response.status_code == 200

            response = e2e_client.delete(
                "/api/settings/oauth/microsoft",
                headers=headers,
            )
            assert response.status_code == 204

            response = e2e_client.get("/api/settings/oauth", headers=headers)
            assert response.status_code == 200
            preference = response.json()["login_preference"]
            excluded_user_ids = preference.pop("excluded_user_ids")
            assert str(platform_admin.user_id) in excluded_user_ids
            assert preference == {
                "auto_redirect_to_sso": False,
                "default_sso_provider": None,
                "password_login_enabled": True,
                "passkey_login_enabled": True,
                "excluded_organization_ids": [],
            }
        finally:
            e2e_client.put(
                "/api/settings/oauth/login-preference",
                headers=headers,
                json={
                    "auto_redirect_to_sso": False,
                    "default_sso_provider": None,
                },
            )
            e2e_client.delete(
                "/api/settings/oauth/microsoft",
                headers=headers,
            )

    def test_non_admin_cannot_update_login_preference(
        self,
        e2e_client,
        org1_user,
    ) -> None:
        response = e2e_client.put(
            "/api/settings/oauth/login-preference",
            headers=org1_user.headers,
            json={
                "auto_redirect_to_sso": False,
                "default_sso_provider": None,
            },
        )

        assert response.status_code == 403


@pytest.mark.e2e
class TestOAuthClientAuthentication:
    def test_defaults_to_client_secret_post_and_masks_private_keys(
        self, e2e_client, platform_admin
    ) -> None:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        headers = platform_admin.headers
        try:
            response = e2e_client.put(
                "/api/settings/oauth/google",
                headers=headers,
                json={"client_id": "auth-client", "client_secret": "auth-secret"},
            )
            assert response.status_code == 200
            body = response.json()
            assert body["token_endpoint_auth_method"] == "client_secret_post"
            assert body["private_key_set"] is False

            pem = (
                rsa.generate_private_key(public_exponent=65537, key_size=2048)
                .private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
                .decode()
            )
            response = e2e_client.put(
                "/api/settings/oauth/google",
                headers=headers,
                json={
                    "client_id": "auth-client",
                    "client_secret": "auth-secret",
                    "token_endpoint_auth_method": "private_key_jwt",
                    "private_key": pem,
                    "private_key_kid": "key-1",
                },
            )
            assert response.status_code == 200
            body = response.json()
            assert body["token_endpoint_auth_method"] == "private_key_jwt"
            assert body["private_key_set"] is True
            assert body["private_key_kid"] == "key-1"
            assert "private_key" not in body
            assert "PRIVATE KEY" not in response.text

            response = e2e_client.get("/api/settings/oauth/google", headers=headers)
            assert response.status_code == 200
            body = response.json()
            assert body["token_endpoint_auth_method"] == "private_key_jwt"
            assert body["private_key_set"] is True
            assert "private_key" not in body
            assert "PRIVATE KEY" not in response.text
        finally:
            e2e_client.delete("/api/settings/oauth/google", headers=headers)

    def test_private_key_jwt_without_a_key_is_rejected_at_save(
        self, e2e_client, platform_admin
    ) -> None:
        headers = platform_admin.headers
        e2e_client.delete("/api/settings/oauth/oidc", headers=headers)
        try:
            response = e2e_client.put(
                "/api/settings/oauth/oidc",
                headers=headers,
                json={
                    "discovery_url": "https://idp.example/.well-known/openid-configuration",
                    "client_id": "oidc-client",
                    "client_secret": "oidc-secret",
                    "token_endpoint_auth_method": "private_key_jwt",
                },
            )
            assert response.status_code == 400
            assert "requires a private key" in response.json()["detail"]
        finally:
            e2e_client.delete("/api/settings/oauth/oidc", headers=headers)


@pytest.mark.e2e
@pytest.mark.parametrize(
    "method", ["client_secret_post", "client_secret_basic", "private_key_jwt"]
)
async def test_saved_config_drives_the_token_request(
    e2e_client, platform_admin, db_session, monkeypatch, method
) -> None:
    headers = platform_admin.headers
    payload = {
        "client_id": "wire-client",
        "client_secret": "wire-secret",
        "token_endpoint_auth_method": method,
    }
    if method == "private_key_jwt":
        payload["private_key"] = _rsa_pem()
        payload["private_key_kid"] = "wire-kid"

    captured: dict[str, httpx.Request] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(
            200,
            json={"access_token": "at", "id_token": "idt", "token_type": "Bearer"},
        )

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    try:
        response = e2e_client.put(
            "/api/settings/oauth/google", headers=headers, json=payload
        )
        assert response.status_code == 200, response.text

        monkeypatch.setattr(
            oauth_sso.httpx,
            "AsyncClient",
            lambda *_args, **_kwargs: real_client(transport=transport),
        )
        service = OAuthService(
            db_session, oidc_client_factory=lambda **_kwargs: _FakeRelyingParty()
        )
        await service.exchange_code_for_tokens(
            "google", "wire-code", "https://app.test/callback", "verifier", "nonce-1"
        )
    finally:
        e2e_client.delete("/api/settings/oauth/google", headers=headers)

    request = captured["request"]
    body = parse_qs(request.content.decode())
    assert body["client_id"] == ["wire-client"]
    if method == "client_secret_post":
        assert body["client_secret"] == ["wire-secret"]
        assert "authorization" not in {key.lower() for key in request.headers}
    elif method == "client_secret_basic":
        assert request.headers["authorization"].startswith("Basic ")
        assert "client_secret" not in body
    else:
        assert body["client_assertion_type"] == [
            "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
        ]
        assert "client_assertion" in body
        assert "client_secret" not in body


@pytest.mark.e2e
def test_private_key_lifecycle_keeps_and_revokes_key_predictably(
    e2e_client, platform_admin
) -> None:
    headers = platform_admin.headers
    base = {
        "client_id": "lifecycle-client",
        "client_secret": "lifecycle-secret",
        "token_endpoint_auth_method": "private_key_jwt",
    }
    try:
        response = e2e_client.put(
            "/api/settings/oauth/google",
            headers=headers,
            json={**base, "private_key": _rsa_pem(), "private_key_kid": "kid-1"},
        )
        assert response.status_code == 200

        # Switching to a secret method without a key leaves the key in place.
        response = e2e_client.put(
            "/api/settings/oauth/google",
            headers=headers,
            json={
                "client_id": "lifecycle-client",
                "client_secret": "lifecycle-secret",
                "token_endpoint_auth_method": "client_secret_post",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["private_key_set"] is True
        assert body["private_key_kid"] == "kid-1"

        # Replacing the key clears the kid when it is omitted.
        response = e2e_client.put(
            "/api/settings/oauth/google",
            headers=headers,
            json={**base, "private_key": _rsa_pem()},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["private_key_set"] is True
        assert body["private_key_kid"] is None

        # Explicit revocation without deleting the provider.
        response = e2e_client.put(
            "/api/settings/oauth/google",
            headers=headers,
            json={
                "client_id": "lifecycle-client",
                "client_secret": "lifecycle-secret",
                "token_endpoint_auth_method": "client_secret_post",
                "clear_private_key": True,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["private_key_set"] is False
        assert body["private_key_kid"] is None

        response = e2e_client.put(
            "/api/settings/oauth/google",
            headers=headers,
            json={**base, "private_key": _rsa_pem(), "clear_private_key": True},
        )
        assert response.status_code == 400
        assert "not both" in response.json()["detail"]

        response = e2e_client.put(
            "/api/settings/oauth/google",
            headers=headers,
            json={**base, "private_key": "not-a-pem"},
        )
        assert response.status_code == 400
        assert "not a valid PEM" in response.json()["detail"]
    finally:
        e2e_client.delete("/api/settings/oauth/google", headers=headers)


@pytest.mark.e2e
def test_scopes_default_customize_and_require_openid(e2e_client, platform_admin) -> None:
    headers = platform_admin.headers
    base = {"client_id": "scope-client", "client_secret": "scope-secret"}
    try:
        response = e2e_client.put(
            "/api/settings/oauth/google", headers=headers, json=base
        )
        assert response.status_code == 200
        assert response.json()["scopes"] == ["openid", "email", "profile"]

        response = e2e_client.put(
            "/api/settings/oauth/google",
            headers=headers,
            json={**base, "scopes": ["openid", "email", "groups"]},
        )
        assert response.status_code == 200
        assert response.json()["scopes"] == ["openid", "email", "groups"]

        response = e2e_client.put(
            "/api/settings/oauth/google",
            headers=headers,
            json={**base, "scopes": ["email"]},
        )
        assert response.status_code == 400
        assert "openid" in response.json()["detail"]
    finally:
        e2e_client.delete("/api/settings/oauth/google", headers=headers)


@pytest.mark.e2e
def test_microsoft_scope_validation_preserves_saved_configuration(
    e2e_client, platform_admin
) -> None:
    headers = platform_admin.headers
    base = {"client_id": "scope-client", "client_secret": "scope-secret"}
    try:
        response = e2e_client.put(
            "/api/settings/oauth/microsoft", headers=headers, json=base
        )
        assert response.status_code == 200
        saved = response.json()

        response = e2e_client.put(
            "/api/settings/oauth/microsoft",
            headers=headers,
            json={**base, "client_id": "invalid-update", "scopes": ["openid"]},
        )
        assert response.status_code == 400
        assert "User.Read" in response.json()["detail"]
        response = e2e_client.get("/api/settings/oauth/microsoft", headers=headers)
        assert response.status_code == 200
        assert response.json() == saved
    finally:
        e2e_client.delete("/api/settings/oauth/microsoft", headers=headers)


@pytest.mark.e2e
async def test_saved_scopes_are_used_in_the_authorization_url(
    e2e_client, platform_admin, db_session
) -> None:
    headers = platform_admin.headers
    try:
        response = e2e_client.put(
            "/api/settings/oauth/google",
            headers=headers,
            json={
                "client_id": "scope-url-client",
                "client_secret": "scope-url-secret",
                "scopes": ["openid", "email", "groups"],
            },
        )
        assert response.status_code == 200

        service = OAuthService(db_session)
        url = await service.get_authorization_url(
            "google", "https://app.test/callback", "state-1", "verifier", "nonce-1"
        )
    finally:
        e2e_client.delete("/api/settings/oauth/google", headers=headers)

    query = parse_qs(urlparse(url).query)
    assert query["scope"] == ["openid email groups"]
