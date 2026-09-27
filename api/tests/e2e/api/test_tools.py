"""
E2E tests for the /api/tools unified tools endpoint.

Verifies that workflow tools carry their owning organization's id + name,
and that system tools leave both fields null.
"""

import uuid

import pytest


def _register_tool(e2e_client, headers, organization_id: str | None) -> dict:
    """Register a minimal @tool workflow and pin its organization_id."""
    slug = uuid.uuid4().hex[:8]
    path = f"apps/tools_api_test/tool_{slug}.py"
    fn = f"tool_{slug}"
    content = (
        "from bifrost import tool\n"
        "\n"
        f"@tool(description='tools endpoint org-field test {slug}')\n"
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

    # Pin org scope (endpoint distinguishes unset from explicit null via model_fields_set)
    patch_resp = e2e_client.patch(
        f"/api/workflows/{workflow['id']}",
        headers=headers,
        json={"organization_id": organization_id},
    )
    assert patch_resp.status_code == 200, patch_resp.text
    return {**patch_resp.json(), "_path": path}


def _cleanup_tool(e2e_client, headers, tool: dict) -> None:
    """Best-effort teardown: remove the workflow file so it doesn't pollute
    module index / S3 listing for subsequent tests (particularly unit tests
    that assert an empty module index)."""
    path = tool.get("_path")
    if not path:
        return
    try:
        e2e_client.delete(
            "/api/files/editor",
            headers=headers,
            params={"path": path},
        )
    except Exception:
        # Best-effort; teardown failures must not mask real test failures.
        pass


@pytest.mark.e2e
class TestToolsEndpointOrgFields:
    """The /api/tools response must expose organization_id + organization_name."""

    def test_global_workflow_tool_has_null_org(self, e2e_client, platform_admin):
        """A workflow tool pinned to global (null org) returns null org fields."""
        tool = _register_tool(e2e_client, platform_admin.headers, organization_id=None)
        try:
            resp = e2e_client.get("/api/tools", headers=platform_admin.headers)
            assert resp.status_code == 200, resp.text
            tools = resp.json()["tools"]

            match = next((t for t in tools if t["id"] == tool["id"]), None)
            assert match is not None, f"tool {tool['id']} missing from /api/tools"
            assert match["type"] == "workflow"
            assert match["organization_id"] is None
            assert match["organization_name"] is None
        finally:
            _cleanup_tool(e2e_client, platform_admin.headers, tool)

    def test_org_scoped_workflow_tool_carries_org_fields(
        self, e2e_client, platform_admin, org1
    ):
        """A workflow tool pinned to an org returns that org's id + name."""
        tool = _register_tool(
            e2e_client, platform_admin.headers, organization_id=str(org1["id"])
        )
        try:
            resp = e2e_client.get("/api/tools", headers=platform_admin.headers)
            assert resp.status_code == 200, resp.text
            tools = resp.json()["tools"]

            match = next((t for t in tools if t["id"] == tool["id"]), None)
            assert match is not None, f"tool {tool['id']} missing from /api/tools"
            assert match["type"] == "workflow"
            assert match["organization_id"] == str(org1["id"])
            assert match["organization_name"] == org1["name"]
        finally:
            _cleanup_tool(e2e_client, platform_admin.headers, tool)

    def test_system_tools_have_null_org_fields(self, e2e_client, platform_admin):
        """System tools never belong to an org — both fields must be null."""
        resp = e2e_client.get(
            "/api/tools?type=system", headers=platform_admin.headers
        )
        assert resp.status_code == 200, resp.text
        tools = resp.json()["tools"]
        assert len(tools) > 0, "expected at least one system tool"

        for tool in tools:
            assert tool["type"] == "system"
            assert tool["organization_id"] is None, (
                f"system tool {tool['id']} unexpectedly has organization_id"
            )
            assert tool["organization_name"] is None, (
                f"system tool {tool['id']} unexpectedly has organization_name"
            )


@pytest.fixture
def scoped_workflow_tools(e2e_client, platform_admin, org1, org2):
    """Create one global tool and one tool in each test org."""
    tools = [
        _register_tool(e2e_client, platform_admin.headers, organization_id=None),
        _register_tool(
            e2e_client, platform_admin.headers, organization_id=str(org1["id"])
        ),
        _register_tool(
            e2e_client, platform_admin.headers, organization_id=str(org2["id"])
        ),
    ]
    try:
        yield tools
    finally:
        for tool in tools:
            _cleanup_tool(e2e_client, platform_admin.headers, tool)


@pytest.mark.e2e
class TestToolsEndpointOrgScope:
    """The /api/tools workflow list uses resolved org-scope semantics."""

    def _workflow_tool_ids(self, e2e_client, headers, scope: str | None = None) -> set[str]:
        params = {"type": "workflow"}
        if scope is not None:
            params["scope"] = scope
        resp = e2e_client.get("/api/tools", headers=headers, params=params)
        assert resp.status_code == 200, resp.text
        return {tool["id"] for tool in resp.json()["tools"]}

    def test_platform_admin_no_scope_sees_all_workflow_tools(
        self, e2e_client, platform_admin, scoped_workflow_tools
    ):
        ids = self._workflow_tool_ids(e2e_client, platform_admin.headers)

        assert {tool["id"] for tool in scoped_workflow_tools}.issubset(ids)

    def test_platform_admin_global_scope_sees_only_global_workflow_tools(
        self, e2e_client, platform_admin, scoped_workflow_tools
    ):
        global_tool, org1_tool, org2_tool = scoped_workflow_tools

        ids = self._workflow_tool_ids(
            e2e_client, platform_admin.headers, scope="global"
        )

        assert global_tool["id"] in ids
        assert org1_tool["id"] not in ids
        assert org2_tool["id"] not in ids

    def test_platform_admin_org_scope_sees_only_that_org_workflow_tools(
        self, e2e_client, platform_admin, org1, scoped_workflow_tools
    ):
        global_tool, org1_tool, org2_tool = scoped_workflow_tools

        ids = self._workflow_tool_ids(
            e2e_client, platform_admin.headers, scope=str(org1["id"])
        )

        assert global_tool["id"] not in ids
        assert org1_tool["id"] in ids
        assert org2_tool["id"] not in ids

    def test_org_user_sees_own_org_plus_global_workflow_tools(
        self, e2e_client, org1_user, scoped_workflow_tools
    ):
        global_tool, org1_tool, org2_tool = scoped_workflow_tools

        ids = self._workflow_tool_ids(e2e_client, org1_user.headers)

        assert global_tool["id"] in ids
        assert org1_tool["id"] in ids
        assert org2_tool["id"] not in ids
