"""End-to-end coverage for the four org-scoping scenarios.

These tests cover the persisted role and organization-reach model against
real endpoints, rather than mocked resolvers.

Scenarios — each is verified against a different real user via real
endpoints (config, tables, mapping):

    (1) Orgs can get to their own stuff.
    (2a) Platform admin (is_superuser=True) can get to everybody.
    (2b) Provider-org membership alone grants neither foreign reach nor
         named permissions.
    (3) Resolution returns org-then-global cascade.
    (4) Regular orgs can't get to other orgs.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.core.constants import PROVIDER_ORG_ID


# =============================================================================
# Helpers
# =============================================================================


def _seed_config(e2e_client, headers, *, key: str, value: str, org_id: str | None):
    """Create or replace a config value in the target scope."""
    body: dict = {
        "key": key,
        "value": value,
        "type": "string",
    }
    if org_id is not None:
        body["organization_id"] = org_id
    else:
        body["organization_id"] = None
    r = e2e_client.post(
        "/api/config",
        headers=headers,
        json=body,
    )
    assert r.status_code in (200, 201, 409), f"seed_config: {r.status_code} {r.text}"


def _get_config_via_sdk(e2e_client, headers, *, key: str, scope: str | None):
    """Read config via the SDK endpoint (the one the C2 gate lives on)."""
    body: dict = {"key": key}
    if scope is not None:
        body["scope"] = scope
    return e2e_client.post(
        "/api/sdk/config/get",
        headers=headers,
        json=body,
    )


# =============================================================================
# Scenario 1: Orgs can get to their own stuff
# =============================================================================


@pytest.mark.e2e
class TestScenario0_SdkRequiresExecutionCredentials:
    """A regular user's own session token cannot use the SDK secret routes."""

    def test_regular_user_token_is_refused(self, e2e_client, org1_user):
        r = _get_config_via_sdk(e2e_client, org1_user.headers, key="anything", scope=None)
        assert r.status_code == 403, r.text


@pytest.mark.e2e
class TestScenario1_OrgsReachOwnData:
    """An org-scoped execution credential reads its own org's data via the
    SDK, and the resolver returns its org_id on UNSET scope."""

    def test_org_user_sdk_get_with_unset_scope_resolves_to_own_org(
        self, e2e_client, platform_admin, org1, org1_service_headers
    ):
        key = f"own_{uuid4().hex[:8]}"
        _seed_config(
            e2e_client,
            platform_admin.headers,
            key=key,
            value="org1_value",
            org_id=org1["id"],
        )

        r = _get_config_via_sdk(e2e_client, org1_service_headers, key=key, scope=None)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body is not None, "Unset scope should resolve to the caller's own org"
        assert body["value"] == "org1_value"

    def test_org_user_sdk_get_with_explicit_own_org_succeeds(
        self, e2e_client, platform_admin, org1, org1_service_headers
    ):
        key = f"explicit_own_{uuid4().hex[:8]}"
        _seed_config(
            e2e_client,
            platform_admin.headers,
            key=key,
            value="org1_explicit",
            org_id=org1["id"],
        )

        r = _get_config_via_sdk(
            e2e_client, org1_service_headers, key=key, scope=org1["id"]
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body is not None
        assert body["value"] == "org1_explicit"


# =============================================================================
# Scenario 2a: Platform admin can reach any org
# =============================================================================


@pytest.mark.e2e
class TestScenario2a_PlatformAdminBypass:
    """Superuser bypass: cross-org reads via explicit scope are allowed."""

    def test_platform_admin_reads_org1(
        self, e2e_client, platform_admin, org1
    ):
        key = f"admin_{uuid4().hex[:8]}"
        _seed_config(
            e2e_client,
            platform_admin.headers,
            key=key,
            value="org1_admin_view",
            org_id=org1["id"],
        )
        r = _get_config_via_sdk(
            e2e_client, platform_admin.headers, key=key, scope=org1["id"]
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body is not None and body["value"] == "org1_admin_view"

    def test_platform_admin_reads_global(
        self, e2e_client, platform_admin
    ):
        key = f"admin_global_{uuid4().hex[:8]}"
        _seed_config(
            e2e_client, platform_admin.headers, key=key, value="g", org_id=None
        )
        r = _get_config_via_sdk(
            e2e_client, platform_admin.headers, key=key, scope="global"
        )
        assert r.status_code == 200, r.text
        assert r.json() is not None

    def test_platform_admin_lists_every_mapping_whatever_the_scope(
        self, e2e_client, platform_admin, org1, org2
    ):
        integration_name = f"admin_list_mappings_{uuid4().hex[:8]}"
        integration_resp = e2e_client.post(
            "/api/integrations",
            headers=platform_admin.headers,
            json={"name": integration_name},
        )
        assert integration_resp.status_code == 201, integration_resp.text
        integration = integration_resp.json()

        try:
            for org_id, entity_id in (
                (str(PROVIDER_ORG_ID), "provider-tenant"),
                (org1["id"], "org1-tenant"),
                (org2["id"], "org2-tenant"),
            ):
                mapping_resp = e2e_client.post(
                    f"/api/integrations/{integration['id']}/mappings",
                    headers=platform_admin.headers,
                    json={"organization_id": org_id, "entity_id": entity_id},
                )
                assert mapping_resp.status_code == 201, mapping_resp.text

            def listed(scope: str | None) -> set[str]:
                r = e2e_client.post(
                    "/api/sdk/integrations/list_mappings",
                    headers=platform_admin.headers,
                    json={"name": integration_name, "scope": scope},
                )
                assert r.status_code == 200, r.text
                return {item["entity_id"] for item in r.json()["items"]}

            every = {"provider-tenant", "org1-tenant", "org2-tenant"}
            for scope in (None, "global", str(PROVIDER_ORG_ID), org1["id"]):
                assert listed(scope) == every, scope
        finally:
            e2e_client.delete(
                f"/api/integrations/{integration['id']}",
                headers=platform_admin.headers,
            )


# =============================================================================
# Scenario 2b: Provider-org membership does not grant foreign authority
# =============================================================================


@pytest.mark.e2e
class TestScenario2b_ProviderOrgRequiresExplicitGrants:
    """A provider-org member has only its persisted roles and reach."""

    def test_provider_member_foreign_scope_is_refused(
        self, e2e_client, platform_admin, provider_org_user, org1
    ):
        key = f"prov_to_org1_{uuid4().hex[:8]}"
        _seed_config(
            e2e_client,
            platform_admin.headers,
            key=key,
            value="org1_private_value",
            org_id=org1["id"],
        )
        r = _get_config_via_sdk(
            e2e_client, provider_org_user.headers, key=key, scope=org1["id"]
        )
        assert r.status_code == 403, r.text

    def test_provider_member_global_scope_is_refused_without_config_read(
        self, e2e_client, platform_admin, provider_org_user
    ):
        key = f"prov_to_global_{uuid4().hex[:8]}"
        _seed_config(
            e2e_client, platform_admin.headers, key=key, value="g", org_id=None
        )
        r = _get_config_via_sdk(
            e2e_client, provider_org_user.headers, key=key, scope="global"
        )
        assert r.status_code == 403, r.text

    def test_provider_member_cannot_list_mappings_without_integration_read(
        self,
        e2e_client,
        platform_admin,
        provider_org_user,
        org1,
        org1_service_headers,
        org2,
    ):
        integration_name = f"prov_list_mappings_{uuid4().hex[:8]}"
        integration_resp = e2e_client.post(
            "/api/integrations",
            headers=platform_admin.headers,
            json={
                "name": integration_name,
                "config_schema": [
                    {"key": "endpoint", "type": "string", "required": True, "description": ""}
                ],
            },
        )
        assert integration_resp.status_code == 201, integration_resp.text
        integration = integration_resp.json()

        try:
            for org_id, entity_id in (
                (str(PROVIDER_ORG_ID), "provider-tenant"),
                (org1["id"], "org1-tenant"),
                (org2["id"], "org2-tenant"),
            ):
                mapping_resp = e2e_client.post(
                    f"/api/integrations/{integration['id']}/mappings",
                    headers=platform_admin.headers,
                    json={
                        "organization_id": org_id,
                        "entity_id": entity_id,
                        "entity_name": entity_id,
                    },
                )
                assert mapping_resp.status_code == 201, mapping_resp.text

            provider_resp = e2e_client.post(
                "/api/sdk/integrations/list_mappings",
                headers=provider_org_user.headers,
                json={"name": integration_name},
            )
            assert provider_resp.status_code == 403, provider_resp.text

            org1_resp = e2e_client.post(
                "/api/sdk/integrations/list_mappings",
                headers=org1_service_headers,
                json={"name": integration_name},
            )
            assert org1_resp.status_code == 200, org1_resp.text
            org1_items = org1_resp.json()["items"]
            assert [item["entity_id"] for item in org1_items] == ["org1-tenant"]
        finally:
            e2e_client.delete(
                f"/api/integrations/{integration['id']}",
                headers=platform_admin.headers,
            )

    def test_full_workflow_permission_does_not_extend_provider_member_reach(
        self, e2e_client, platform_admin, provider_org_user, org1
    ):
        """Full workflow permissions still retain the original user's reach."""
        # Seed an org1-scoped config the provider-org workflow cannot read.
        key = f"prov_engine_{uuid4().hex[:8]}"
        _seed_config(
            e2e_client,
            platform_admin.headers,
            key=key,
            value="org1_private_value",
            org_id=org1["id"],
        )

        # The workflow catches the refused SDK read so the execution itself
        # stays successful and exposes the authorization outcome as data.
        workflow_name = f"e2e_prov_read_{uuid4().hex[:8]}"
        workflow_path = f"{workflow_name}.py"
        workflow_content = (
            '"""E2E: provider member has no implicit foreign config reach."""\n'
            "from bifrost import workflow, config\n"
            "\n"
            "@workflow(\n"
            f'    name="{workflow_name}",\n'
            '    description="Catches a refused foreign config read.",\n'
            ")\n"
            f"async def {workflow_name}():\n"
            "    try:\n"
            f'        value = await config.get("{key}", scope="{org1["id"]}")\n'
            '        return {"raised": False, "value": value}\n'
            "    except Exception as e:\n"
            '        return {"raised": True, "error_type": type(e).__name__, "error": str(e)}\n'
        )

        from tests.e2e.conftest import execute_workflow_sync, write_and_register

        workflow_id: str | None = None
        try:
            # Register as platform admin, then place the workflow in the
            # provider org so the provider user may execute its own workflow.
            registered = write_and_register(
                e2e_client,
                platform_admin.headers,
                workflow_path,
                workflow_content,
                workflow_name,
            )
            workflow_id = registered["id"]
            patch_resp = e2e_client.patch(
                f"/api/workflows/{workflow_id}",
                headers=platform_admin.headers,
                json={
                    "organization_id": str(PROVIDER_ORG_ID),
                    "access_level": "authenticated",
                },
            )
            assert patch_resp.status_code == 200, patch_resp.text

            result = execute_workflow_sync(
                e2e_client,
                provider_org_user.headers,
                workflow_id,
            )
            assert result["status"] == "Success", result
            payload = result.get("result", {})
            assert payload.get("raised") is True, payload
            assert payload.get("value") is None, payload
        finally:
            if workflow_id is not None:
                e2e_client.delete(
                    f"/api/workflows/{workflow_id}", headers=platform_admin.headers
                )
            e2e_client.delete(
                "/api/files/editor",
                headers=platform_admin.headers,
                params={"path": workflow_path},
            )


# =============================================================================
# Scenario 3: Org-then-global cascade resolution
# =============================================================================


@pytest.mark.e2e
class TestScenario3_OrgThenGlobalCascade:
    """When a config key exists at both global and org scope, an org
    caller's UNSET request resolves to the ORG row (override). When only
    the global row exists, the org caller falls back to it. The cascade
    primitive lives in ``OrgScopedRepository`` and is the single source
    of truth for this behavior."""

    def test_org_value_overrides_global_on_unset(
        self, e2e_client, platform_admin, org1, org1_service_headers
    ):
        # Same key at global and org scope — org wins.
        key = f"cascade_{uuid4().hex[:8]}"
        _seed_config(
            e2e_client, platform_admin.headers, key=key, value="GLOBAL", org_id=None
        )
        _seed_config(
            e2e_client,
            platform_admin.headers,
            key=key,
            value="ORG1",
            org_id=org1["id"],
        )

        r = _get_config_via_sdk(e2e_client, org1_service_headers, key=key, scope=None)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body is not None
        assert body["value"] == "ORG1", (
            "Cascade should prefer org-scoped value over global when both exist."
        )

    def test_falls_back_to_global_when_org_value_missing(
        self, e2e_client, platform_admin, org1_service_headers
    ):
        # Only a global row exists; org caller should see it via cascade.
        key = f"fallback_{uuid4().hex[:8]}"
        _seed_config(
            e2e_client,
            platform_admin.headers,
            key=key,
            value="GLOBAL_FALLBACK",
            org_id=None,
        )

        r = _get_config_via_sdk(e2e_client, org1_service_headers, key=key, scope=None)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body is not None
        assert body["value"] == "GLOBAL_FALLBACK", (
            "When no org-scoped row exists, cascade should fall back to global."
        )


# =============================================================================
# Scenario 4: Regular orgs cannot reach other orgs
# =============================================================================


@pytest.mark.e2e
class TestScenario4_CrossOrgBlocked:
    """A regular org user (not platform admin, not provider-org member)
    cannot read or mutate another organization's data. Internal identities
    can still read shared Global configuration within their normal reach."""

    def test_org_user_explicit_other_org_returns_403(
        self, e2e_client, platform_admin, org2, org1_service_headers
    ):
        key = f"other_org_{uuid4().hex[:8]}"
        _seed_config(
            e2e_client,
            platform_admin.headers,
            key=key,
            value="org2_secret",
            org_id=org2["id"],
        )
        r = _get_config_via_sdk(
            e2e_client, org1_service_headers, key=key, scope=org2["id"]
        )
        # Resolver raises 403; the endpoint may also legitimately return
        # 200 with null body if the implementation chooses to swallow.
        # 403 is what we expect; we assert it explicitly so a silent
        # downgrade to 200+null can't slip through.
        assert r.status_code == 403, (
            f"Cross-org explicit scope must be 403, not {r.status_code}: {r.text}"
        )

    def test_org_service_reads_shared_global_config(
        self, e2e_client, platform_admin, org1_service_headers
    ):
        key = f"shared_global_{uuid4().hex[:8]}"
        _seed_config(
            e2e_client,
            platform_admin.headers,
            key=key,
            value="shared-global-value",
            org_id=None,
        )
        r = _get_config_via_sdk(
            e2e_client, org1_service_headers, key=key, scope="global"
        )
        assert r.status_code == 200, r.text
        assert r.json()["value"] == "shared-global-value"

    def test_org_user_unset_does_not_leak_other_org(
        self, e2e_client, platform_admin, org2, org1_service_headers
    ):
        """The forgery scenario the overhaul exists to close. Pre-fix a
        user could set DeveloperContext.default_org_id to another org and
        UNSET would return that org's data. Post-fix UNSET sources from
        ``current_user.organization_id`` (auth-verified), so this can't
        happen.
        """
        key = f"leak_{uuid4().hex[:8]}"
        _seed_config(
            e2e_client,
            platform_admin.headers,
            key=key,
            value="org2_should_be_invisible",
            org_id=org2["id"],
        )
        r = _get_config_via_sdk(e2e_client, org1_service_headers, key=key, scope=None)
        # Either 200 with null body (org1 has no such key, no global
        # fallback either) or 404. NOT 200 with the org2 value.
        if r.status_code == 200:
            body = r.json()
            assert body is None, (
                f"Org1 UNSET must not surface org2 data; got {body!r}"
            )
        else:
            assert r.status_code in (404,), (
                f"Expected 200+null or 404; got {r.status_code}: {r.text}"
            )

    def test_workflow_via_engine_cannot_mutate_other_org_mapping(
        self,
        e2e_client,
        platform_admin,
        org1,
        org2,
        org1_user,
    ):
        """The runtime evaluator denies a foreign mapping write over the socket.

        A real org1 workflow tries to change an org2 mapping. The SDK exposes
        the HTTP denial, and the mapping keeps its original value.
        """
        integration_name = f"engine_isolation_{uuid4().hex[:8]}"
        integration_resp = e2e_client.post(
            "/api/integrations",
            headers=platform_admin.headers,
            json={
                "name": integration_name,
                "config_schema": [
                    {"key": "endpoint", "type": "string", "required": True, "description": ""}
                ],
            },
        )
        assert integration_resp.status_code == 201, integration_resp.text
        integration = integration_resp.json()

        try:
            # Seed an org2 mapping that the workflow will try to clobber.
            mapping_resp = e2e_client.post(
                f"/api/integrations/{integration['id']}/mappings",
                headers=platform_admin.headers,
                json={
                    "organization_id": org2["id"],
                    "entity_id": "org2-original",
                    "entity_name": "Original",
                },
            )
            assert mapping_resp.status_code == 201, mapping_resp.text

            # Define an org1-scoped workflow that attempts cross-org mutation.
            workflow_name = f"e2e_engine_iso_{uuid4().hex[:8]}"
            workflow_path = f"{workflow_name}.py"
            workflow_content = (
                '"""E2E workflow runtime isolation regression."""\n'
                'from bifrost import workflow, integrations\n'
                '\n'
                '@workflow(\n'
                f'    name="{workflow_name}",\n'
                '    description="Attempts to clobber another org\'s mapping.",\n'
                ')\n'
                f'async def {workflow_name}():\n'
                '    try:\n'
                '        await integrations.upsert_mapping(\n'
                f'            "{integration_name}",\n'
                f'            scope="{org2["id"]}",\n'
                '            entity_id="forged-by-org1",\n'
                '            entity_name="Forged",\n'
                '        )\n'
                '        return {"raised": False, "error": None}\n'
                '    except Exception as e:\n'
                '        return {"raised": True, "error_type": type(e).__name__, "error": str(e)}\n'
            )
            # Write + register the workflow as a platform admin, then patch
            # its org so org1_user can execute it.
            from tests.e2e.conftest import write_and_register
            registered = write_and_register(
                e2e_client,
                platform_admin.headers,
                workflow_path,
                workflow_content,
                workflow_name,
            )
            workflow_id = registered["id"]
            patch_resp = e2e_client.patch(
                f"/api/workflows/{workflow_id}",
                headers=platform_admin.headers,
                json={
                    "organization_id": org1["id"],
                    "access_level": "authenticated",
                },
            )
            assert patch_resp.status_code == 200, patch_resp.text

            try:
                # Execute as org1_user — a non-admin, non-provider caller.
                from tests.e2e.conftest import execute_workflow_sync
                result = execute_workflow_sync(
                    e2e_client,
                    org1_user.headers,
                    workflow_id,
                )
                assert result["status"] == "Success", (
                    f"Workflow itself should not have errored — the SDK call should "
                    f"raise inside the try/except. Got: {result}"
                )
                payload = result.get("result", {})
                assert payload.get("raised") is True, payload
                assert payload.get("error_type") == "RuntimeError", payload
                assert "403" in payload.get("error", ""), payload
                assert "outside the caller's organization reach" in payload["error"], payload

                # Confirm the org2 mapping is unchanged (no forgery side-effect).
                check = e2e_client.get(
                    f"/api/integrations/{integration['id']}/mappings",
                    headers=platform_admin.headers,
                )
                assert check.status_code == 200
                items = check.json().get("items", [])
                org2_mappings = [m for m in items if m["organization_id"] == org2["id"]]
                assert len(org2_mappings) == 1
                assert org2_mappings[0]["entity_id"] == "org2-original", (
                    f"org2 mapping must NOT be overwritten by the blocked attempt; "
                    f"got entity_id={org2_mappings[0]['entity_id']!r}"
                )
            finally:
                e2e_client.delete(
                    f"/api/files/editor?path={workflow_path}",
                    headers=platform_admin.headers,
                )
        finally:
            e2e_client.delete(
                f"/api/integrations/{integration['id']}",
                headers=platform_admin.headers,
            )

    def test_knowledge_endpoint_isolation(
        self, e2e_client, org1_user, org2
    ):
        """Knowledge endpoints apply the same C2 gate as everywhere else.

        Full embedding-backed knowledge cross-org tests live in
        ``test_scope_execution.py`` under the ``EMBEDDINGS_AI_TEST_KEY``
        gate. This focused test exercises the *authorization* boundary
        only — it doesn't need embeddings — by hitting endpoints that
        gate before any embedding work.
        """
        # Org1 user requesting list_namespaces with scope=org2 must be 403.
        r = e2e_client.get(
            "/api/sdk/knowledge/namespaces",
            params={"scope": org2["id"]},
            headers=org1_user.headers,
        )
        assert r.status_code == 403, (
            f"Cross-org list_namespaces must be 403, got {r.status_code}: {r.text}"
        )

        # Same with explicit global.
        r = e2e_client.get(
            "/api/sdk/knowledge/namespaces",
            params={"scope": "global"},
            headers=org1_user.headers,
        )
        assert r.status_code == 403, (
            f"Org user requesting global namespaces must be 403, got {r.status_code}: {r.text}"
        )

        # delete_namespace with scope=org2 must be 403 (no embedding involved).
        r = e2e_client.delete(
            "/api/sdk/knowledge/namespace/anything",
            params={"scope": org2["id"]},
            headers=org1_user.headers,
        )
        assert r.status_code == 403, (
            f"Cross-org delete_namespace must be 403, got {r.status_code}: {r.text}"
        )

        # Search with scope=org2 must be 403 — gate fires before embedding.
        r = e2e_client.post(
            "/api/sdk/knowledge/search",
            headers=org1_user.headers,
            json={"query": "anything", "scope": org2["id"]},
        )
        assert r.status_code == 403, (
            f"Cross-org search must be 403, got {r.status_code}: {r.text}"
        )

        # Own-org UNSET must succeed (200, possibly empty results).
        r = e2e_client.get(
            "/api/sdk/knowledge/namespaces",
            headers=org1_user.headers,
        )
        assert r.status_code == 200, (
            f"Own-org UNSET list_namespaces must be 200, got {r.status_code}: {r.text}"
        )

    def test_mapping_endpoint_isolation(
        self, e2e_client, platform_admin, org1_user, org1_service_headers, org2
    ):
        """Mapping endpoints were a Codex HIGH finding (unguarded). A
        non-bypass org user must not be able to list or mutate another
        org's integration mapping via the SDK endpoints.

        Seeds a REAL integration with a REAL org2 mapping so the
        handler's _resolve_sdk_org_id call actually fires — the previous
        version of this test used a nonexistent integration name and
        returned before the gate ran, which Codex correctly flagged as
        too weak to catch a swallowed-403 regression.
        """
        integration_name = f"isolation_probe_{uuid4().hex[:8]}"
        integration_resp = e2e_client.post(
            "/api/integrations",
            headers=platform_admin.headers,
            json={
                "name": integration_name,
                "config_schema": [
                    {"key": "endpoint", "type": "string", "required": True, "description": ""}
                ],
            },
        )
        assert integration_resp.status_code == 201, integration_resp.text
        integration = integration_resp.json()

        try:
            # Seed an org2-scoped mapping so a swallowed 403 would
            # actually leak data, not just return empty.
            mapping_resp = e2e_client.post(
                f"/api/integrations/{integration['id']}/mappings",
                headers=platform_admin.headers,
                json={
                    "organization_id": org2["id"],
                    "entity_id": "org2-tenant-secret",
                    "entity_name": "Org2 Tenant",
                },
            )
            assert mapping_resp.status_code == 201, mapping_resp.text

            # list_mappings ignores scope: asking for org2 from org1 still
            # lists only what org1 can read, never org2's mapping.
            r = e2e_client.post(
                "/api/sdk/integrations/list_mappings",
                headers=org1_service_headers,
                json={"name": integration_name, "scope": org2["id"]},
            )
            assert r.status_code == 200, r.text
            assert "org2-tenant-secret" not in {item["entity_id"] for item in r.json()["items"]}

            # get_mapping with scope=org2 as an org1 user must be 403.
            r = e2e_client.post(
                "/api/sdk/integrations/get_mapping",
                headers=org1_user.headers,
                json={"name": integration_name, "scope": org2["id"]},
            )
            assert r.status_code == 403, (
                f"Cross-org get_mapping must be 403, got {r.status_code}: {r.text}"
            )

            # delete_mapping with scope=org2 as an org1 user must be 403,
            # and the row must still exist afterward.
            r = e2e_client.post(
                "/api/sdk/integrations/delete_mapping",
                headers=org1_user.headers,
                json={"name": integration_name, "scope": org2["id"]},
            )
            assert r.status_code == 403, (
                f"Cross-org delete_mapping must be 403, got {r.status_code}: {r.text}"
            )

            # Confirm the org2 mapping is still there — the delete didn't fall through.
            check = e2e_client.get(
                f"/api/integrations/{integration['id']}/mappings",
                headers=platform_admin.headers,
            )
            assert check.status_code == 200
            items = check.json().get("items", [])
            org2_mappings = [m for m in items if m["organization_id"] == org2["id"]]
            assert len(org2_mappings) == 1, (
                f"org2 mapping must survive a forbidden delete attempt; got: {check.json()}"
            )

            # upsert_mapping targeting org2 from an org1 user must be 403.
            r = e2e_client.post(
                "/api/sdk/integrations/upsert_mapping",
                headers=org1_user.headers,
                json={
                    "name": integration_name,
                    "scope": org2["id"],
                    "entity_id": "forged",
                    "entity_name": "forged",
                    "config": {},
                },
            )
            assert r.status_code == 403, (
                f"Cross-org upsert_mapping must be 403, got {r.status_code}: {r.text}"
            )
        finally:
            e2e_client.delete(
                f"/api/integrations/{integration['id']}",
                headers=platform_admin.headers,
            )
