"""E2E: external-user access rules.

The model (api/src/repositories/README.md): org cascade scoping is pure
org→global for EVERY principal — ``is_external`` never subtracts scope.
External access is governed by the ACCESS LEVEL:

- ``authenticated`` ("Everyone except external users"): does not grant to
  externals — org or global.
- ``everyone``: grants to any signed-in user in scope, including externals.
- ``role_based``: grants externals exactly what it grants anyone with the
  role — including on GLOBAL entities.
- Knowledge content has no grant axis, so its direct endpoints deny
  externals outright; externals reach KB content only through workflows and
  agents they were granted.
- Decrypted global secrets (config/OAuth) stay denied to externals on the
  SDK surfaces (the OPEN-E/NEW-1 carve-out).
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
def ext_denied_workflow(e2e_client, platform_admin):
    """A GLOBAL workflow with NO role grants — the denial canary.

    Kept separate from ext_workflow, which the role-form fixtures
    deliberately grant (form role_ids sync additively onto the linked
    workflow via sync_entity_roles_to_workflows).
    """
    content = '''"""E2E External Users Denied Workflow"""
from bifrost import workflow

@workflow(
    name="e2e_external_users_denied_workflow",
    description="Never role-granted; externals must not execute it",
)
async def e2e_external_users_denied_workflow() -> dict:
    return {"ok": True}
'''
    result = write_and_register(
        e2e_client,
        platform_admin.headers,
        "e2e_external_users_denied_workflow.py",
        content,
        "e2e_external_users_denied_workflow",
    )
    yield result
    e2e_client.delete(
        "/api/files/editor?path=e2e_external_users_denied_workflow.py",
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
    """GLOBAL form at access_level=authenticated — excluded-tier canary."""
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
def global_role_form(e2e_client, platform_admin, ext_workflow, portal_role):
    """GLOBAL role_based form granted to the external user's role.

    The capability the old cascade-drop broke: an explicit role grant on a
    GLOBAL entity must work for an external user.
    """
    form = _create_form(
        e2e_client,
        platform_admin.headers,
        name=f"E2E Ext Global Role Form {SUFFIX}",
        workflow_id=ext_workflow["id"],
        org_id=None,
        access_level="role_based",
        role_ids=[portal_role["id"]],
    )
    yield form
    e2e_client.delete(f"/api/forms/{form['id']}", headers=platform_admin.headers)


@pytest.fixture(scope="module")
def global_everyone_form(e2e_client, platform_admin, ext_workflow):
    """GLOBAL form at access_level=everyone — grants to externals, no roles.

    The provider-built global app case (PAM/Customer Portal): one flag, no
    per-person role grants.
    """
    form = _create_form(
        e2e_client,
        platform_admin.headers,
        name=f"E2E Ext Global Everyone Form {SUFFIX}",
        workflow_id=ext_workflow["id"],
        org_id=None,
        access_level="everyone",
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


def _execute_form(e2e_client, user, form_id):
    return e2e_client.post(
        f"/api/forms/{form_id}/execute",
        headers=user.headers,
        json={"form_data": {"foo": "x"}},
    )


class TestExternalAuthenticatedTier:
    """``authenticated`` ("Everyone except external users") never grants to an
    external — org-scoped or global. The denial is the ACCESS LEVEL, not the
    scope."""

    def test_global_authenticated_form_not_listed_or_reachable(
        self, e2e_client, external_user, global_auth_form
    ):
        assert global_auth_form["id"] not in _form_ids(e2e_client, external_user)
        resp = e2e_client.get(
            f"/api/forms/{global_auth_form['id']}", headers=external_user.headers
        )
        assert resp.status_code in (403, 404)
        resp = _execute_form(e2e_client, external_user, global_auth_form["id"])
        assert resp.status_code in (403, 404)

    def test_org_authenticated_form_not_visible_to_external(
        self, e2e_client, external_user, org_auth_form
    ):
        assert org_auth_form["id"] not in _form_ids(e2e_client, external_user)
        resp = e2e_client.get(
            f"/api/forms/{org_auth_form['id']}", headers=external_user.headers
        )
        assert resp.status_code in (403, 404)

    def test_org_authenticated_form_visible_to_normal_user(
        self, e2e_client, org1_user, org_auth_form
    ):
        assert org_auth_form["id"] in _form_ids(e2e_client, org1_user)

    def test_normal_org_user_still_sees_global_tier(
        self, e2e_client, org1_user, global_auth_form
    ):
        assert global_auth_form["id"] in _form_ids(e2e_client, org1_user)


class TestExternalRoleGrant:
    """``role_based`` + an assigned role grants an external exactly what it
    grants anyone — INCLUDING on global entities (the pure-cascade capability
    the old external scope-drop broke)."""

    def test_org_role_granted_form_is_listed_and_executable(
        self, e2e_client, external_user, org_role_form
    ):
        assert org_role_form["id"] in _form_ids(e2e_client, external_user)
        resp = _execute_form(e2e_client, external_user, org_role_form["id"])
        assert resp.status_code == 200, resp.text

    def test_global_role_granted_form_is_listed_and_executable(
        self, e2e_client, external_user, global_role_form
    ):
        assert global_role_form["id"] in _form_ids(e2e_client, external_user), (
            "a role grant on a GLOBAL form must reach the external user — "
            "the cascade is org-keyed, not user-keyed"
        )
        resp = _execute_form(e2e_client, external_user, global_role_form["id"])
        assert resp.status_code == 200, resp.text


class TestExternalEveryoneTier:
    """``everyone`` grants to any signed-in user including externals — the
    provider-built global app case, no per-person role grants."""

    def test_global_everyone_form_is_listed_and_executable(
        self, e2e_client, external_user, global_everyone_form
    ):
        assert global_everyone_form["id"] in _form_ids(e2e_client, external_user)
        resp = _execute_form(e2e_client, external_user, global_everyone_form["id"])
        assert resp.status_code == 200, resp.text

    def test_everyone_form_works_for_normal_user_too(
        self, e2e_client, org1_user, global_everyone_form
    ):
        assert global_everyone_form["id"] in _form_ids(e2e_client, org1_user)


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


class TestExternalToolsCatalog:
    """The tools catalog is org-cascade scoped the same for every principal —
    an external sees global tool metadata like any org user."""

    def test_external_user_sees_global_tool(
        self, e2e_client, external_user, global_tool
    ):
        resp = e2e_client.get(
            "/api/tools?type=workflow", headers=external_user.headers
        )
        assert resp.status_code == 200, resp.text
        tools = resp.json()["tools"]
        assert global_tool["id"] in {t["id"] for t in tools}, (
            "external user lists global tools like any org user"
        )


@pytest.fixture
async def global_kb_doc(db_session, org1):
    """Seed a GLOBAL and an org knowledge document directly in the DB.

    Seeding via DB (not the embedding-dependent create_document endpoint)
    keeps the test independent of an embedding service.
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


class TestExternalKnowledgeDenied:
    """Knowledge content has no grant axis (no roles, no access_level, no row
    policies), so every direct KB surface denies externals outright. Their
    agents/workflows still ground on KB via the engine sentinel."""

    def test_external_denied_knowledge_sources_documents(
        self, e2e_client, external_user, global_kb_doc
    ):
        resp = e2e_client.get(
            "/api/knowledge-sources/documents",
            headers=external_user.headers,
            params={"namespace": global_kb_doc["namespace"]},
        )
        assert resp.status_code == 403, resp.text

    def test_external_denied_sdk_knowledge_namespaces(
        self, e2e_client, external_user
    ):
        resp = e2e_client.get(
            "/api/sdk/knowledge/namespaces", headers=external_user.headers
        )
        assert resp.status_code == 403, resp.text

    def test_normal_user_lists_global_doc(
        self, e2e_client, org1_user, global_kb_doc
    ):
        resp = e2e_client.get(
            "/api/knowledge-sources/documents",
            headers=org1_user.headers,
            params={"namespace": global_kb_doc["namespace"]},
        )
        assert resp.status_code == 200, resp.text
        assert global_kb_doc["id"] in {d["id"] for d in resp.json()}, (
            "normal org user must still read the global knowledge document"
        )


@pytest.fixture(scope="module")
def config_canaries(e2e_client, platform_admin, org1):
    """A GLOBAL secret config + an org config, both under a unique key, so the
    SDK/CLI config-get path (the NEW-1 secrets carve-out) can be exercised."""
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
    """The secrets carve-out (NEW-1/OPEN-E): a direct EXTERNAL user calling the
    SDK config-get endpoint gets ONLY their org's config on default scope (no
    global union — a global SECRET is never returned/decrypted), and is 403'd
    if they explicitly ask for global or a foreign org. The engine-sentinel
    path is unaffected (separate principal)."""

    def _get(self, e2e_client, user, *, key, scope=None):
        body = {"key": key}
        if scope is not None:
            body["scope"] = scope
        return e2e_client.post("/api/sdk/config/get", headers=user.headers, json=body)

    def test_external_default_scope_excludes_global_secret(
        self, e2e_client, external_user, config_canaries
    ):
        resp = self._get(e2e_client, external_user, key=config_canaries["global_key"])
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
    user holds), exposing the global tool workflow."""
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


class TestExternalMCPAgentRoleGrant:
    """An external user holding the agent's role reaches a GLOBAL role_based
    agent's tools — the explicit grant works across the global tier (LEAK #2's
    org-scoping fix still holds for cross-org/by-name collisions; the
    external-specific scope drop is gone)."""

    def _mcp_tool_names(self, e2e_client, user) -> set[str]:
        resp = e2e_client.get("/api/mcp/tools", headers=user.headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        tools = body.get("tools", body) if isinstance(body, dict) else body
        return {t.get("name") or t.get("id") for t in tools}

    def test_external_user_with_role_reaches_global_agent_tools(
        self, e2e_client, external_user, global_role_agent, global_tool
    ):
        names = self._mcp_tool_names(e2e_client, external_user)
        assert global_tool["name"] in names, (
            "external user holding the role must reach the global role_based "
            "agent's tools"
        )


# =============================================================================
# Solution install: external execution is governed by the workflow's access
# level — ``everyone`` executes, ``authenticated`` does not. (Replaces the
# old W3 own-install carve-out: the solution author marks external-facing
# workflows ``everyone`` explicitly.)
# =============================================================================


@pytest.fixture(scope="module")
def ext_install(e2e_client, platform_admin, org1):
    """An org1-scoped Solution install shipping two workflows + a v2 app:
    one workflow at access_level=everyone, one at authenticated."""
    slug = f"ext-tier-{SUFFIX}"
    resp = e2e_client.post(
        "/api/solutions",
        headers=platform_admin.headers,
        json={
            "slug": slug,
            "name": slug,
            "scope": "org",
            "organization_id": org1["id"],
            "global_repo_access": False,
        },
    )
    assert resp.status_code in (200, 201), resp.text
    sid = resp.json()["id"]

    app_manifest_id = str(uuid4())
    deploy = e2e_client.post(
        f"/api/solutions/{sid}/deploy",
        headers=platform_admin.headers,
        json={
            "python_files": {
                "workflows/ping.py": (
                    "from bifrost import workflow\n\n"
                    "@workflow\n"
                    "async def ping():\n"
                    "    return {'pong': True}\n"
                ),
                "workflows/internal.py": (
                    "from bifrost import workflow\n\n"
                    "@workflow\n"
                    "async def internal():\n"
                    "    return {'internal': True}\n"
                ),
            },
            "workflows": [
                {
                    "id": str(uuid4()),
                    "name": f"ping_{slug}",
                    "function_name": "ping",
                    "path": "workflows/ping.py",
                    "type": "workflow",
                    # The external-facing tier: the solution author marks
                    # workflows externals may call as ``everyone``.
                    "access_level": "everyone",
                },
                {
                    "id": str(uuid4()),
                    "name": f"internal_{slug}",
                    "function_name": "internal",
                    "path": "workflows/internal.py",
                    "type": "workflow",
                    "access_level": "authenticated",
                },
            ],
            "apps": [{
                "id": app_manifest_id,
                "slug": f"app-{slug}",
                "name": "Ext Tier App",
                "app_model": "standalone_v2",
                "dependencies": {},
                "access_level": "everyone",
                "dist_files": {
                    "index.html": '<!doctype html><div id="root"></div>',
                },
            }],
        },
    )
    assert deploy.status_code in (200, 201), deploy.text

    # The app's DB id is the remapped uuid5(install, manifest_id).
    from src.services.solutions.deploy import solution_entity_id

    app_db_id = str(solution_entity_id(UUID(sid), UUID(app_manifest_id)))
    yield {"id": sid, "app_id": app_db_id, "slug": slug}
    e2e_client.delete(f"/api/solutions/{sid}", headers=platform_admin.headers)


class TestExternalSolutionWorkflowTiers:
    def test_external_executes_everyone_tier_install_workflow(
        self, e2e_client, external_user, ext_install
    ):
        resp = e2e_client.post(
            "/api/workflows/execute",
            headers=external_user.headers,
            json={
                "workflow_id": "workflows/ping.py::ping",
                "app_id": ext_install["app_id"],
                "sync": True,
            },
        )
        assert resp.status_code == 200, (
            f"everyone-tier install workflow must execute for the external: "
            f"{resp.status_code} {resp.text}"
        )
        body = resp.json()
        assert body["status"] == "Success", body
        assert body["result"] == {"pong": True}, body

    def test_external_denied_authenticated_tier_install_workflow(
        self, e2e_client, external_user, ext_install
    ):
        resp = e2e_client.post(
            "/api/workflows/execute",
            headers=external_user.headers,
            json={
                "workflow_id": "workflows/internal.py::internal",
                "app_id": ext_install["app_id"],
                "sync": True,
            },
        )
        assert resp.status_code in (403, 404), (
            f"authenticated-tier install workflow must NOT execute for the "
            f"external: {resp.status_code} {resp.text}"
        )

    def test_external_still_cannot_execute_ungranted_global_repo_workflow(
        self, e2e_client, external_user, ext_denied_workflow, ext_install
    ):
        # A global _repo/ workflow with NO role granted to the external stays
        # unreachable even when they smuggle their own install's app_id
        # alongside the ref — the access level (not the scope) denies it.
        # (ext_workflow itself is NOT usable here: the role-form fixtures
        # sync portal_role onto it, deliberately granting execute.)
        resp = e2e_client.post(
            "/api/workflows/execute",
            headers=external_user.headers,
            json={
                "workflow_id": ext_denied_workflow["id"],
                "app_id": ext_install["app_id"],
                "sync": True,
            },
        )
        assert resp.status_code in (403, 404), (
            f"external user must NOT execute an ungranted global _repo/ "
            f"workflow: {resp.status_code} {resp.text}"
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
    """Tables are cascade-scoped like everything else: an external lists org +
    global table names/schemas (row DATA stays policy-gated, default deny).
    OPEN-B's keeper is sentinel trust: the external must not inherit
    ``is_superuser=True`` and see ALL orgs."""

    def _names(self, e2e_client, user) -> set[str]:
        resp = e2e_client.post(
            "/api/sdk/tables/list", headers=user.headers, json={}
        )
        assert resp.status_code == 200, resp.text
        return {t["name"] for t in resp.json()}

    def test_external_list_matches_normal_cascade(
        self, e2e_client, external_user, table_canaries
    ):
        names = self._names(e2e_client, external_user)
        assert table_canaries["org"]["name"] in names
        assert table_canaries["global"]["name"] in names, (
            "external user lists global table names like any org user"
        )

    def test_normal_user_list_includes_global_table(
        self, e2e_client, org1_user, table_canaries
    ):
        names = self._names(e2e_client, org1_user)
        assert table_canaries["global"]["name"] in names


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
