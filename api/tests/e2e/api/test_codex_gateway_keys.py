"""E2E coverage for Codex Gateway downstream key lifecycle."""

import pytest


@pytest.mark.e2e
class TestCodexGatewayKeys:
    def test_requires_authenticated_user(self, e2e_client):
        response = e2e_client.get("/api/codex-gateway/keys")

        assert response.status_code == 401

    def test_create_list_and_revoke_gateway_key_for_same_user(
        self,
        e2e_client,
        org1_user,
        platform_admin,
    ):
        create_response = e2e_client.post(
            "/api/codex-gateway/keys",
            headers=org1_user.headers,
            json={
                "name": "e2e developer workstation",
                "allowed_models": ["gpt-5.1-codex"],
                "daily_limit": 25,
            },
        )
        assert create_response.status_code == 201, create_response.text
        created = create_response.json()
        key_id = created["record"]["id"]
        plaintext_key = created["key"]

        assert plaintext_key.startswith("bfck_")
        assert created["record"]["name"] == "e2e developer workstation"
        assert created["record"]["status"] == "active"
        assert "key_hash" not in created["record"]

        list_response = e2e_client.get(
            "/api/codex-gateway/keys",
            headers=org1_user.headers,
        )
        assert list_response.status_code == 200, list_response.text
        listed = list_response.json()["items"]
        matching = [item for item in listed if item["id"] == key_id]
        assert matching
        assert "key" not in matching[0]
        assert "key_hash" not in matching[0]

        audit_response = e2e_client.get(
            "/api/audit?action=codex_gateway.key.",
            headers=platform_admin.headers,
        )
        assert audit_response.status_code == 200, audit_response.text
        create_events = [
            event
            for event in audit_response.json()["entries"]
            if event["action"] == "codex_gateway.key.create"
            and event.get("resource_id") == key_id
        ]
        assert create_events

        revoke_response = e2e_client.delete(
            f"/api/codex-gateway/keys/{key_id}",
            headers=org1_user.headers,
        )
        assert revoke_response.status_code == 200, revoke_response.text
        assert revoke_response.json()["status"] == "revoked"
        assert "key" not in revoke_response.json()
        assert "key_hash" not in revoke_response.json()

    def test_gateway_keys_are_user_scoped(self, e2e_client, org1_user, org2_user):
        create_response = e2e_client.post(
            "/api/codex-gateway/keys",
            headers=org1_user.headers,
            json={"name": "e2e user scoped key"},
        )
        assert create_response.status_code == 201, create_response.text
        key_id = create_response.json()["record"]["id"]

        list_response = e2e_client.get(
            "/api/codex-gateway/keys",
            headers=org2_user.headers,
        )
        assert list_response.status_code == 200, list_response.text
        assert all(item["id"] != key_id for item in list_response.json()["items"])

        revoke_response = e2e_client.delete(
            f"/api/codex-gateway/keys/{key_id}",
            headers=org2_user.headers,
        )
        assert revoke_response.status_code == 404

        cleanup_response = e2e_client.delete(
            f"/api/codex-gateway/keys/{key_id}",
            headers=org1_user.headers,
        )
        assert cleanup_response.status_code == 200, cleanup_response.text
