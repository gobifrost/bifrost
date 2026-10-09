"""E2E: /api/tools (type=workflow) only lists tools the caller can use.

Regression coverage for the ``list_tools_for_filter`` access-level filter
(``shared.workflow_access.user_can_access_workflow``): the agent editor's
tool picker must never offer a tool that ``validate_user_tool_access``
would then reject at save time.

- Platform admin: unchanged, sees every in-scope tool workflow regardless of
  access_level (regression guard — the agent editor relies on this).
- Regular org member: sees "everyone", "authenticated", and role_based tools
  where they hold a matching role; not role_based-without-role, and not
  another org's tools.
- External user: sees "everyone" and role_based-with-a-matching-role tools
  only; "authenticated" does not grant to externals (EXT-1 rule 2).
"""

from __future__ import annotations

import uuid
from uuid import UUID

import pytest

from tests.e2e.fixtures.setup import _register_and_authenticate_user
from tests.e2e.fixtures.users import E2EUser

SUFFIX = uuid.uuid4().hex[:8]


def _register_tool(e2e_client, headers, *, organization_id, access_level, role_ids=None):
    """Register a minimal @tool workflow with a given access_level/roles."""
    slug = uuid.uuid4().hex[:8]
    path = f"apps/tools_access_filter_test/tool_{slug}.py"
    fn = f"tool_{slug}"
    content = (
        "from bifrost import tool\n"
        "\n"
        f"@tool(description='tools access filter test {slug}')\n"
        f"def {fn}() -> str:\n"
        "    return 'ok'\n"
    )

    write_resp = e2e_client.put(
        "/api/files/editor/content",
        headers=headers,
        json={"path": path, "content": content, "encoding": "utf-8"},
    )
    assert write_resp.status_code in (200, 201), write_resp.text

    register_resp = e2e_client.post(
        "/api/workflows/register",
        headers=headers,
        json={"path": path, "function_name": fn},
    )
    assert register_resp.status_code in (200, 201), register_resp.text
    workflow = register_resp.json()

    patch_body = {
        "organization_id": organization_id,
        "access_level": access_level,
    }
    if role_ids is not None:
        patch_body["role_ids"] = role_ids
    patch_resp = e2e_client.patch(
        f"/api/workflows/{workflow['id']}",
        headers=headers,
        json=patch_body,
    )
    assert patch_resp.status_code == 200, patch_resp.text
    return {**patch_resp.json(), "_path": path}


def _cleanup_tool(e2e_client, headers, tool: dict) -> None:
    path = tool.get("_path")
    if not path:
        return
    try:
        e2e_client.delete("/api/files/editor", headers=headers, params={"path": path})
    except Exception:
        # Best-effort teardown: a failed delete must not mask the test's own
        # result; each test registers under a unique path, so leftovers don't collide.
        pass


def _tool_ids(e2e_client, headers) -> set[str]:
    resp = e2e_client.get("/api/tools", headers=headers, params={"type": "workflow"})
    assert resp.status_code == 200, resp.text
    return {t["id"] for t in resp.json()["tools"]}


@pytest.fixture(scope="module")
def bespoke_role(e2e_client, platform_admin):
    resp = e2e_client.post(
        "/api/roles",
        headers=platform_admin.headers,
        json={"name": f"E2E Tools Filter Role {SUFFIX}", "description": "tools access filter test"},
    )
    assert resp.status_code == 201, resp.text
    role = resp.json()
    yield role
    e2e_client.delete(f"/api/roles/{role['id']}", headers=platform_admin.headers)


@pytest.fixture(scope="module")
def other_role(e2e_client, platform_admin):
    """A role the test principals never hold — proves role_based exclusion."""
    resp = e2e_client.post(
        "/api/roles",
        headers=platform_admin.headers,
        json={"name": f"E2E Tools Filter Other Role {SUFFIX}", "description": "tools access filter test"},
    )
    assert resp.status_code == 201, resp.text
    role = resp.json()
    yield role
    e2e_client.delete(f"/api/roles/{role['id']}", headers=platform_admin.headers)


@pytest.fixture(scope="module")
def role_member(e2e_client, platform_admin, org1, bespoke_role) -> E2EUser:
    """A fresh, non-external org1 user holding only bespoke_role."""
    user = E2EUser(
        email=f"e2e-tools-filter-member-{SUFFIX}@gobifrost.dev",
        password="ToolsFilterPass123!",
        name=f"E2E Tools Filter Member {SUFFIX}",
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

    assign = e2e_client.post(
        f"/api/roles/{bespoke_role['id']}/users",
        headers=platform_admin.headers,
        json={"user_ids": [str(user.user_id)]},
    )
    assert assign.status_code == 204, assign.text

    user = _register_and_authenticate_user(user, skip_registration=False)
    user.organization_id = UUID(org1["id"])
    return user


@pytest.fixture(scope="module")
def external_member(e2e_client, platform_admin, org1, bespoke_role) -> E2EUser:
    """A fresh external (portal/guest) org1 user holding only bespoke_role."""
    user = E2EUser(
        email=f"e2e-tools-filter-external-{SUFFIX}@gobifrost.dev",
        password="ToolsFilterExtPass123!",
        name=f"E2E Tools Filter External {SUFFIX}",
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
    assert body["is_external"] is True
    user.user_id = UUID(body["id"])

    assign = e2e_client.post(
        f"/api/roles/{bespoke_role['id']}/users",
        headers=platform_admin.headers,
        json={"user_ids": [str(user.user_id)]},
    )
    assert assign.status_code == 204, assign.text

    user = _register_and_authenticate_user(user, skip_registration=False)
    user.organization_id = UUID(org1["id"])
    return user


@pytest.fixture(scope="module")
def access_level_tools(e2e_client, platform_admin, org1, org2, bespoke_role, other_role):
    """One tool per access_level, plus a role_based tool in org2, all org1-scoped
    unless noted."""
    tools = {
        "everyone": _register_tool(
            e2e_client, platform_admin.headers,
            organization_id=org1["id"], access_level="everyone",
        ),
        "authenticated": _register_tool(
            e2e_client, platform_admin.headers,
            organization_id=org1["id"], access_level="authenticated",
        ),
        "role_based_match": _register_tool(
            e2e_client, platform_admin.headers,
            organization_id=org1["id"], access_level="role_based",
            role_ids=[bespoke_role["id"]],
        ),
        "role_based_no_match": _register_tool(
            e2e_client, platform_admin.headers,
            organization_id=org1["id"], access_level="role_based",
            role_ids=[other_role["id"]],
        ),
        "other_org": _register_tool(
            e2e_client, platform_admin.headers,
            organization_id=org2["id"], access_level="everyone",
        ),
    }
    yield tools
    for tool in tools.values():
        _cleanup_tool(e2e_client, platform_admin.headers, tool)


@pytest.mark.e2e
class TestToolsAccessFilter:
    def test_platform_admin_sees_every_access_level(
        self, e2e_client, platform_admin, access_level_tools
    ):
        """Regression guard: admin output must stay unfiltered (agent editor)."""
        ids = _tool_ids(e2e_client, platform_admin.headers)
        for key, tool in access_level_tools.items():
            assert tool["id"] in ids, f"admin missing {key} tool"

    def test_org_member_sees_everyone_authenticated_and_matching_role(
        self, e2e_client, role_member, access_level_tools
    ):
        ids = _tool_ids(e2e_client, role_member.headers)
        assert access_level_tools["everyone"]["id"] in ids
        assert access_level_tools["authenticated"]["id"] in ids
        assert access_level_tools["role_based_match"]["id"] in ids
        assert access_level_tools["role_based_no_match"]["id"] not in ids
        assert access_level_tools["other_org"]["id"] not in ids

    def test_external_user_sees_everyone_and_matching_role_not_authenticated(
        self, e2e_client, external_member, access_level_tools
    ):
        ids = _tool_ids(e2e_client, external_member.headers)
        assert access_level_tools["everyone"]["id"] in ids
        assert access_level_tools["role_based_match"]["id"] in ids
        assert access_level_tools["authenticated"]["id"] not in ids
        assert access_level_tools["role_based_no_match"]["id"] not in ids
        assert access_level_tools["other_org"]["id"] not in ids
