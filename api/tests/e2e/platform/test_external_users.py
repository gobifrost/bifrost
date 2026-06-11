"""E2E: external-user isolation (EXT-1).

An EXTERNAL, non-bypass user sees only role-granted entities in their own
org:

- No global (NULL-org) tier: listings contain ZERO global entities, by-id
  access to global entities is denied — while a normal user in the same org
  still sees the global tier.
- No authenticated-tier entitlement: ``access_level="authenticated"``
  entities (even in their own org) do not grant; ``role_based`` + an
  explicitly assigned role does, and the form is executable.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from tests.e2e.conftest import write_and_register
from tests.e2e.fixtures.setup import _register_and_authenticate_user
from tests.e2e.fixtures.users import E2EUser

pytestmark = pytest.mark.e2e

SUFFIX = uuid4().hex[:8]


@pytest.fixture(scope="module")
def ext_workflow(e2e_client, platform_admin):
    """A real (global) workflow so the role-granted form is executable."""
    content = '''"""E2E External Users Workflow"""
from bifrost import workflow

@workflow(
    name="e2e_external_users_workflow",
    description="Workflow used by external-user isolation E2E tests",
)
async def e2e_external_users_workflow(foo: str = "bar") -> dict:
    return {"ok": True, "foo": foo}
'''
    result = write_and_register(
        e2e_client,
        platform_admin.headers,
        "e2e_external_users_workflow.py",
        content,
        "e2e_external_users_workflow",
    )
    yield result
    e2e_client.delete(
        "/api/files/editor?path=e2e_external_users_workflow.py",
        headers=platform_admin.headers,
    )


@pytest.fixture(scope="module")
def portal_role(e2e_client, platform_admin):
    """Bespoke role held by the external user."""
    resp = e2e_client.post(
        "/api/roles",
        headers=platform_admin.headers,
        json={
            "name": f"E2E External Portal {SUFFIX}",
            "description": "external-user isolation e2e",
        },
    )
    assert resp.status_code == 201, resp.text
    role = resp.json()
    yield role
    e2e_client.delete(f"/api/roles/{role['id']}", headers=platform_admin.headers)


def _create_form(e2e_client, headers, *, name, workflow_id, org_id, access_level, role_ids=None):
    body = {
        "name": name,
        "description": "external-user isolation e2e",
        "workflow_id": workflow_id,
        "form_schema": {
            "fields": [
                {"name": "foo", "type": "text", "label": "Foo", "required": False},
            ]
        },
        "access_level": access_level,
        "organization_id": org_id,
    }
    if role_ids:
        body["role_ids"] = role_ids
    resp = e2e_client.post("/api/forms", headers=headers, json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.fixture(scope="module")
def global_auth_form(e2e_client, platform_admin, ext_workflow):
    """GLOBAL form at access_level=authenticated — the canary entity."""
    form = _create_form(
        e2e_client,
        platform_admin.headers,
        name=f"E2E Ext Global Auth Form {SUFFIX}",
        workflow_id=ext_workflow["id"],
        org_id=None,
        access_level="authenticated",
    )
    yield form
    e2e_client.delete(f"/api/forms/{form['id']}", headers=platform_admin.headers)


@pytest.fixture(scope="module")
def org_auth_form(e2e_client, platform_admin, ext_workflow, org1):
    """Org-scoped authenticated form — externals get no authenticated tier."""
    form = _create_form(
        e2e_client,
        platform_admin.headers,
        name=f"E2E Ext Org Auth Form {SUFFIX}",
        workflow_id=ext_workflow["id"],
        org_id=org1["id"],
        access_level="authenticated",
    )
    yield form
    e2e_client.delete(f"/api/forms/{form['id']}", headers=platform_admin.headers)


@pytest.fixture(scope="module")
def org_role_form(e2e_client, platform_admin, ext_workflow, org1, portal_role):
    """Org-scoped role_based form granted to the external user's role."""
    form = _create_form(
        e2e_client,
        platform_admin.headers,
        name=f"E2E Ext Org Role Form {SUFFIX}",
        workflow_id=ext_workflow["id"],
        org_id=org1["id"],
        access_level="role_based",
        role_ids=[portal_role["id"]],
    )
    yield form
    e2e_client.delete(f"/api/forms/{form['id']}", headers=platform_admin.headers)


@pytest.fixture(scope="module")
def global_auth_agent(e2e_client, platform_admin):
    """GLOBAL agent at access_level=authenticated — agents-listing canary."""
    resp = e2e_client.post(
        "/api/agents",
        headers=platform_admin.headers,
        json={
            "name": f"E2E Ext Global Agent {SUFFIX}",
            "system_prompt": "You are an e2e canary.",
            "access_level": "authenticated",
            "organization_id": None,
        },
    )
    assert resp.status_code == 201, resp.text
    agent = resp.json()
    yield agent
    e2e_client.delete(f"/api/agents/{agent['id']}", headers=platform_admin.headers)


@pytest.fixture(scope="module")
def external_user(e2e_client, platform_admin, org1, portal_role) -> E2EUser:
    """External (portal/guest) user in org1, holding only the bespoke role."""
    user = E2EUser(
        email=f"e2e-external-{SUFFIX}@gobifrost.dev",
        password="ExternalPass123!",
        name=f"E2E External {SUFFIX}",
        organization_id=UUID(org1["id"]),
    )
    resp = e2e_client.post(
        "/api/users",
        headers=platform_admin.headers,
        json={
            "email": user.email,
            "name": user.name,
            "organization_id": org1["id"],
            "is_superuser": False,
            "is_external": True,
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["is_external"] is True, "is_external must round-trip on create"
    user.user_id = UUID(body["id"])

    assign = e2e_client.post(
        f"/api/roles/{portal_role['id']}/users",
        headers=platform_admin.headers,
        json={"user_ids": [str(user.user_id)]},
    )
    assert assign.status_code == 204, assign.text

    user = _register_and_authenticate_user(e2e_client, user, skip_registration=False)
    user.organization_id = UUID(org1["id"])
    return user


def _form_ids(e2e_client, user) -> set[str]:
    resp = e2e_client.get("/api/forms", headers=user.headers)
    assert resp.status_code == 200, resp.text
    return {f["id"] for f in resp.json()}


def _global_entities(items: list[dict]) -> list[dict]:
    return [i for i in items if i.get("organization_id") is None]


class TestExternalUserGlobalTier:
    def test_external_listings_contain_zero_global_entities(
        self,
        e2e_client,
        external_user,
        global_auth_form,
        global_auth_agent,
    ):
        # Forms: not just the canary — ZERO global entries of any kind.
        resp = e2e_client.get("/api/forms", headers=external_user.headers)
        assert resp.status_code == 200, resp.text
        forms = resp.json()
        assert _global_entities(forms) == [], (
            "external user's form listing must contain zero global forms"
        )
        assert global_auth_form["id"] not in {f["id"] for f in forms}

        # Agents.
        resp = e2e_client.get("/api/agents", headers=external_user.headers)
        assert resp.status_code == 200, resp.text
        agents = resp.json()
        agent_items = agents if isinstance(agents, list) else agents.get("agents", [])
        assert _global_entities(agent_items) == [], (
            "external user's agent listing must contain zero global agents"
        )
        assert global_auth_agent["id"] not in {a["id"] for a in agent_items}

    def test_normal_org_user_still_sees_global_tier(
        self,
        e2e_client,
        org1_user,
        global_auth_form,
        global_auth_agent,
    ):
        assert global_auth_form["id"] in _form_ids(e2e_client, org1_user), (
            "normal org user must still see the global authenticated form"
        )
        resp = e2e_client.get("/api/agents", headers=org1_user.headers)
        assert resp.status_code == 200, resp.text
        agents = resp.json()
        agent_items = agents if isinstance(agents, list) else agents.get("agents", [])
        assert global_auth_agent["id"] in {a["id"] for a in agent_items}

    def test_external_user_denied_global_form_by_id(
        self, e2e_client, external_user, global_auth_form
    ):
        resp = e2e_client.get(
            f"/api/forms/{global_auth_form['id']}", headers=external_user.headers
        )
        assert resp.status_code in (403, 404), (
            f"global form must not be reachable by id for externals: {resp.status_code}"
        )

    def test_external_user_cannot_execute_global_authenticated_form(
        self, e2e_client, external_user, global_auth_form
    ):
        resp = e2e_client.post(
            f"/api/forms/{global_auth_form['id']}/execute",
            headers=external_user.headers,
            json={"form_data": {"foo": "x"}},
        )
        assert resp.status_code in (403, 404), (
            f"global authenticated form must not execute for externals: {resp.status_code}"
        )


class TestExternalUserAuthenticatedTier:
    def test_org_authenticated_form_not_visible_to_external(
        self, e2e_client, external_user, org_auth_form
    ):
        assert org_auth_form["id"] not in _form_ids(e2e_client, external_user), (
            "authenticated-tier org form must not grant to an external user"
        )
        resp = e2e_client.get(
            f"/api/forms/{org_auth_form['id']}", headers=external_user.headers
        )
        assert resp.status_code in (403, 404)

    def test_org_authenticated_form_visible_to_normal_user(
        self, e2e_client, org1_user, org_auth_form
    ):
        assert org_auth_form["id"] in _form_ids(e2e_client, org1_user)


class TestExternalUserRoleGrant:
    def test_role_granted_form_is_listed(
        self, e2e_client, external_user, org_role_form
    ):
        assert org_role_form["id"] in _form_ids(e2e_client, external_user), (
            "role_based form granted to the external user's role must be listed"
        )

    def test_role_granted_form_is_executable(
        self, e2e_client, external_user, org_role_form
    ):
        resp = e2e_client.post(
            f"/api/forms/{org_role_form['id']}/execute",
            headers=external_user.headers,
            json={"form_data": {"foo": "external"}},
        )
        assert resp.status_code == 200, (
            f"role-granted form must execute for the external user: "
            f"{resp.status_code} {resp.text}"
        )


@pytest.fixture(scope="module")
def global_tool(e2e_client, platform_admin):
    """A GLOBAL workflow of type='tool' — the /api/tools canary."""
    content = '''"""E2E External Users Global Tool"""
from bifrost import tool

@tool(
    name="e2e_external_users_global_tool",
    description="Global tool canary for external-user isolation",
)
async def e2e_external_users_global_tool(q: str = "x") -> str:
    return q
'''
    result = write_and_register(
        e2e_client,
        platform_admin.headers,
        "e2e_external_users_global_tool.py",
        content,
        "e2e_external_users_global_tool",
    )
    assert result["type"] == "tool", result
    yield result
    e2e_client.delete(
        "/api/files/editor?path=e2e_external_users_global_tool.py",
        headers=platform_admin.headers,
    )


def _tool_workflow_ids(e2e_client, user) -> list[dict]:
    resp = e2e_client.get("/api/tools?type=workflow", headers=user.headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["tools"]


class TestExternalUserGlobalTools:
    """Proof: an external user gets ZERO global tools from /api/tools."""

    def test_external_user_sees_no_global_tools(
        self, e2e_client, external_user, global_tool
    ):
        tools = _tool_workflow_ids(e2e_client, external_user)
        global_tools = [t for t in tools if t.get("organization_id") is None]
        assert global_tools == [], (
            "external user's /api/tools must contain zero global tools"
        )
        assert global_tool["id"] not in {t["id"] for t in tools}

    def test_normal_user_sees_global_tool(
        self, e2e_client, org1_user, global_tool
    ):
        tools = _tool_workflow_ids(e2e_client, org1_user)
        assert global_tool["id"] in {t["id"] for t in tools}, (
            "normal org user must still see the global tool"
        )


@pytest.fixture
async def global_kb_doc(db_session, org1):
    """Seed a GLOBAL and an org knowledge document directly in the DB — the
    content-leak canary. Seeding via DB (not the embedding-dependent
    create_document endpoint) keeps the test independent of an embedding
    service; the /documents LIST path under test is a plain SELECT.
    """
    from src.models.orm.knowledge import KnowledgeStore

    ns = f"e2e-ext-ns-{SUFFIX}"
    glob = KnowledgeStore(
        namespace=ns,
        organization_id=None,
        key="global-secret",
        content="GLOBAL-SECRET-CONTENT",
        doc_metadata={},
        embedding=[0.1, 0.2, 0.3],
        chunk_index=0,
        chunk_count=1,
    )
    org_doc = KnowledgeStore(
        namespace=ns,
        organization_id=UUID(org1["id"]),
        key="org-doc",
        content="ORG-CONTENT",
        doc_metadata={},
        embedding=[0.1, 0.2, 0.3],
        chunk_index=0,
        chunk_count=1,
    )
    db_session.add_all([glob, org_doc])
    await db_session.commit()
    yield {"id": str(glob.id), "namespace": ns, "org_doc_id": str(org_doc.id)}
    for d in (glob, org_doc):
        await db_session.delete(d)
    await db_session.commit()


class TestExternalUserGlobalKnowledge:
    """Proof (worst leak): an external user reads ZERO global KB documents."""

    def test_external_user_lists_no_global_docs(
        self, e2e_client, external_user, global_kb_doc
    ):
        resp = e2e_client.get(
            "/api/knowledge-sources/documents",
            headers=external_user.headers,
            params={"namespace": f"e2e-ext-ns-{SUFFIX}"},
        )
        assert resp.status_code == 200, resp.text
        docs = resp.json()
        global_docs = [d for d in docs if d.get("organization_id") is None]
        assert global_docs == [], (
            "external user must read zero global knowledge documents"
        )
        assert global_kb_doc["id"] not in {d["id"] for d in docs}

    def test_normal_user_lists_global_doc(
        self, e2e_client, org1_user, global_kb_doc
    ):
        resp = e2e_client.get(
            "/api/knowledge-sources/documents",
            headers=org1_user.headers,
            params={"namespace": f"e2e-ext-ns-{SUFFIX}"},
        )
        assert resp.status_code == 200, resp.text
        assert global_kb_doc["id"] in {d["id"] for d in resp.json()}, (
            "normal org user must still read the global knowledge document"
        )


@pytest.fixture(scope="module")
def config_canaries(e2e_client, platform_admin, org1):
    """A GLOBAL secret config + an org config, both under a unique key, so the
    SDK/CLI config-get path (EXT-1 NEW-1) can be exercised end-to-end."""
    gkey = f"ext_global_secret_{SUFFIX}"
    okey = f"ext_org_val_{SUFFIX}"
    g = e2e_client.post(
        "/api/config",
        headers=platform_admin.headers,
        json={"key": gkey, "value": "GLOBAL-SECRET", "type": "secret", "organization_id": None},
    )
    assert g.status_code in (200, 201), g.text
    o = e2e_client.post(
        "/api/config",
        headers=platform_admin.headers,
        json={"key": okey, "value": "org-value", "type": "string", "organization_id": org1["id"]},
    )
    assert o.status_code in (200, 201), o.text
    yield {"global_key": gkey, "org_key": okey}
    e2e_client.request(
        "DELETE", "/api/sdk/config/delete",
        headers=platform_admin.headers, json={"key": gkey, "scope": "global"},
    )
    e2e_client.request(
        "DELETE", "/api/sdk/config/delete",
        headers=platform_admin.headers, json={"key": okey, "scope": org1["id"]},
    )


class TestExternalUserConfigPath:
    """EXT-1 NEW-1 (user-facing): a direct EXTERNAL user calling the SDK/CLI
    config-get endpoint gets ONLY their org's config on default scope (no global
    union — so a global SECRET is never returned/decrypted), and is 403'd if
    they explicitly ask for global or a foreign org (the _resolve_sdk_org_id
    gate). The engine-sentinel/workflow path is unaffected (separate principal)."""

    def _get(self, e2e_client, user, *, key, scope=None):
        body = {"key": key}
        if scope is not None:
            body["scope"] = scope
        return e2e_client.post("/api/sdk/config/get", headers=user.headers, json=body)

    def test_external_default_scope_excludes_global_secret(
        self, e2e_client, external_user, config_canaries
    ):
        resp = self._get(e2e_client, external_user, key=config_canaries["global_key"])
        # Global config is not in the external user's org-only view → None/404.
        assert resp.status_code in (200, 404), resp.text
        if resp.status_code == 200:
            assert resp.json() is None, (
                "external user must NOT receive the global secret value"
            )

    def test_external_explicit_global_scope_is_forbidden(
        self, e2e_client, external_user, config_canaries
    ):
        resp = self._get(
            e2e_client, external_user, key=config_canaries["global_key"], scope="global"
        )
        assert resp.status_code == 403, (
            f"external user requesting global scope must be 403'd: {resp.status_code}"
        )

    def test_normal_user_default_scope_sees_global(
        self, e2e_client, org1_user, config_canaries
    ):
        resp = self._get(e2e_client, org1_user, key=config_canaries["global_key"])
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body is not None and body.get("value") == "GLOBAL-SECRET", (
            "normal org user's default-scope read still unions global config"
        )


@pytest.fixture(scope="module")
def global_role_agent(e2e_client, platform_admin, global_tool, portal_role):
    """A GLOBAL role_based agent, assigned to portal_role (which the external
    user holds), exposing the global tool workflow. Pre-fix an external user
    would reach this agent's tools via the global tier + role-name match
    (LEAK #2); post-fix the org filter drops it before the role check.
    """
    resp = e2e_client.post(
        "/api/agents",
        headers=platform_admin.headers,
        json={
            "name": f"E2E Ext Global Role Agent {SUFFIX}",
            "system_prompt": "You are a global role agent.",
            "access_level": "role_based",
            "organization_id": None,
            "role_ids": [portal_role["id"]],
            "tool_ids": [global_tool["id"]],
        },
    )
    assert resp.status_code == 201, resp.text
    agent = resp.json()
    yield agent
    e2e_client.delete(f"/api/agents/{agent['id']}", headers=platform_admin.headers)


class TestExternalUserMCPAgentIsolation:
    """Proof (LEAK #2): an external user cannot reach a global/cross-org agent's
    tools via /api/mcp/tools, even when they share the agent's role."""

    def _mcp_tool_names(self, e2e_client, user) -> set[str]:
        resp = e2e_client.get("/api/mcp/tools", headers=user.headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        tools = body.get("tools", body) if isinstance(body, dict) else body
        return {t.get("name") or t.get("id") for t in tools}

    def test_external_user_cannot_reach_global_agent_tools(
        self, e2e_client, external_user, global_role_agent, global_tool
    ):
        names = self._mcp_tool_names(e2e_client, external_user)
        assert global_tool["name"] not in names, (
            "external user must NOT reach a global role_based agent's tools "
            "(no global tier — LEAK #2)"
        )
        assert global_tool["id"] not in names

    def test_normal_user_with_role_reaches_global_agent_tools(
        self, e2e_client, platform_admin, org1, portal_role,
        global_role_agent, global_tool,
    ):
        # A NON-external org user holding portal_role DOES reach the global
        # agent's tools — proving the restriction is external-specific.
        suffix = uuid4().hex[:8]
        user = E2EUser(
            email=f"e2e-mcp-normal-{suffix}@gobifrost.dev",
            password="NormalPass123!",
            name=f"Normal {suffix}",
            organization_id=UUID(org1["id"]),
        )
        resp = e2e_client.post(
            "/api/users",
            headers=platform_admin.headers,
            json={
                "email": user.email,
                "name": user.name,
                "organization_id": org1["id"],
                "is_superuser": False,
            },
        )
        assert resp.status_code == 201, resp.text
        user.user_id = UUID(resp.json()["id"])
        e2e_client.post(
            f"/api/roles/{portal_role['id']}/users",
            headers=platform_admin.headers,
            json={"user_ids": [str(user.user_id)]},
        )
        user = _register_and_authenticate_user(e2e_client, user, skip_registration=False)
        try:
            names = self._mcp_tool_names(e2e_client, user)
            assert global_tool["name"] in names, (
                "normal org user with the role must reach the global agent's tools"
            )
        finally:
            e2e_client.patch(
                f"/api/users/{user.user_id}",
                headers=platform_admin.headers,
                json={"is_active": False},
            )
            e2e_client.delete(
                f"/api/users/{user.user_id}", headers=platform_admin.headers
            )


class TestExternalUserSDKKnowledge:
    """OPEN-A: the /api/sdk/knowledge endpoints are plain CurrentUser routes
    that hardcoded ``is_superuser=True`` (sentinel trust), so an external user
    inherited the full cascade — global KB document content via search, global
    namespace counts via the listing. Post-fix the repo is constructed from
    the calling principal: external → org tier only. (The search endpoint's
    SQL is proven in tests/unit/test_cli_sdk_external.py — it requires an
    embedding service, so the e2e proof uses the embedding-free namespaces
    listing, which shares the same construction fix.)"""

    def test_external_namespaces_exclude_global_scope(
        self, e2e_client, external_user, global_kb_doc
    ):
        resp = e2e_client.get(
            "/api/sdk/knowledge/namespaces", headers=external_user.headers
        )
        assert resp.status_code == 200, resp.text
        by_ns = {n["namespace"]: n["scopes"] for n in resp.json()}
        ns = global_kb_doc["namespace"]
        # The org-scoped doc keeps the namespace visible to the external…
        assert ns in by_ns, "external must still see their org's namespace"
        # …but the GLOBAL doc must not be counted for them.
        assert by_ns[ns].get("global", 0) == 0, (
            "external user must not see the global knowledge scope"
        )

    def test_normal_user_namespaces_include_global_scope(
        self, e2e_client, org1_user, global_kb_doc
    ):
        resp = e2e_client.get(
            "/api/sdk/knowledge/namespaces", headers=org1_user.headers
        )
        assert resp.status_code == 200, resp.text
        by_ns = {n["namespace"]: n["scopes"] for n in resp.json()}
        ns = global_kb_doc["namespace"]
        assert ns in by_ns
        assert by_ns[ns].get("global", 0) >= 1, (
            "normal org user still sees the global knowledge scope"
        )


@pytest.fixture(scope="module")
def table_canaries(e2e_client, platform_admin, org1):
    """A GLOBAL table + an org1 table — the SDK tables/list canaries."""
    gname = f"e2e_ext_global_table_{SUFFIX}"
    oname = f"e2e_ext_org_table_{SUFFIX}"
    g = e2e_client.post(
        "/api/tables",
        headers=platform_admin.headers,
        json={"name": gname, "organization_id": None},
    )
    assert g.status_code == 201, g.text
    o = e2e_client.post(
        "/api/tables",
        headers=platform_admin.headers,
        json={"name": oname, "organization_id": org1["id"]},
    )
    assert o.status_code == 201, o.text
    yield {"global": g.json(), "org": o.json()}
    e2e_client.delete(
        f"/api/tables/{g.json()['id']}", headers=platform_admin.headers
    )
    e2e_client.delete(
        f"/api/tables/{o.json()['id']}", headers=platform_admin.headers
    )


class TestExternalUserSDKTablesList:
    """OPEN-B: /api/sdk/tables/list hardcoded ``is_superuser=True`` on a plain
    CurrentUser route, so an external user listed GLOBAL table names/schemas.
    Post-fix: external → org tier only; normal users keep the cascade."""

    def _names(self, e2e_client, user) -> set[str]:
        resp = e2e_client.post(
            "/api/sdk/tables/list", headers=user.headers, json={}
        )
        assert resp.status_code == 200, resp.text
        return {t["name"] for t in resp.json()}

    def test_external_list_excludes_global_table(
        self, e2e_client, external_user, table_canaries
    ):
        names = self._names(e2e_client, external_user)
        assert table_canaries["org"]["name"] in names, (
            "external user must still list their org's tables"
        )
        assert table_canaries["global"]["name"] not in names, (
            "external user must NOT list global table names/schemas"
        )

    def test_normal_user_list_includes_global_table(
        self, e2e_client, org1_user, table_canaries
    ):
        names = self._names(e2e_client, org1_user)
        assert table_canaries["global"]["name"] in names, (
            "normal org user still lists global tables"
        )


class TestExternalFlagLifecycle:
    def test_flag_updatable_via_user_update(
        self, e2e_client, platform_admin, org1
    ):
        suffix = uuid4().hex[:8]
        resp = e2e_client.post(
            "/api/users",
            headers=platform_admin.headers,
            json={
                "email": f"e2e-ext-toggle-{suffix}@gobifrost.dev",
                "name": "Toggle Test",
                "organization_id": org1["id"],
                "is_superuser": False,
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["is_external"] is False
        user_id = body["id"]

        try:
            resp = e2e_client.patch(
                f"/api/users/{user_id}",
                headers=platform_admin.headers,
                json={"is_external": True},
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["is_external"] is True

            resp = e2e_client.patch(
                f"/api/users/{user_id}",
                headers=platform_admin.headers,
                json={"is_external": False},
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["is_external"] is False
        finally:
            e2e_client.delete(
                f"/api/users/{user_id}", headers=platform_admin.headers
            )
