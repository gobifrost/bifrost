"""Live API coverage for platform OAuth login preferences."""

import pytest


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
