"""End-to-end coverage for the four org-scoping scenarios.

These tests inspire confidence that the consolidation works against a
production-shaped environment, not just against mocked resolvers. They
were added during the 2026-05 consolidation when the user pointed out
that we had unit-level confidence in the C2 bypass gate but no e2e
proof — particularly for the non-admin provider-org-member path, which
is the caller archetype the bypass rule was added for (e.g. a Covi
employee in the platform org).

Scenarios — each is verified against a different real user via real
endpoints (config, tables, mapping):

    (1) Orgs can get to their own stuff.
    (2a) Platform admin (is_superuser=True) can get to everybody.
    (2b) Provider-org member (is_superuser=False, in is_provider=True org)
         can get to everybody. ← The new C2 path.
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
class TestScenario1_OrgsReachOwnData:
    """Regular org users can read their own org's data via the SDK,
    and the resolver returns their org_id on UNSET scope."""

    def test_org_user_sdk_get_with_unset_scope_resolves_to_own_org(
        self, e2e_client, platform_admin, org1, org1_user
    ):
        key = f"own_{uuid4().hex[:8]}"
        _seed_config(
            e2e_client,
            platform_admin.headers,
            key=key,
            value="org1_value",
            org_id=org1["id"],
        )

        r = _get_config_via_sdk(e2e_client, org1_user.headers, key=key, scope=None)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body is not None, "Unset scope should resolve to org1_user's own org"
        assert body["value"] == "org1_value"

    def test_org_user_sdk_get_with_explicit_own_org_succeeds(
        self, e2e_client, platform_admin, org1, org1_user
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
            e2e_client, org1_user.headers, key=key, scope=org1["id"]
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


# =============================================================================
# Scenario 2b: Provider-org member (non-admin) can reach any org
# This is the new C2 path that had no e2e before this PR.
# =============================================================================


@pytest.mark.e2e
class TestScenario2b_ProviderOrgBypass:
    """A non-superuser in the provider org bypasses scope restrictions.

    Pre-overhaul this case would have failed in two ways:
    (a) The pre-overhaul ``_resolve_sdk_org_id`` accepted any UUID without
        a gate, so even regular users could traverse — not a "test passes
        for the right reason" outcome.
    (b) Post-overhaul fix had to grant the provider-org membership path.
    These tests pin the post-fix behavior so the C2 bypass doesn't
    silently regress to "platform admin only."
    """

    def test_provider_member_explicit_scope_to_other_org(
        self, e2e_client, platform_admin, provider_org_user, org1
    ):
        key = f"prov_to_org1_{uuid4().hex[:8]}"
        _seed_config(
            e2e_client,
            platform_admin.headers,
            key=key,
            value="org1_via_provider_member",
            org_id=org1["id"],
        )
        r = _get_config_via_sdk(
            e2e_client, provider_org_user.headers, key=key, scope=org1["id"]
        )
        assert r.status_code == 200, (
            f"Provider-org member should reach org1 via explicit scope. "
            f"Got: {r.status_code} {r.text}"
        )
        body = r.json()
        assert body is not None, "Provider-org bypass should return the row"
        assert body["value"] == "org1_via_provider_member"

    def test_provider_member_explicit_global(
        self, e2e_client, platform_admin, provider_org_user
    ):
        key = f"prov_to_global_{uuid4().hex[:8]}"
        _seed_config(
            e2e_client, platform_admin.headers, key=key, value="g", org_id=None
        )
        r = _get_config_via_sdk(
            e2e_client, provider_org_user.headers, key=key, scope="global"
        )
        assert r.status_code == 200, (
            f"Provider-org member should reach global via explicit scope. "
            f"Got: {r.status_code} {r.text}"
        )
        assert r.json() is not None

    def test_provider_member_unset_list_mappings_returns_all_orgs(
        self, e2e_client, platform_admin, provider_org_user, org1, org1_user, org2
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
            assert provider_resp.status_code == 200, provider_resp.text
            provider_items = provider_resp.json()["items"]
            assert {item["entity_id"] for item in provider_items} == {
                "provider-tenant",
                "org1-tenant",
                "org2-tenant",
            }

            org1_resp = e2e_client.post(
                "/api/sdk/integrations/list_mappings",
                headers=org1_user.headers,
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

    def test_provider_member_cross_org_read_through_engine(
        self, e2e_client, platform_admin, provider_org_user, org1
    ):
        """Provider-org member reads another org's config FROM INSIDE a workflow.

        This is the path the two tests above do NOT cover. They hit
        ``/api/sdk/config/get`` directly, where the API authenticates the
        *user* and the API-side ``_resolve_sdk_org_id`` resolves scope.

        Under real workflow execution the path is different and is the one
        that actually matters for the C2 bypass:

          form/execute → enqueue (context reduced to ``org_id`` + scalars,
          stored in Redis) → ``workflow_execution`` consumer rehydrates the
          org via ``OrganizationRepository.get_with_cache(org_id)`` (which
          must carry ``is_provider``) → worker → SDK ``resolve_scope`` is the
          security gate (the API is the engine sentinel, ``is_superuser=True``,
          so the API-side gate always passes).

        If ``get_with_cache`` ever drops ``is_provider`` (e.g. a cache entry
        that omits the field), the SDK-side ``resolve_scope`` sees
        ``is_provider=False`` and raises ``PermissionError`` — and this test
        fails. That is exactly the regression we want pinned: the provider-org
        bypass must survive the enqueue→rehydrate round-trip, not just the
        direct API call.
        """
        # Seed an org1-scoped config the workflow will read cross-org.
        key = f"prov_engine_{uuid4().hex[:8]}"
        _seed_config(
            e2e_client,
            platform_admin.headers,
            key=key,
            value="org1_secret_read_by_provider_member",
            org_id=org1["id"],
        )

        # A workflow that reads the org1 config with an explicit cross-org scope.
        # It returns the value (success) or the exception type (gate denied),
        # so the assertion can distinguish "bypass worked" from "bypass broke".
        workflow_name = f"e2e_prov_read_{uuid4().hex[:8]}"
        workflow_path = f"{workflow_name}.py"
        workflow_content = (
            '"""E2E: provider-org member cross-org read through the engine."""\n'
            "from bifrost import workflow, config\n"
            "\n"
            "@workflow(\n"
            f'    name="{workflow_name}",\n'
            '    description="Provider-org member reads another org\'s config.",\n'
            '    execution_mode="sync",\n'
            ")\n"
            f"async def {workflow_name}():\n"
            "    try:\n"
            f'        value = await config.get("{key}", scope="{org1["id"]}")\n'
            '        return {"raised": False, "value": value}\n'
            "    except Exception as e:\n"
            '        return {"raised": True, "error_type": type(e).__name__, "error": str(e)}\n'
        )

        from tests.e2e.conftest import execute_workflow_sync, write_and_register

        # Register as platform admin (engine needs the file), then move the
        # workflow into the provider org so provider_org_user can execute it.
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

        # Execute as provider_org_user — non-admin (is_superuser=False),
        # is_provider=True. The C2 bypass must let the cross-org read through.
        result = execute_workflow_sync(
            e2e_client,
            provider_org_user.headers,
            workflow_id,
        )
        assert result["status"] == "Success", (
            f"Workflow should not error — the SDK read is inside try/except. "
            f"Got: {result}"
        )
        payload = result.get("result", {})
        assert payload.get("raised") is False, (
            f"Provider-org member's cross-org read was DENIED inside the engine. "
            f"This means is_provider did not survive the enqueue→get_with_cache "
            f"rehydration (the C2 bypass is broken on the execution path). "
            f"Got: {payload!r}"
        )
        assert payload.get("value") == "org1_secret_read_by_provider_member", (
            f"Cross-org read returned the wrong value: {payload!r}"
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
        self, e2e_client, platform_admin, org1, org1_user
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

        r = _get_config_via_sdk(e2e_client, org1_user.headers, key=key, scope=None)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body is not None
        assert body["value"] == "ORG1", (
            "Cascade should prefer org-scoped value over global when both exist."
        )

    def test_falls_back_to_global_when_org_value_missing(
        self, e2e_client, platform_admin, org1_user
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

        r = _get_config_via_sdk(e2e_client, org1_user.headers, key=key, scope=None)
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
    must not be able to surface another org's data — not via explicit
    scope, not via UNSET (because their resolved org_id is their own,
    not the other org's), not via explicit global."""

    def test_org_user_explicit_other_org_returns_403(
        self, e2e_client, platform_admin, org2, org1_user
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
            e2e_client, org1_user.headers, key=key, scope=org2["id"]
        )
        # Resolver raises 403; the endpoint may also legitimately return
        # 200 with null body if the implementation chooses to swallow.
        # 403 is what we expect; we assert it explicitly so a silent
        # downgrade to 200+null can't slip through.
        assert r.status_code == 403, (
            f"Cross-org explicit scope must be 403, not {r.status_code}: {r.text}"
        )

    def test_org_user_explicit_global_returns_403(
        self, e2e_client, org1_user
    ):
        r = _get_config_via_sdk(
            e2e_client, org1_user.headers, key="anything", scope="global"
        )
        assert r.status_code == 403, (
            f"Org user requesting global must be 403, not {r.status_code}: {r.text}"
        )

    def test_org_user_unset_does_not_leak_other_org(
        self, e2e_client, platform_admin, org2, org1_user
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
        r = _get_config_via_sdk(e2e_client, org1_user.headers, key=key, scope=None)
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
        """Regression for Codex round-3 HIGH finding.

        Under workflow execution, the API authenticates the engine
        sentinel (``is_superuser=True``), so the API-side C2 gate ALWAYS
        passes. The SDK-side ``resolve_scope`` is the security boundary
        for workflow callers — it must catch a non-bypass workflow
        trying to mutate another org's mapping.

        This test:
          1. Seeds a real integration with an org2 mapping.
          2. Defines a workflow in org1 (non-provider).
          3. Executes it as ``org1_user`` (non-admin, non-provider).
          4. The workflow attempts ``integrations.upsert_mapping``
             with ``scope=org2_id``.
          5. Asserts a PermissionError was raised inside the workflow
             (caught and surfaced via the workflow result).

        Before the fix, the SDK posted the caller-supplied scope raw
        and the API let it through (engine sentinel is superuser).
        After the fix the SDK calls ``resolve_scope(scope)`` locally
        which raises PermissionError under the C2 rule.
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
                '"""E2E engine-sentinel isolation regression."""\n'
                'from bifrost import workflow, integrations\n'
                '\n'
                '@workflow(\n'
                f'    name="{workflow_name}",\n'
                '    description="Attempts to clobber another org\'s mapping.",\n'
                '    execution_mode="sync",\n'
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
                assert payload.get("raised") is True, (
                    f"SDK-side resolve_scope must raise PermissionError; "
                    f"got result {payload!r}"
                )
                assert payload.get("error_type") == "PermissionError", (
                    f"Expected PermissionError, got {payload.get('error_type')!r}: "
                    f"{payload.get('error')!r}"
                )

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
        self, e2e_client, platform_admin, org1_user, org2
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

            # list_mappings with scope=org2 as an org1 user must be 403.
            r = e2e_client.post(
                "/api/sdk/integrations/list_mappings",
                headers=org1_user.headers,
                json={"name": integration_name, "scope": org2["id"]},
            )
            assert r.status_code == 403, (
                f"Cross-org list_mappings must be 403, got {r.status_code}: {r.text}"
            )

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
