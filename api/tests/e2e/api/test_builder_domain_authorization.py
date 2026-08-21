from __future__ import annotations

from uuid import uuid4

import pytest


@pytest.fixture
def domain_builder(e2e_client, platform_admin, org1_user, org1):
    platform_headers = {
        **platform_admin.headers,
        "X-Bifrost-Boundary": "platform",
    }
    role_response = e2e_client.post(
        "/api/roles",
        headers=platform_headers,
        json={
            "name": f"Domain Builder {uuid4().hex[:8]}",
            "description": "Exact-boundary domain authorization E2E",
            "capabilities": [
                "claims.readwrite",
                "configs.readwrite",
                "events.readwrite",
                "filepolicies.readwrite",
                "integrations.readwrite",
                "organizations.read",
                "policyrules.readwrite",
            ],
        },
    )
    assert role_response.status_code == 201, role_response.text
    role_id = role_response.json()["id"]
    assignment_response = e2e_client.post(
        f"/api/roles/{role_id}/users",
        headers=platform_headers,
        json={
            "user_ids": [str(org1_user.user_id)],
            "boundaries": [
                {
                    "boundary_kind": "organization",
                    "organization_id": org1["id"],
                },
                {"boundary_kind": "platform"},
            ],
        },
    )
    assert assignment_response.status_code == 204, assignment_response.text
    yield {
        "headers": {
            **org1_user.headers,
            "X-Bifrost-Boundary": f"organization:{org1['id']}",
        },
        "builder_platform_headers": {
            **org1_user.headers,
            "X-Bifrost-Boundary": "platform",
        },
        "platform_headers": platform_headers,
        "role_id": role_id,
    }
    e2e_client.delete(
        f"/api/roles/{role_id}/users/{org1_user.user_id}",
        headers=platform_headers,
    )
    e2e_client.delete(f"/api/roles/{role_id}", headers=platform_headers)


@pytest.mark.e2e
class TestBuilderDomainAuthorization:
    def test_exact_organization_event_source_round_trip(
        self,
        e2e_client,
        domain_builder,
        org1,
    ):
        organization_headers = domain_builder["headers"]
        source_name = f"Domain Event {uuid4().hex[:8]}"
        payload = {
            "name": source_name,
            "source_type": "schedule",
            "organization_id": org1["id"],
            "schedule": {
                "cron_expression": "0 9 * * *",
                "timezone": "America/New_York",
                "enabled": True,
                "overlap_policy": "skip",
            },
        }

        denied_wrong_boundary = e2e_client.post(
            "/api/events/sources",
            headers=domain_builder["builder_platform_headers"],
            json=payload,
        )
        assert denied_wrong_boundary.status_code == 409

        created = e2e_client.post(
            "/api/events/sources",
            headers=organization_headers,
            json=payload,
        )
        assert created.status_code == 201, created.text
        source_id = created.json()["id"]
        try:
            listed = e2e_client.get(
                "/api/events/sources",
                headers=organization_headers,
            )
            assert listed.status_code == 200, listed.text
            assert source_id in {row["id"] for row in listed.json()["items"]}

            fetched = e2e_client.get(
                f"/api/events/sources/{source_id}",
                headers=organization_headers,
            )
            assert fetched.status_code == 200, fetched.text
            assert fetched.json()["organization_id"] == org1["id"]

            updated = e2e_client.patch(
                f"/api/events/sources/{source_id}",
                headers=organization_headers,
                json={"name": f"{source_name} Updated"},
            )
            assert updated.status_code == 200, updated.text
            assert updated.json()["name"] == f"{source_name} Updated"
        finally:
            deleted = e2e_client.delete(
                f"/api/events/sources/{source_id}",
                headers=organization_headers,
            )
            assert deleted.status_code == 204, deleted.text

    def test_exact_organization_file_policy_round_trip(
        self,
        e2e_client,
        domain_builder,
        org1,
    ):
        organization_headers = domain_builder["headers"]
        path = f"domain-policy-{uuid4().hex[:8]}"
        params = {"location": "reports", "scope": org1["id"]}
        policy = {
            "policies": [
                {
                    "name": "domain_builder_access",
                    "actions": ["read", "write", "list"],
                    "when": None,
                }
            ]
        }

        denied_wrong_boundary = e2e_client.put(
            f"/api/files/policies/{path}",
            headers=domain_builder["builder_platform_headers"],
            params=params,
            json={"policies": policy},
        )
        assert denied_wrong_boundary.status_code == 409

        created = e2e_client.put(
            f"/api/files/policies/{path}",
            headers=organization_headers,
            params=params,
            json={"policies": policy},
        )
        assert created.status_code == 200, created.text
        policy_id = created.json()["id"]
        try:
            listed = e2e_client.get(
                "/api/files/policies",
                headers=organization_headers,
                params=params,
            )
            assert listed.status_code == 200, listed.text
            assert policy_id in {
                row["id"] for row in listed.json()["policies"]
            }

            fetched = e2e_client.get(
                f"/api/files/policies/{path}",
                headers=organization_headers,
                params=params,
            )
            assert fetched.status_code == 200, fetched.text
            assert fetched.json()["organization_id"] == org1["id"]

            diagnostic = e2e_client.post(
                "/api/files/policies/test",
                headers=organization_headers,
                json={
                    "path": path,
                    "location": "reports",
                    "scope": org1["id"],
                    "action": "read",
                },
            )
            assert diagnostic.status_code == 200, diagnostic.text
            assert diagnostic.json()["allowed"] is True

            structure = e2e_client.post(
                "/api/files/structure",
                headers=organization_headers,
                json={"scope": org1["id"]},
            )
            assert structure.status_code == 200, structure.text
            assert "reports" in {
                share["location"] for share in structure.json()["shares"]
            }
        finally:
            deleted = e2e_client.delete(
                f"/api/files/policies/{path}",
                headers=organization_headers,
                params=params,
            )
            assert deleted.status_code == 204, deleted.text

    @pytest.mark.asyncio
    async def test_integration_mcp_tools_preserve_builder_target_boundaries(
        self,
        e2e_client,
        e2e_api_url,
        domain_builder,
        monkeypatch,
        org1,
        org1_user,
    ):
        from src.services.mcp_server.server import MCPContext
        from src.services.mcp_server.tools.integrations import (
            bifrost_create_integration,
            bifrost_create_integration_mapping,
            bifrost_update_integration_mapping,
        )

        monkeypatch.setenv("BIFROST_MCP_HTTP_BRIDGE_URL", e2e_api_url)
        platform_context = MCPContext(
            user_id=org1_user.user_id,
            org_id=org1_user.organization_id,
            user_email=org1_user.email,
            user_name=org1_user.name,
            authorization_boundary="platform",
        )
        organization_context = MCPContext(
            user_id=org1_user.user_id,
            org_id=org1_user.organization_id,
            user_email=org1_user.email,
            user_name=org1_user.name,
            authorization_boundary=f"organization:{org1['id']}",
        )
        name = f"MCP Domain Integration {uuid4().hex[:8]}"
        integration_id = None
        try:
            create_result = await bifrost_create_integration(
                platform_context,
                name=name,
            )
            created = create_result.structured_content or {}
            assert "error" not in created, created
            integration_id = str(created["id"])

            mapping_result = await bifrost_create_integration_mapping(
                organization_context,
                integration_ref=name,
                organization=org1["name"],
                entity_id="mcp-tenant",
            )
            mapping = mapping_result.structured_content or {}
            assert "error" not in mapping, mapping

            update_result = await bifrost_update_integration_mapping(
                organization_context,
                integration_ref=name,
                organization=org1["name"],
                entity_name="Updated through MCP",
            )
            updated = update_result.structured_content or {}
            assert "error" not in updated, updated
            assert updated["entity_name"] == "Updated through MCP"
        finally:
            if integration_id is not None:
                deleted = e2e_client.delete(
                    f"/api/integrations/{integration_id}",
                    headers=domain_builder["builder_platform_headers"],
                )
                assert deleted.status_code == 204, deleted.text

    def test_global_integration_definition_and_exact_org_mapping(
        self,
        e2e_client,
        domain_builder,
        org1,
    ):
        organization_headers = domain_builder["headers"]
        platform_headers = domain_builder["builder_platform_headers"]
        name = f"Domain Integration {uuid4().hex[:8]}"

        created = e2e_client.post(
            "/api/integrations",
            headers=platform_headers,
            json={"name": name},
        )
        assert created.status_code == 201, created.text
        integration_id = created.json()["id"]
        mapping_id = None
        try:
            hidden_before_mapping = e2e_client.get(
                "/api/integrations",
                headers=organization_headers,
            )
            assert hidden_before_mapping.status_code == 200
            assert integration_id not in {
                row["id"] for row in hidden_before_mapping.json()["items"]
            }

            denied_org_definition = e2e_client.post(
                "/api/integrations",
                headers=organization_headers,
                json={"name": f"Denied {uuid4().hex[:8]}"},
            )
            assert denied_org_definition.status_code == 409

            denied_platform_mapping = e2e_client.post(
                f"/api/integrations/{integration_id}/mappings",
                headers=platform_headers,
                json={
                    "organization_id": org1["id"],
                    "entity_id": "denied",
                },
            )
            assert denied_platform_mapping.status_code == 409

            mapped = e2e_client.post(
                f"/api/integrations/{integration_id}/mappings",
                headers=organization_headers,
                json={
                    "organization_id": org1["id"],
                    "entity_id": "tenant-one",
                    "entity_name": "Tenant One",
                },
            )
            assert mapped.status_code == 201, mapped.text
            mapping_id = mapped.json()["id"]

            visible = e2e_client.get(
                "/api/integrations",
                headers=organization_headers,
            )
            assert visible.status_code == 200, visible.text
            assert integration_id in {row["id"] for row in visible.json()["items"]}

            detail = e2e_client.get(
                f"/api/integrations/{integration_id}",
                headers=organization_headers,
            )
            assert detail.status_code == 200, detail.text
            assert [row["id"] for row in detail.json()["mappings"]] == [mapping_id]
            assert detail.json()["config_defaults"] is None
            assert detail.json()["oauth_config"] is None

            updated_mapping = e2e_client.put(
                f"/api/integrations/{integration_id}/mappings/{mapping_id}",
                headers=organization_headers,
                json={"entity_name": "Updated Tenant"},
            )
            assert updated_mapping.status_code == 200, updated_mapping.text
            assert updated_mapping.json()["entity_name"] == "Updated Tenant"

            updated_definition = e2e_client.put(
                f"/api/integrations/{integration_id}",
                headers=platform_headers,
                json={"entity_id_name": "Tenant ID"},
            )
            assert updated_definition.status_code == 200, updated_definition.text
            assert updated_definition.json()["entity_id_name"] == "Tenant ID"
        finally:
            deleted = e2e_client.delete(
                f"/api/integrations/{integration_id}",
                headers=platform_headers,
            )
            assert deleted.status_code == 204, deleted.text

    def test_exact_organization_config_and_policy_rule_round_trip(
        self,
        e2e_client,
        domain_builder,
        org1,
    ):
        headers = domain_builder["headers"]
        config_key = f"domain_builder_{uuid4().hex[:8]}"
        config_response = e2e_client.post(
            "/api/config",
            headers=headers,
            json={
                "key": config_key,
                "value": "enabled",
                "type": "string",
                "organization_id": org1["id"],
            },
        )
        assert config_response.status_code == 201, config_response.text
        config_id = config_response.json()["id"]

        rule_name = f"domain_builder_{uuid4().hex[:8]}"
        rule_response = e2e_client.post(
            "/api/policy-rules",
            headers=headers,
            json={
                "name": rule_name,
                "domain": "file",
                "organization_id": org1["id"],
                "body": {"actions": ["read"], "when": None},
            },
        )
        assert rule_response.status_code == 201, rule_response.text

        try:
            listed_configs = e2e_client.get("/api/config", headers=headers)
            assert listed_configs.status_code == 200, listed_configs.text
            assert config_key in {row["key"] for row in listed_configs.json()}

            fetched_rule = e2e_client.get(
                f"/api/policy-rules/file/{rule_name}",
                headers=headers,
                params={"organization_id": org1["id"]},
            )
            assert fetched_rule.status_code == 200, fetched_rule.text
            assert fetched_rule.json()["organization_id"] == org1["id"]

            denied_global = e2e_client.post(
                "/api/config",
                headers=headers,
                json={
                    "key": f"denied_{uuid4().hex[:8]}",
                    "value": "no",
                    "type": "string",
                    "organization_id": None,
                },
            )
            assert denied_global.status_code == 409, denied_global.text
        finally:
            e2e_client.delete(
                f"/api/policy-rules/file/{rule_name}",
                headers=headers,
                params={"organization_id": org1["id"]},
            )
            e2e_client.delete(f"/api/config/{config_id}", headers=headers)

    def test_exact_organization_custom_claim_round_trip(
        self,
        e2e_client,
        domain_builder,
        org1,
    ):
        headers = domain_builder["headers"]
        table_name = f"claim_source_{uuid4().hex[:8]}"
        table_response = e2e_client.post(
            "/api/tables",
            headers={
                **domain_builder["platform_headers"],
                "X-Bifrost-Boundary": f"organization:{org1['id']}",
            },
            json={
                "name": table_name,
                "description": "Builder domain authorization claim source",
                "organization_id": org1["id"],
            },
        )
        assert table_response.status_code == 201, table_response.text
        table_id = table_response.json()["id"]
        claim_name = f"claim_{uuid4().hex[:8]}"
        create_response = e2e_client.post(
            "/api/claims",
            headers=headers,
            json={
                "name": claim_name,
                "type": "list",
                "query": {"table": table_name, "select": "id"},
            },
        )
        assert create_response.status_code == 201, create_response.text
        try:
            get_response = e2e_client.get(
                f"/api/claims/{claim_name}",
                headers=headers,
            )
            assert get_response.status_code == 200, get_response.text
            assert get_response.json()["organization_id"] == org1["id"]
        finally:
            e2e_client.delete(f"/api/claims/{claim_name}", headers=headers)
            e2e_client.delete(
                f"/api/tables/{table_id}",
                headers={
                    **domain_builder["platform_headers"],
                    "X-Bifrost-Boundary": f"organization:{org1['id']}",
                },
            )

    def test_managed_organizations_cannot_be_used_as_a_mutation_identity(
        self,
        e2e_client,
        platform_admin,
        org1,
    ):
        headers = {
            **platform_admin.headers,
            "X-Bifrost-Boundary": "managed_organizations",
        }
        config_response = e2e_client.post(
            "/api/config",
            headers=headers,
            json={
                "key": f"managed_denied_{uuid4().hex[:8]}",
                "value": "no",
                "type": "string",
                "organization_id": org1["id"],
            },
        )
        assert config_response.status_code == 409, config_response.text

        rule_response = e2e_client.post(
            "/api/policy-rules",
            headers=headers,
            json={
                "name": f"managed_denied_{uuid4().hex[:8]}",
                "domain": "file",
                "organization_id": org1["id"],
                "body": {"actions": ["read"], "when": None},
            },
        )
        assert rule_response.status_code == 409, rule_response.text

        claim_response = e2e_client.post(
            "/api/claims",
            headers=headers,
            params={"scope": org1["id"]},
            json={
                "name": f"managed_denied_{uuid4().hex[:8]}",
                "type": "list",
                "query": {"table": "unused", "select": "id"},
            },
        )
        assert claim_response.status_code == 409, claim_response.text
