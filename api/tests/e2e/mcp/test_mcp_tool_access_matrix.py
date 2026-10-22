"""E2E matrix: MCP tool-access decisions across (caller × agent × workflow).

This file exercises access to attached tools through the real MCP HTTP route,
including listing and execution. Each case registers only its target workflow.

The matrix is deliberately table-driven: each row names a concrete
(caller, agent_org, workflow_variant) combination and declares whether the
workflow can be listed and called. The pytest id names the affected row.

Workflow variants modelled:

* ``global_authenticated`` — ``organization_id=None``, ``access_level=authenticated``
* ``global_role_gated`` — ``organization_id=None``, ``access_level=role_based``, role=``role_x``
* ``org_a_authenticated`` — in Org A (platform), ``access_level=authenticated``
* ``org_a_role_gated`` — in Org A, ``role_based``, role=``role_x``
* ``org_b_authenticated`` — in Org B, ``access_level=authenticated``
* ``org_b_role_gated`` — in Org B, ``role_based``, role=``role_x``  ← THE BUG

Callers modelled:

* ``platform_admin`` — is_superuser=True (from conftest)
* ``org_a_user`` — a non-admin caller with role_x in Org A

Non-admin rows prove the admin bypass does not widen user visibility.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Any

import pytest
import pytest_asyncio


logger = logging.getLogger(__name__)


# =============================================================================
# Workflow variant table
# =============================================================================


@dataclass(frozen=True)
class WorkflowVariant:
    """Describes one row's worth of workflow state we seed for the agent."""

    key: str
    in_org: (
        str | None
    )  # "A" (platform org / admin's org), "B" (cross-org), or None (global)
    role_based: bool
    with_role: bool  # only meaningful if role_based=True


VARIANTS: list[WorkflowVariant] = [
    WorkflowVariant("global_authenticated", None, role_based=False, with_role=False),
    WorkflowVariant("global_role_gated_empty", None, role_based=True, with_role=False),
    WorkflowVariant("global_role_gated", None, role_based=True, with_role=True),
    WorkflowVariant("org_a_authenticated", "A", role_based=False, with_role=False),
    WorkflowVariant("org_a_role_gated", "A", role_based=True, with_role=True),
    WorkflowVariant("org_b_authenticated", "B", role_based=False, with_role=False),
    WorkflowVariant("org_b_role_gated", "B", role_based=True, with_role=True),
]


# A platform administrator has reach and permission for every attached tool.
ADMIN_EXPECTED_VISIBLE: dict[tuple[str, str, str], bool] = {
    # Admin + Org A agent: every variant attached must be listed for the admin.
    ("platform_admin", "A", "global_authenticated"): True,
    ("platform_admin", "A", "global_role_gated_empty"): True,
    ("platform_admin", "A", "global_role_gated"): True,
    ("platform_admin", "A", "org_a_authenticated"): True,
    ("platform_admin", "A", "org_a_role_gated"): True,
    ("platform_admin", "A", "org_b_authenticated"): True,  # cross-org, authenticated
    (
        "platform_admin",
        "A",
        "org_b_role_gated",
    ): True,  # THE BUG — cross-org + role-gated
}


# The Agent's sharing policy authorizes its explicitly bound tools. The
# original user's organization reach still limits foreign workflows.
ORG_A_USER_EXPECTED_VISIBLE: dict[tuple[str, str, str], bool] = {
    ("org_a_user_with_role_x", "A", "global_authenticated"): True,
    ("org_a_user_with_role_x", "A", "global_role_gated_empty"): True,
    ("org_a_user_with_role_x", "A", "global_role_gated"): True,
    ("org_a_user_with_role_x", "A", "org_a_authenticated"): True,
    ("org_a_user_with_role_x", "A", "org_a_role_gated"): True,
    ("org_a_user_with_role_x", "A", "org_b_authenticated"): False,
    ("org_a_user_with_role_x", "A", "org_b_role_gated"): False,
}


# =============================================================================
# Helpers: seed workflows + roles via REST as the platform admin.
# =============================================================================


def _register_simple_tool_workflow(
    e2e_client, headers: dict, organization_id: str | None
) -> dict:
    """Register a minimal ``type='tool'`` workflow under a unique path/function.

    Uses the same write-file-then-register flow as test_mcp_parity. Returns
    the RegisterWorkflowResponse dict.
    """
    slug = uuid.uuid4().hex[:8]
    path = f"apps/mcp_matrix/tool_{slug}.py"
    fn = f"tool_{slug}"
    content = (
        "from bifrost import tool\n"
        "\n"
        f"@tool(description='matrix test tool {slug}')\n"
        f"async def {fn}() -> str:\n"
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

    # Pin organization scope via PATCH /api/workflows/{id}. The endpoint
    # distinguishes "not provided" from "explicitly null" using
    # model_fields_set, so we always send organization_id for determinism.
    patch_resp = e2e_client.patch(
        f"/api/workflows/{workflow['id']}",
        headers=headers,
        json={"organization_id": organization_id},
    )
    assert patch_resp.status_code == 200, patch_resp.text
    return patch_resp.json()


def _parse_mcp_response(resp) -> dict[str, Any]:
    """Decode a FastMCP HTTP response.

    FastMCP's stateless transport returns either application/json or SSE
    (`text/event-stream`) depending on the request Accept header and
    server config. Handle both shapes here so callers can assert on a
    plain JSON-RPC payload.
    """
    import json as _json

    raw = resp.text
    if raw.startswith("data:") or "\ndata:" in raw:
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                return _json.loads(line[len("data:") :].strip())
        raise AssertionError(f"SSE response had no data line: {raw[:300]}")
    return _json.loads(raw)


def _mcp_headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }


def _gateway_data(
    e2e_client,
    headers: dict[str, str],
    tool_name: str,
    arguments: dict[str, Any],
    *,
    path: str = "/mcp",
) -> Any:
    """Call a stable gateway tool and return its structured or direct result."""
    response = e2e_client.post(
        path,
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        },
    )
    assert response.status_code == 200, response.text
    payload = _parse_mcp_response(response)
    assert "error" not in payload, payload
    result = payload["result"]
    structured = result.get("structuredContent")
    if structured is not None:
        return structured
    return result["content"][0]["text"]


def _discover_workflow_tool(
    e2e_client,
    headers: dict[str, str],
    agent_id: str,
    workflow: dict[str, Any],
    *,
    path: str | None = None,
) -> dict[str, Any]:
    """Discover one attached workflow through the stable gateway identity."""
    data = _gateway_data(
        e2e_client,
        headers,
        "bifrost_search_capabilities",
        {"agent_id": agent_id, "query": workflow["name"]},
        path=path or f"/mcp/{agent_id}",
    )
    assert "code" not in data, data
    agent = next((item for item in data["agents"] if item["id"] == agent_id), None)
    assert agent is not None, data
    tools = [item for item in agent["matching_tools"] if item["source"] == "workflow"]
    assert len(tools) == 1, data
    return tools[0]


def _set_workflow_access_level(
    e2e_client, headers: dict, workflow_id: str, access_level: str
) -> None:
    """Pin the workflow's access_level explicitly.

    Default for newly-registered workflows is ``role_based`` (see
    ``Workflow.access_level`` server_default), so any variant that wants
    ``authenticated`` semantics MUST set it explicitly — leaving it at the
    default means "role_based with no roles == superuser only" and the
    non-admin matrix will silently fail the wrong way.
    """
    patch_resp = e2e_client.patch(
        f"/api/workflows/{workflow_id}",
        headers=headers,
        json={"access_level": access_level},
    )
    assert patch_resp.status_code == 200, patch_resp.text


def _attach_workflow_role(
    e2e_client, headers: dict, workflow_id: str, role_id: str
) -> None:
    """Attach a role to a role_based workflow."""
    grant_resp = e2e_client.post(
        f"/api/workflows/{workflow_id}/roles",
        headers=headers,
        json={"role_ids": [role_id]},
    )
    assert grant_resp.status_code in (200, 201, 204), grant_resp.text


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def role_x(e2e_client, platform_admin) -> Iterator[dict]:
    """A named role used to gate role_based workflows in the matrix."""
    name = f"mcp-matrix-role-x-{uuid.uuid4().hex[:8]}"
    resp = e2e_client.post(
        "/api/roles",
        headers=platform_admin.headers,
        json={"name": name},
    )
    assert resp.status_code == 201, resp.text
    role = resp.json()
    yield role
    e2e_client.delete(f"/api/roles/{role['id']}", headers=platform_admin.headers)


@pytest.fixture
def org_a_user_with_role_x(e2e_client, platform_admin, org1_user, role_x) -> Iterator:
    """``org1_user`` (an Org A non-admin) granted ``role_x`` for the duration of the test.

    Membership is granted via ``POST /api/roles/{role_id}/users`` and revoked
    via ``DELETE /api/roles/{role_id}/users/{user_id}`` on teardown. Roles
    don't auto-cascade into the user's existing access token, but JWTs are
    short-lived and the MCP middleware re-checks role membership against the
    DB on each call (``MCPToolAccessService._get_accessible_agents`` queries
    Agent.roles, which is fine), so we don't need to mint a fresh token.

    EXCEPT — and this is important for the bug under test — the executor's
    ``WorkflowRepository.get(id=...)`` path does its role check against the
    live ``UserRole`` table by ``user_id``, not against JWT claims. So the
    DB-level grant is what matters for ``tools/call``. If a future change
    moves role checks back to JWT claims, this fixture needs to mint a
    fresh token after grant.
    """
    grant_resp = e2e_client.post(
        f"/api/roles/{role_x['id']}/users",
        headers=platform_admin.headers,
        json={"user_ids": [str(org1_user.user_id)]},
    )
    assert grant_resp.status_code in (200, 201, 204), grant_resp.text
    yield org1_user
    # Best-effort teardown.
    try:
        e2e_client.delete(
            f"/api/roles/{role_x['id']}/users/{org1_user.user_id}",
            headers=platform_admin.headers,
        )
    except Exception:
        logger.exception("matrix teardown: revoke role_x from org1_user failed")


@pytest.fixture
def customer_org_platform_admin(
    e2e_client,
    platform_admin,
    org1_user,
    refresh_user_tokens,
) -> Iterator:
    """A custom role's name does not confer the built-in admin authority."""
    role_resp = e2e_client.post(
        "/api/roles",
        headers=platform_admin.headers,
        json={"name": "Platform Admin"},
    )
    assert role_resp.status_code == 201, role_resp.text
    role = role_resp.json()
    grant_resp = e2e_client.post(
        f"/api/roles/{role['id']}/users",
        headers=platform_admin.headers,
        json={"user_ids": [str(org1_user.user_id)]},
    )
    assert grant_resp.status_code in (200, 201, 204), grant_resp.text
    refreshed_user = refresh_user_tokens(org1_user)

    try:
        yield refreshed_user
    finally:
        e2e_client.delete(
            f"/api/roles/{role['id']}/users/{org1_user.user_id}",
            headers=platform_admin.headers,
        )
        e2e_client.delete(
            f"/api/roles/{role['id']}",
            headers=platform_admin.headers,
        )
        refresh_user_tokens(org1_user)


@pytest.fixture
def org_a(org1) -> dict:
    """'Org A' in the matrix is ``org1`` — the agent's home org.

    Per the bug report, the failure mode is: platform admin accesses a
    cross-org agent. The platform admin session user has
    ``organization_id=None`` (global/platform scope), so we pin the agent
    itself into ``org1`` and call that "Org A". That matches the real-world
    shape of "admin in Platform Org uses an agent owned by a customer org."
    """
    return {"id": str(org1["id"]), "label": "A"}


@pytest.fixture
def org_b(org2) -> dict:
    """'Org B' is org2 — a real second org from the session fixtures."""
    return {"id": str(org2["id"]), "label": "B"}


@pytest_asyncio.fixture
async def seeded_agent_with_tools(
    request, e2e_client, platform_admin, role_x, org_a, org_b
) -> AsyncIterator[dict[str, Any]]:
    """Create only the workflow variant exercised by this HTTP scenario.

    Uses a fresh uuid-suffixed agent + fresh workflows per test to keep the
    stack clean under state-reset semantics of ``./test.sh``. On teardown we
    try to delete the agent and each workflow; failures are logged not raised
    so a single cleanup hiccup doesn't mask the real test failure.
    """
    admin_headers = platform_admin.headers

    # Singleton cases pass the fixture's target directly. Matrix cases use
    # their named row; discovery passes None and needs only the agent.
    variant_key = (
        request.param
        if hasattr(request, "param")
        else request.node.callspec.params["variant_key"]
    )
    assert variant_key is None or any(v.key == variant_key for v in VARIANTS)
    workflows_by_key: dict[str, dict] = {}
    for variant in (v for v in VARIANTS if v.key == variant_key):
        target_org = (
            org_a["id"]
            if variant.in_org == "A"
            else org_b["id"]
            if variant.in_org == "B"
            else None
        )
        wf = _register_simple_tool_workflow(
            e2e_client, admin_headers, organization_id=target_org
        )
        # Pin access_level explicitly. The DB default is "role_based", so
        # the "authenticated" variants need an explicit PATCH or they end
        # up gated to superusers only and the non-admin matrix lies.
        _set_workflow_access_level(
            e2e_client,
            admin_headers,
            workflow_id=wf["id"],
            access_level="role_based" if variant.role_based else "authenticated",
        )
        if variant.role_based and variant.with_role:
            _attach_workflow_role(
                e2e_client,
                admin_headers,
                workflow_id=wf["id"],
                role_id=role_x["id"],
            )
        workflows_by_key[variant.key] = wf

    # Create an Org A agent with the one workflow needed for this case.
    tool_ids = [wf["id"] for wf in workflows_by_key.values()]
    agent_resp = e2e_client.post(
        "/api/agents",
        headers=admin_headers,
        json={
            "name": f"matrix-agent-{uuid.uuid4().hex[:8]}",
            "description": "MCP tool access matrix fixture agent",
            "system_prompt": "Be helpful.",
            "channels": ["chat"],
            "access_level": "authenticated",
            "organization_id": org_a["id"],
            "tool_ids": tool_ids,
        },
    )
    assert agent_resp.status_code == 201, agent_resp.text
    agent = agent_resp.json()

    try:
        yield {
            "agent": agent,
            "workflows_by_key": workflows_by_key,
            "org_a": org_a,
            "org_b": org_b,
            "role_x": role_x,
        }
    finally:
        # Best-effort teardown.
        try:
            e2e_client.delete(f"/api/agents/{agent['id']}", headers=admin_headers)
        except Exception:
            logger.exception("matrix teardown: delete agent failed")
        for wf in workflows_by_key.values():
            try:
                e2e_client.delete(
                    f"/api/workflows/{wf['id']}?force=true",
                    headers=admin_headers,
                )
            except Exception:
                logger.exception("matrix teardown: delete workflow %s failed", wf["id"])


# =============================================================================
# The matrix
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
class TestMcpToolAccessMatrix:
    """Pin each caller and workflow variant at the stable MCP gateway boundary."""

    @pytest.mark.parametrize(
        "variant_key",
        [
            k
            for (caller, _agent_org, k) in ADMIN_EXPECTED_VISIBLE
            if caller == "platform_admin"
        ],
        ids=lambda k: f"admin__gateway_call__{k}",
    )
    async def test_platform_admin_can_execute_discovered_tool_via_mcp_gateway(
        self,
        variant_key: str,
        seeded_agent_with_tools: dict[str, Any],
        e2e_client,
        platform_admin,
    ) -> None:
        """An administrator can discover and execute every attached workflow."""
        agent = seeded_agent_with_tools["agent"]
        workflow = seeded_agent_with_tools["workflows_by_key"][variant_key]
        headers = _mcp_headers(platform_admin.access_token)
        tool = _discover_workflow_tool(e2e_client, headers, agent["id"], workflow)

        result = _gateway_data(
            e2e_client,
            headers,
            "bifrost_execute_tool",
            {"agent_id": agent["id"], "tool_ref": tool["tool_ref"], "arguments": {}},
            path=f"/mcp/{agent['id']}",
        )
        assert result == "ok", result

    @pytest.mark.parametrize(
        "variant_key",
        [
            k
            for (caller, _agent_org, k) in ORG_A_USER_EXPECTED_VISIBLE
            if caller == "org_a_user_with_role_x"
            and ORG_A_USER_EXPECTED_VISIBLE[(caller, _agent_org, k)]
        ],
        ids=lambda k: f"org_a_user__gateway_call__{k}",
    )
    async def test_org_a_user_can_execute_authorized_tools_via_mcp_gateway(
        self,
        variant_key: str,
        seeded_agent_with_tools: dict[str, Any],
        e2e_client,
        org_a_user_with_role_x,
    ) -> None:
        """A role-authorized Org A user executes the discovered tool successfully."""
        agent = seeded_agent_with_tools["agent"]
        workflow = seeded_agent_with_tools["workflows_by_key"][variant_key]
        headers = _mcp_headers(org_a_user_with_role_x.access_token)
        tool = _discover_workflow_tool(e2e_client, headers, agent["id"], workflow)

        result = _gateway_data(
            e2e_client,
            headers,
            "bifrost_execute_tool",
            {"agent_id": agent["id"], "tool_ref": tool["tool_ref"], "arguments": {}},
            path=f"/mcp/{agent['id']}",
        )
        assert result == "ok", result

    @pytest.mark.parametrize(
        "variant_key",
        [
            k
            for (caller, _agent_org, k) in ORG_A_USER_EXPECTED_VISIBLE
            if caller == "org_a_user_with_role_x"
            and not ORG_A_USER_EXPECTED_VISIBLE[(caller, _agent_org, k)]
        ],
        ids=lambda k: f"org_a_user__gateway_deny__{k}",
    )
    async def test_org_a_user_cannot_discover_or_execute_foreign_tools_via_mcp_gateway(
        self,
        variant_key: str,
        seeded_agent_with_tools: dict[str, Any],
        e2e_client,
        platform_admin,
        org_a_user_with_role_x,
    ) -> None:
        """Foreign workflows stay hidden and reject an admin-discovered reference."""
        agent = seeded_agent_with_tools["agent"]
        workflow = seeded_agent_with_tools["workflows_by_key"][variant_key]
        admin_tool = _discover_workflow_tool(
            e2e_client, _mcp_headers(platform_admin.access_token), agent["id"], workflow
        )
        headers = _mcp_headers(org_a_user_with_role_x.access_token)
        discovery = _gateway_data(
            e2e_client,
            headers,
            "bifrost_search_capabilities",
            {"agent_id": agent["id"], "query": workflow["name"]},
            path=f"/mcp/{agent['id']}",
        )
        assert "code" not in discovery, discovery
        assert all(
            tool["tool_ref"] != admin_tool["tool_ref"]
            for tool in discovery["agents"][0]["matching_tools"]
        ), discovery

        denied = _gateway_data(
            e2e_client,
            headers,
            "bifrost_execute_tool",
            {
                "agent_id": agent["id"],
                "tool_ref": admin_tool["tool_ref"],
                "arguments": {},
            },
            path=f"/mcp/{agent['id']}",
        )
        assert denied["code"] == "TOOL_NOT_FOUND_OR_FORBIDDEN", denied

    @pytest.mark.parametrize(
        "seeded_agent_with_tools", ["org_b_role_gated"], indirect=True
    )
    async def test_provider_org_user_cannot_discover_or_execute_foreign_agent_tool(
        self,
        seeded_agent_with_tools: dict[str, Any],
        e2e_client,
        provider_org_user,
        platform_admin,
    ) -> None:
        """Provider membership adds neither foreign-agent discovery nor execution."""
        agent = seeded_agent_with_tools["agent"]
        workflow = seeded_agent_with_tools["workflows_by_key"]["org_b_role_gated"]
        admin_tool = _discover_workflow_tool(
            e2e_client, _mcp_headers(platform_admin.access_token), agent["id"], workflow
        )
        headers = _mcp_headers(provider_org_user.access_token)
        discovery = _gateway_data(
            e2e_client,
            headers,
            "bifrost_search_capabilities",
            {"agent_id": agent["id"], "query": workflow["name"]},
        )
        assert discovery["code"] == "AGENT_NOT_FOUND_OR_FORBIDDEN", discovery
        denied = _gateway_data(
            e2e_client,
            headers,
            "bifrost_execute_tool",
            {
                "agent_id": agent["id"],
                "tool_ref": admin_tool["tool_ref"],
                "arguments": {},
            },
        )
        assert denied["code"] == "AGENT_NOT_FOUND_OR_FORBIDDEN", denied

    @pytest.mark.parametrize("seeded_agent_with_tools", [None], indirect=True)
    async def test_provider_gateway_discovery_preserves_organization_reach(
        self,
        seeded_agent_with_tools: dict[str, Any],
        e2e_client,
        provider_org_user,
    ) -> None:
        """Neither broader discovery nor an exact ID adds organization reach."""
        agent = seeded_agent_with_tools["agent"]
        headers = _mcp_headers(provider_org_user.access_token)

        accessible = _gateway_data(
            e2e_client, headers, "bifrost_search_capabilities", {"query": agent["name"]}
        )
        assert agent["id"] not in {item["id"] for item in accessible.get("agents", [])}
        all_scopes = _gateway_data(
            e2e_client,
            headers,
            "bifrost_search_capabilities",
            {"query": agent["name"], "discovery_scope": "all"},
        )
        assert agent["id"] not in {item["id"] for item in all_scopes.get("agents", [])}
        explicit = _gateway_data(
            e2e_client,
            headers,
            "bifrost_search_capabilities",
            {"agent_id": agent["id"]},
        )
        assert explicit["code"] == "AGENT_NOT_FOUND_OR_FORBIDDEN", explicit

    @pytest.mark.parametrize(
        "seeded_agent_with_tools", ["org_b_role_gated"], indirect=True
    )
    async def test_customer_org_platform_admin_cannot_execute_cross_org_tool(
        self,
        seeded_agent_with_tools: dict[str, Any],
        e2e_client,
        platform_admin,
        customer_org_platform_admin,
    ) -> None:
        """A custom role called Platform Admin receives no cross-org tool grant."""
        agent = seeded_agent_with_tools["agent"]
        workflow = seeded_agent_with_tools["workflows_by_key"]["org_b_role_gated"]
        admin_tool = _discover_workflow_tool(
            e2e_client, _mcp_headers(platform_admin.access_token), agent["id"], workflow
        )
        headers = _mcp_headers(customer_org_platform_admin.access_token)
        discovery = _gateway_data(
            e2e_client,
            headers,
            "bifrost_search_capabilities",
            {
                "agent_id": agent["id"],
                "query": workflow["name"],
                "discovery_scope": "all",
            },
        )
        assert "code" not in discovery, discovery
        assert agent["id"] in {item["id"] for item in discovery["agents"]}, discovery
        assert all(
            tool["tool_ref"] != admin_tool["tool_ref"]
            for tool in discovery["agents"][0]["matching_tools"]
        ), discovery

        denied = _gateway_data(
            e2e_client,
            headers,
            "bifrost_execute_tool",
            {
                "agent_id": agent["id"],
                "tool_ref": admin_tool["tool_ref"],
                "arguments": {},
            },
        )
        assert denied["code"] == "TOOL_NOT_FOUND_OR_FORBIDDEN", denied
