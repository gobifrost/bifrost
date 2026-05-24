"""E2E coverage for Codex Gateway upstream OAuth onboarding."""

import pytest


@pytest.mark.e2e
class TestCodexGatewayOAuth:
    def test_requires_authenticated_user(self, e2e_client):
        response = e2e_client.get("/api/codex-gateway/oauth/status")

        assert response.status_code == 401

    def test_connect_status_import_and_disconnect_flow(
        self,
        e2e_client,
        org1_user,
        platform_admin,
    ):
        connect_response = e2e_client.post(
            "/api/codex-gateway/oauth/connect",
            headers=org1_user.headers,
        )
        assert connect_response.status_code == 200, connect_response.text
        assert connect_response.json()["preferred_method"] == "device_code"
        assert connect_response.json()["client_command"] == "codex login --device-auth"

        disconnected_response = e2e_client.get(
            "/api/codex-gateway/oauth/status",
            headers=org1_user.headers,
        )
        assert disconnected_response.status_code == 200, disconnected_response.text
        assert disconnected_response.json()["connected"] is False

        account_id = None
        try:
            import_response = e2e_client.post(
                "/api/codex-gateway/oauth/import-auth-cache",
                headers=org1_user.headers,
                json={
                    "auth_cache": {
                        "tokens": {
                            "access_token": "e2e-access-token-secret",
                            "refresh_token": "e2e-refresh-token-secret",
                            "scope": "openid profile offline_access",
                        },
                        "account": {
                            "sub": "e2e-chatgpt-user",
                            "email": "e2e-codex@example.test",
                            "workspace_id": "workspace-midtown",
                        },
                    }
                },
            )
            assert import_response.status_code == 200, import_response.text
            imported_text = import_response.text
            imported = import_response.json()
            account_id = imported["account"]["id"]
            assert imported["connected"] is True
            assert imported["account"]["upstream_subject"] == "e2e-chatgpt-user"
            assert imported["account"]["upstream_email"] == "e2e-codex@example.test"
            assert "e2e-access-token-secret" not in imported_text
            assert "e2e-refresh-token-secret" not in imported_text

            connected_response = e2e_client.get(
                "/api/codex-gateway/oauth/status",
                headers=org1_user.headers,
            )
            assert connected_response.status_code == 200, connected_response.text
            assert connected_response.json()["connected"] is True
            assert connected_response.json()["account"]["id"] == account_id
            assert "e2e-access-token-secret" not in connected_response.text
            assert "e2e-refresh-token-secret" not in connected_response.text
            assert "refresh_token" not in connected_response.text

            audit_response = e2e_client.get(
                "/api/audit?action=codex_gateway.oauth.",
                headers=platform_admin.headers,
            )
            assert audit_response.status_code == 200, audit_response.text
            import_events = [
                event
                for event in audit_response.json()["entries"]
                if event["action"] == "codex_gateway.oauth.import"
                and event.get("resource_id") == account_id
            ]
            assert import_events
        finally:
            disconnect_response = e2e_client.delete(
                "/api/codex-gateway/oauth",
                headers=org1_user.headers,
            )
            assert disconnect_response.status_code == 200, disconnect_response.text
            if account_id is not None:
                assert disconnect_response.json() == {"connected": False, "revoked": True}

    def test_oauth_connections_are_user_scoped(self, e2e_client, org1_user, org2_user):
        account_id = None
        try:
            import_response = e2e_client.post(
                "/api/codex-gateway/oauth/import-auth-cache",
                headers=org1_user.headers,
                json={
                    "auth_cache": {
                        "tokens": {"access_token": "e2e-scoped-access-token"},
                        "account": {"sub": "e2e-scoped-chatgpt-user"},
                    }
                },
            )
            assert import_response.status_code == 200, import_response.text
            account_id = import_response.json()["account"]["id"]

            other_status = e2e_client.get(
                "/api/codex-gateway/oauth/status",
                headers=org2_user.headers,
            )
            assert other_status.status_code == 200, other_status.text
            assert other_status.json()["account"] is None
            assert account_id
        finally:
            cleanup_response = e2e_client.delete(
                "/api/codex-gateway/oauth",
                headers=org1_user.headers,
            )
            assert cleanup_response.status_code == 200, cleanup_response.text
            if account_id is not None:
                assert cleanup_response.json()["revoked"] is True

        final_status = e2e_client.get(
            "/api/codex-gateway/oauth/status",
            headers=org1_user.headers,
        )
        assert final_status.status_code == 200, final_status.text
        assert final_status.json()["account"] is None
