"""Live REST/SDK contracts for independent integration connection instances."""

from pathlib import Path
from uuid import uuid4


def test_instance_connection_isolation_and_sdk_selection(
    e2e_client, platform_admin, org1_service_headers, org1
):
    name = f"instances-{uuid4().hex[:8]}"
    created = e2e_client.post(
        "/api/integrations",
        headers=platform_admin.headers,
        json={
            "name": name,
            "config_schema": [
                {"key": "endpoint", "type": "string"},
                {"key": "api_key", "type": "secret"},
            ],
        },
    )
    assert created.status_code == 201, created.text
    definition_id = created.json()["id"]

    try:
        instance = e2e_client.post(
            f"/api/integrations/{definition_id}/instances",
            headers=platform_admin.headers,
            json={"name": "Europe", "key": "eu", "authentication_method": "config"},
        )
        assert instance.status_code == 201, instance.text
        instance_id = instance.json()["id"]

        assert e2e_client.put(
            f"/api/integrations/{definition_id}/config",
            headers=platform_admin.headers,
            json={"config": {"endpoint": "https://default.example", "api_key": "default-secret"}},
        ).status_code == 200
        assert e2e_client.put(
            f"/api/integrations/{instance_id}/config",
            headers=platform_admin.headers,
            json={"config": {"endpoint": "https://eu.example", "api_key": "eu-secret"}},
        ).status_code == 200
        mapping = e2e_client.post(
            f"/api/integrations/{instance_id}/mappings",
            headers=platform_admin.headers,
            json={"organization_id": org1["id"], "entity_id": "eu-tenant"},
        )
        assert mapping.status_code == 201, mapping.text

        detail = e2e_client.get(
            f"/api/integrations/{instance_id}", headers=platform_admin.headers
        )
        assert detail.status_code == 200, detail.text
        payload = detail.json()
        assert payload["definition_id"] == definition_id
        assert payload["instance_key"] == "eu"
        assert {item["key"] for item in payload["config_schema"]} == {"endpoint", "api_key"}

        # Logos belong to the shared definition even through a child UUID.
        logo = Path(__file__).parents[1] / "logos" / "square.png"
        uploaded = e2e_client.post(
            f"/api/integrations/{instance_id}/logo",
            headers={key: value for key, value in platform_admin.headers.items()
                     if key.lower() != "content-type"},
            files={"file": ("logo.png", logo.read_bytes(), "image/png")},
        )
        assert uploaded.status_code == 200, uploaded.text
        root_logo = e2e_client.get(
            f"/api/integrations/{definition_id}/logo", headers=platform_admin.headers
        )
        child_logo = e2e_client.get(
            f"/api/integrations/{instance_id}/logo", headers=platform_admin.headers
        )
        assert root_logo.status_code == child_logo.status_code == 200
        assert root_logo.content == child_logo.content
        assert e2e_client.delete(
            f"/api/integrations/{instance_id}/logo", headers=platform_admin.headers
        ).status_code == 204
        assert e2e_client.get(
            f"/api/integrations/{definition_id}/logo", headers=platform_admin.headers
        ).status_code == 404

        selected = e2e_client.post(
            "/api/sdk/integrations/instances/get",
            headers=org1_service_headers,
            json={"name": name, "instance": "eu"},
        )
        assert selected.status_code == 200, selected.text
        selected_payload = selected.json()
        assert selected_payload["integration_id"] == instance_id
        assert selected_payload["definition_id"] == definition_id
        assert selected_payload["instance_key"] == "eu"
        assert selected_payload["entity_id"] == "eu-tenant"
        assert selected_payload["config"]["endpoint"] == "https://eu.example"
        assert selected_payload["config"]["api_key"] == "eu-secret"

        missing = e2e_client.post(
            "/api/sdk/integrations/instances/get",
            headers=org1_service_headers,
            json={"name": name, "instance": "missing"},
        )
        assert missing.status_code == 404
        fallback = e2e_client.post(
            "/api/sdk/integrations/instances/get",
            headers=org1_service_headers,
            json={"name": name, "instance": "missing", "on_missing": "default"},
        )
        assert fallback.status_code == 200, fallback.text
        assert fallback.json()["instance_key"] == "default"
        assert fallback.json()["config"]["endpoint"] == "https://default.example"

        assert e2e_client.delete(
            f"/api/integrations/{definition_id}/instances/{definition_id}",
            headers=platform_admin.headers,
        ).status_code == 409
        assert e2e_client.delete(
            f"/api/integrations/{definition_id}/instances/{instance_id}",
            headers=platform_admin.headers,
        ).status_code == 204
    finally:
        e2e_client.delete(f"/api/integrations/{definition_id}", headers=platform_admin.headers)


def test_oauth_provider_belongs_only_to_its_instance(e2e_client, platform_admin):
    definition = e2e_client.post(
        "/api/integrations", headers=platform_admin.headers,
        json={"name": f"instance-oauth-{uuid4().hex[:8]}"},
    )
    assert definition.status_code == 201, definition.text
    definition_id = definition.json()["id"]
    try:
        child = e2e_client.post(
            f"/api/integrations/{definition_id}/instances", headers=platform_admin.headers,
            json={"name": "Tenant A", "key": "tenant-a", "authentication_method": "oauth"},
        )
        assert child.status_code == 201, child.text
        child_id = child.json()["id"]
        oauth = e2e_client.post(
            "/api/oauth/connections", headers=platform_admin.headers,
            json={
                "integration_id": child_id, "oauth_flow_type": "client_credentials",
                "client_id": "client-a", "client_secret": "secret-a",
                "token_url": "https://login.example.test/token",
            },
        )
        assert oauth.status_code == 201, oauth.text
        assert oauth.json()["integration_id"] == child_id
        root_detail = e2e_client.get(
            f"/api/integrations/{definition_id}", headers=platform_admin.headers
        ).json()
        child_detail = e2e_client.get(
            f"/api/integrations/{child_id}", headers=platform_admin.headers
        ).json()
        assert root_detail["has_oauth_config"] is False
        assert child_detail["has_oauth_config"] is True
        assert child_detail["authentication_method"] == "oauth"
    finally:
        e2e_client.delete(f"/api/integrations/{definition_id}", headers=platform_admin.headers)
