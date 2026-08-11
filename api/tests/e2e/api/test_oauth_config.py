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
            assert response.json()["login_preference"] == {
                "auto_redirect_to_sso": True,
                "default_sso_provider": "microsoft",
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
            assert response.json()["login_preference"] == {
                "auto_redirect_to_sso": False,
                "default_sso_provider": None,
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
