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
    in_org: str | None  # "A" (platform org / admin's org), "B" (cross-org), or None (global)
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


# Expected visibility matrix.
# Key: (caller_label, agent_org_label, variant.key) -> should be visible?
#
# The caller here is always the platform admin (is_superuser=True). The bug is
# that SOME rows are flipping to False today. All should be True — the admin
# has access to the agent AND has bypass on everything.
ADMIN_EXPECTED_VISIBLE: dict[tuple[str, str, str], bool] = {
    # Admin + Org A agent: every variant attached must be listed for the admin.
    ("platform_admin", "A", "global_authenticated"): True,
    ("platform_admin", "A", "global_role_gated_empty"): True,
    ("platform_admin", "A", "global_role_gated"): True,
    ("platform_admin", "A", "org_a_authenticated"): True,
    ("platform_admin", "A", "org_a_role_gated"): True,
    ("platform_admin", "A", "org_b_authenticated"): True,  # cross-org, authenticated
    ("platform_admin", "A", "org_b_role_gated"): True,     # THE BUG — cross-org + role-gated
}


# Non-admin matrix: an Org A user with ``role_x`` granted, calling the same
# Org A agent. Visibility rules differ from admin in two important ways:
#
# * Cross-org workflows (in_org="B") become invisible — no superuser bypass
#   in the org-scope check, and the user's org_id != workflow.organization_id.
# * Role-gated-with-no-roles becomes invisible — the
#   ``ROLE_BASED with no roles == superuser only`` rule kicks in.
#
# The org_a_role_gated row is the bug reproducer for jack@musick.gg: visible
# in the listing path (middleware compares role names from JWT claims) but
# *not executable* before the UUID coercion fix because the executor's
# WorkflowRepository.get() compares Workflow.organization_id (UUID) to
# self.org_id (str from JWT claim) and silently returns None.
ORG_A_USER_EXPECTED_VISIBLE: dict[tuple[str, str, str], bool] = {
    ("org_a_user_with_role_x", "A", "global_authenticated"): True,
    ("org_a_user_with_role_x", "A", "global_role_gated_empty"): False,
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
                return _json.loads(line[len("data:"):].strip())
        raise AssertionError(f"SSE response had no data line: {raw[:300]}")
    return _json.loads(raw)


def _candidate_tool_names_for_workflow(workflow: dict) -> set[str]:
    """Return the set of names a workflow could be registered as in MCP.

    The registration table (``_WORKFLOW_ID_TO_TOOL_NAME``) lives in the API
    process, not the test-runner process, so we can't read it directly.
    Recreate the normalization rule from
    ``src.services.mcp_server.server._normalize_tool_name`` and accept either
    the raw name or the normalized one.
    """
    import re as _re

    name = workflow["name"]
    normalized = _re.sub(
        r"[^a-z0-9_]",
        "",
        _re.sub(r"[\s\-]+", "_", name.lower()),
    ).strip("_")
    return {normalized, name}


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
        json={"name": name, "permissions": {}},
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
    """Customer-org user with the named Platform Admin role, but no scope bypass.

    Agent-management routes intentionally recognize this role as an admin grant.
    Organization impersonation does not: only token superusers and provider-org
    callers receive the canonical scope bypass.
    """
    role_resp = e2e_client.post(
        "/api/roles",
        headers=platform_admin.headers,
        json={"name": "Platform Admin", "permissions": {}},
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
            org_a["id"] if variant.in_org == "A"
            else org_b["id"] if variant.in_org == "B"
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
            e2e_client.delete(
                f"/api/agents/{agent['id']}", headers=admin_headers
            )
        except Exception:
            logger.exception("matrix teardown: delete agent failed")
        for wf in workflows_by_key.values():
            try:
                e2e_client.delete(
                    f"/api/workflows/{wf['id']}?force=true",
                    headers=admin_headers,
                )
            except Exception:
                logger.exception(
                    "matrix teardown: delete workflow %s failed", wf["id"]
                )


# =============================================================================
# The matrix
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
class TestMcpToolAccessMatrix:
    """Pin each caller and workflow variant at the MCP HTTP boundary."""

    @pytest.mark.parametrize(
        "variant_key",
        [k for (caller, _agent_org, k) in ADMIN_EXPECTED_VISIBLE if caller == "platform_admin"],
        ids=lambda k: f"admin__mcp_call__{k}",
    )
    async def test_platform_admin_can_execute_tool_via_mcp_http(
        self,
        variant_key: str,
        seeded_agent_with_tools: dict[str, Any],
        e2e_client,
        platform_admin,
    ) -> None:
        """Admin must be able to *execute* every tool listed for an accessible agent.

        ``tools/list`` and ``tools/call`` go through different code paths
        (middleware filter vs. workflow tool execution -> WorkflowRepository
        scoping). The reported failure mode for jack@musick.gg was
        "tool listed in Claude, but `tools/call` returns 'Workflow not found'"
        — the listing path passed while the execute path failed because
        ``OrgScopedRepository.get(id=...)`` compared a UUID column to a JWT
        string and silently returned None. This test pins both paths.

        For every variant where the admin is expected to *see* the tool,
        executing it must also succeed (no 'Error: Workflow X not found',
        no JSON-RPC error envelope). The tool body itself returns 'ok'.
        """
        agent = seeded_agent_with_tools["agent"]
        workflow = seeded_agent_with_tools["workflows_by_key"][variant_key]
        mcp_headers = {
            "Authorization": f"Bearer {platform_admin.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }

        # Find the actual tool name FastMCP registered the workflow under by
        # asking the server. Avoids guessing wrong when normalization rules
        # change. tools/list is already covered above so we can rely on it.
        list_resp = e2e_client.post(
            f"/mcp/{agent['id']}",
            headers=mcp_headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {},
            },
        )
        assert list_resp.status_code == 200, (
            f"tools/list precondition failed: {list_resp.status_code} {list_resp.text[:300]}"
        )
        list_payload = _parse_mcp_response(list_resp)
        candidate_names = _candidate_tool_names_for_workflow(workflow)
        registered_tool_name: str | None = None
        for tool in list_payload["result"].get("tools", []):
            if tool.get("name") in candidate_names:
                registered_tool_name = tool.get("name")
                break
        assert registered_tool_name is not None, (
            f"tools/list did not return a name matching candidates "
            f"{candidate_names}; cannot execute. Got: "
            f"{[t.get('name') for t in list_payload['result'].get('tools', [])]}"
        )

        call_resp = e2e_client.post(
            f"/mcp/{agent['id']}",
            headers=mcp_headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": registered_tool_name, "arguments": {}},
            },
        )
        assert call_resp.status_code == 200, (
            f"MCP tools/call HTTP failed: status={call_resp.status_code} "
            f"body={call_resp.text[:500]}"
        )

        payload = _parse_mcp_response(call_resp)

        # JSON-RPC envelope-level error: middleware denied the call.
        # That's a different failure mode from the bug we're testing; surface
        # it with a clear message rather than letting the next assert misfire.
        assert "error" not in payload, (
            f"MCP middleware rejected tools/call for variant={variant_key}: "
            f"{payload['error']}"
        )

        result = payload.get("result")
        assert result is not None, f"tools/call returned no result: {payload}"

        # The fix path: result.isError must be False AND content must not
        # be the 'Workflow not found' error string from
        # _execute_workflow_tool_impl. Either of those signals the executor
        # ran but its lookup returned None.
        is_error = result.get("isError", False)
        content_blocks = result.get("content", [])
        content_text = " ".join(
            block.get("text", "") for block in content_blocks if isinstance(block, dict)
        )

        assert not is_error, (
            f"tools/call returned isError=True for variant={variant_key}, "
            f"tool={registered_tool_name!r}: content={content_text[:300]}"
        )
        assert "Workflow" not in content_text or "not found" not in content_text, (
            f"tools/call returned 'Workflow not found' for variant={variant_key}, "
            f"tool={registered_tool_name!r}. This is the jack@musick.gg bug: "
            f"executor's WorkflowRepository.get() returned None for an "
            f"accessible workflow. Content: {content_text[:300]}"
        )

        # The seeded tool body returns the literal string 'ok'.
        assert "ok" in content_text, (
            f"tools/call did not return expected 'ok' for variant={variant_key}, "
            f"tool={registered_tool_name!r}. Content: {content_text[:300]}"
        )

    # =========================================================================
    # Non-admin caller: Org A user with role_x. This is where the
    # jack@musick.gg bug actually surfaced — admin bypass hides the
    # UUID/string compare. A real org user with role_x assigned trips it.
    # =========================================================================

    @pytest.mark.parametrize(
        "variant_key",
        [k for (caller, _agent_org, k) in ORG_A_USER_EXPECTED_VISIBLE
         if caller == "org_a_user_with_role_x"
         and ORG_A_USER_EXPECTED_VISIBLE[(caller, _agent_org, k)]],
        ids=lambda k: f"org_a_user__call__{k}",
    )
    async def test_org_a_user_can_execute_authorized_tools_via_mcp_http(
        self,
        variant_key: str,
        seeded_agent_with_tools: dict[str, Any],
        e2e_client,
        org_a_user_with_role_x,
    ) -> None:
        """Non-admin Org A user: tools/call must succeed for every visible tool.

        This is the jack@musick.gg reproducer. Without the UUID coercion fix
        in OrgScopedRepository.__init__ + MCPContext.__post_init__, the
        ``org_a_role_gated`` row fails with 'Workflow not found' because:
          - Workflow.organization_id is a UUID (DB column)
          - MCPContext.org_id is a string (JWT claim)
          - OrgScopedRepository.get(id=...) compares them with ``==``
          - ``UUID == str`` is False even when they represent the same value
          - In-scope check fails -> get returns None -> executor reports
            'Error: Workflow ... not found'
        """
        agent = seeded_agent_with_tools["agent"]
        workflow = seeded_agent_with_tools["workflows_by_key"][variant_key]

        mcp_headers = {
            "Authorization": f"Bearer {org_a_user_with_role_x.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }

        # Look up registered name via tools/list; same approach as admin test.
        list_resp = e2e_client.post(
            f"/mcp/{agent['id']}",
            headers=mcp_headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {},
            },
        )
        assert list_resp.status_code == 200, (
            f"tools/list precondition failed: {list_resp.status_code} "
            f"{list_resp.text[:300]}"
        )
        list_payload = _parse_mcp_response(list_resp)
        candidate_names = _candidate_tool_names_for_workflow(workflow)
        registered_tool_name: str | None = None
        for tool in list_payload["result"].get("tools", []):
            if tool.get("name") in candidate_names:
                registered_tool_name = tool.get("name")
                break
        assert registered_tool_name is not None, (
            f"tools/list did not return the expected workflow for variant="
            f"{variant_key}. Candidates: {candidate_names}, "
            f"returned: {[t.get('name') for t in list_payload['result'].get('tools', [])]}."
        )

        call_resp = e2e_client.post(
            f"/mcp/{agent['id']}",
            headers=mcp_headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": registered_tool_name, "arguments": {}},
            },
        )
        assert call_resp.status_code == 200, (
            f"MCP tools/call HTTP failed: status={call_resp.status_code} "
            f"body={call_resp.text[:500]}"
        )

        payload = _parse_mcp_response(call_resp)
        assert "error" not in payload, (
            f"MCP middleware rejected tools/call for variant={variant_key}: "
            f"{payload['error']}"
        )

        result = payload.get("result")
        assert result is not None, f"tools/call returned no result: {payload}"

        is_error = result.get("isError", False)
        content_blocks = result.get("content", [])
        content_text = " ".join(
            block.get("text", "") for block in content_blocks if isinstance(block, dict)
        )

        assert not is_error, (
            f"tools/call returned isError=True for variant={variant_key}, "
            f"tool={registered_tool_name!r}: content={content_text[:300]}"
        )
        # Pin the specific failure shape so a regression names the bug class.
        assert not ("Workflow" in content_text and "not found" in content_text), (
            f"BUG REPRODUCED: tools/call returned 'Workflow not found' for "
            f"variant={variant_key}, tool={registered_tool_name!r}. This is "
            f"the jack@musick.gg failure. Likely cause: "
            f"OrgScopedRepository.get() returned None due to UUID/string "
            f"comparison failure. Content: {content_text[:300]}"
        )
        assert "ok" in content_text, (
            f"tools/call did not return expected 'ok' for variant={variant_key}, "
            f"tool={registered_tool_name!r}. Content: {content_text[:300]}"
        )

    @pytest.mark.parametrize(
        "variant_key",
        [k for (caller, _agent_org, k) in ORG_A_USER_EXPECTED_VISIBLE
         if caller == "org_a_user_with_role_x"
         and not ORG_A_USER_EXPECTED_VISIBLE[(caller, _agent_org, k)]],
        ids=lambda k: f"org_a_user__deny__{k}",
    )
    async def test_org_a_user_denied_for_unauthorized_tools_via_mcp_http(
        self,
        variant_key: str,
        seeded_agent_with_tools: dict[str, Any],
        e2e_client,
        platform_admin,
        org_a_user_with_role_x,
    ) -> None:
        """Symmetric deny test: invisible tools must also reject tools/call.

        Hard rule from product: a user in Org A must NOT access anything in
        Org B regardless of access_level or role-name match. Even if the
        list filter gets bypassed (regression, race, name guess), tools/call
        must reject. This test pins both layers — defense in depth.

        Strategy: use the admin's tools/list response to discover the real
        registered name for each variant — bypassing the user's filter — then
        invoke tools/call as the Org A user with that name. The call must be
        denied via one of:
          * a JSON-RPC ``error`` envelope (middleware rejected the call),
          * an ``isError=true`` result (executor returned an error),
          * a result whose content doesn't contain ``"ok"`` (executor's repo
            lookup returned None and produced "Workflow ... not found").

        The ONE shape that's never acceptable: a successful ``"ok"`` response.
        """
        agent = seeded_agent_with_tools["agent"]
        workflow = seeded_agent_with_tools["workflows_by_key"][variant_key]

        # Use admin to learn the real registered name. Admin sees everything,
        # so this is the ground-truth tool name FastMCP exposes.
        admin_headers = {
            "Authorization": f"Bearer {platform_admin.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        admin_list = e2e_client.post(
            f"/mcp/{agent['id']}",
            headers=admin_headers,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )
        assert admin_list.status_code == 200
        admin_payload = _parse_mcp_response(admin_list)
        candidate_names = _candidate_tool_names_for_workflow(workflow)
        registered_tool_name: str | None = None
        for tool in admin_payload["result"].get("tools", []):
            if tool.get("name") in candidate_names:
                registered_tool_name = tool.get("name")
                break
        assert registered_tool_name is not None, (
            f"Admin couldn't see the workflow either; fixture is broken. "
            f"Candidates: {candidate_names}"
        )

        user_headers = {
            "Authorization": f"Bearer {org_a_user_with_role_x.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }

        # First layer of defense: the user's tools/list must not even expose
        # the name. Listing leaks workflow names/descriptions/parameter
        # schemas — sensitive metadata even if the call would later deny.
        user_list_resp = e2e_client.post(
            f"/mcp/{agent['id']}",
            headers=user_headers,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )
        assert user_list_resp.status_code == 200
        user_list = _parse_mcp_response(user_list_resp)
        user_visible_names = {
            t.get("name") for t in user_list["result"].get("tools", [])
        }
        assert registered_tool_name not in user_visible_names, (
            f"INFO DISCLOSURE: variant={variant_key} should be denied for "
            f"org_a_user_with_role_x but appears in their tools/list. "
            f"Workflow id={workflow['id']}, name={workflow['name']!r}, "
            f"workflow_org_id={workflow.get('organization_id')!r}, "
            f"registered_tool_name={registered_tool_name!r}. "
            f"Listing response leaks name/description/parameter schema "
            f"across the org boundary. Even if the executor denies the "
            f"actual call, this is sensitive metadata — fix in "
            f"MCPToolAccessService."
        )

        # Second layer of defense: even if listing leaks, tools/call must
        # reject. This is the belt to the listing's suspenders.
        call_resp = e2e_client.post(
            f"/mcp/{agent['id']}",
            headers=user_headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": registered_tool_name, "arguments": {}},
            },
        )
        assert call_resp.status_code == 200, (
            f"MCP tools/call HTTP failed: status={call_resp.status_code} "
            f"body={call_resp.text[:500]}"
        )

        payload = _parse_mcp_response(call_resp)
        if "error" in payload:
            return  # middleware denied — correct
        result = payload.get("result")
        if result is None:
            return  # pathological but not a leak
        is_error = result.get("isError", False)
        content_blocks = result.get("content", [])
        content_text = " ".join(
            block.get("text", "") for block in content_blocks if isinstance(block, dict)
        )
        if is_error:
            return  # executor denied — correct

        assert "ok" not in content_text, (
            f"CROSS-TENANT LEAK: variant={variant_key} should be denied for "
            f"org_a_user_with_role_x but tools/call returned 'ok'. "
            f"Workflow id={workflow['id']}, name={workflow['name']!r}, "
            f"tool={registered_tool_name!r}, "
            f"workflow_org_id={workflow.get('organization_id')!r}. "
            f"Content: {content_text[:300]}"
        )

    @pytest.mark.parametrize("seeded_agent_with_tools", ["org_b_role_gated"], indirect=True)
    async def test_provider_org_user_can_list_and_call_cross_org_role_tool(
        self,
        seeded_agent_with_tools: dict[str, Any],
        e2e_client,
        provider_org_user,
    ) -> None:
        """A provider-org non-superuser receives the canonical scope bypass."""
        agent = seeded_agent_with_tools["agent"]
        workflow = seeded_agent_with_tools["workflows_by_key"]["org_b_role_gated"]
        headers = {
            "Authorization": f"Bearer {provider_org_user.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }

        list_resp = e2e_client.post(
            f"/mcp/{agent['id']}",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )
        assert list_resp.status_code == 200, list_resp.text
        list_payload = _parse_mcp_response(list_resp)
        candidate_names = _candidate_tool_names_for_workflow(workflow)
        registered_name = next(
            (
                tool.get("name")
                for tool in list_payload["result"].get("tools", [])
                if tool.get("name") in candidate_names
            ),
            None,
        )
        assert registered_name is not None, (
            "provider-org caller did not receive cross-org role-gated tool; "
            f"candidates={candidate_names}"
        )

        call_resp = e2e_client.post(
            f"/mcp/{agent['id']}",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": registered_name, "arguments": {}},
            },
        )
        assert call_resp.status_code == 200, call_resp.text
        call_payload = _parse_mcp_response(call_resp)
        assert "error" not in call_payload, call_payload
        result = call_payload.get("result")
        assert result is not None and not result.get("isError", False), call_payload
        content_text = " ".join(
            block.get("text", "")
            for block in result.get("content", [])
            if isinstance(block, dict)
        )
        assert "ok" in content_text, content_text

    @pytest.mark.parametrize("seeded_agent_with_tools", [None], indirect=True)
    async def test_provider_gateway_discovery_requires_explicit_scope_or_target(
        self,
        seeded_agent_with_tools: dict[str, Any],
        e2e_client,
        provider_org_user,
    ) -> None:
        """Provider bypass applies to exact targets, not unscoped discovery."""
        agent = seeded_agent_with_tools["agent"]
        headers = {
            "Authorization": f"Bearer {provider_org_user.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }

        tools_response = e2e_client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 0,
                "method": "tools/list",
                "params": {},
            },
        )
        assert tools_response.status_code == 200, tools_response.text
        tools_payload = _parse_mcp_response(tools_response)
        search_tool = next(
            tool
            for tool in tools_payload["result"]["tools"]
            if tool["name"] == "bifrost_search_capabilities"
        )
        assert search_tool["inputSchema"]["properties"]["discovery_scope"][
            "enum"
        ] == ["accessible", "all"]

        search_response = e2e_client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "bifrost_search_capabilities",
                    "arguments": {"query": agent["name"]},
                },
            },
        )
        assert search_response.status_code == 200, search_response.text
        search_payload = _parse_mcp_response(search_response)
        search_result = search_payload["result"]
        assert not search_result.get("isError", False), search_payload
        search_data = search_result.get("structuredContent")
        assert isinstance(search_data, dict), search_payload
        assert agent["id"] not in {
            item["id"] for item in search_data.get("agents", [])
        }

        all_response = e2e_client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "bifrost_search_capabilities",
                    "arguments": {
                        "query": agent["name"],
                        "discovery_scope": "all",
                    },
                },
            },
        )
        assert all_response.status_code == 200, all_response.text
        all_payload = _parse_mcp_response(all_response)
        all_result = all_payload["result"]
        all_data = all_result.get("structuredContent")
        assert isinstance(all_data, dict), all_payload
        assert agent["id"] in {item["id"] for item in all_data.get("agents", [])}

        explicit_response = e2e_client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "bifrost_search_capabilities",
                    "arguments": {"agent_id": agent["id"]},
                },
            },
        )
        assert explicit_response.status_code == 200, explicit_response.text
        explicit_payload = _parse_mcp_response(explicit_response)
        explicit_result = explicit_payload["result"]
        assert not explicit_result.get("isError", False), explicit_payload
        explicit_data = explicit_result.get("structuredContent")
        assert isinstance(explicit_data, dict), explicit_payload
        assert "agents" in explicit_data, explicit_data
        assert explicit_data["agents"][0]["id"] == agent["id"]

    @pytest.mark.parametrize("seeded_agent_with_tools", ["org_b_role_gated"], indirect=True)
    async def test_customer_org_platform_admin_cannot_list_or_call_cross_org_tool(
        self,
        seeded_agent_with_tools: dict[str, Any],
        e2e_client,
        platform_admin,
        customer_org_platform_admin,
    ) -> None:
        """A named admin role outside the provider org never grants impersonation."""
        agent = seeded_agent_with_tools["agent"]
        workflow = seeded_agent_with_tools["workflows_by_key"]["org_b_role_gated"]
        admin_headers = {
            "Authorization": f"Bearer {platform_admin.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        admin_list = e2e_client.post(
            f"/mcp/{agent['id']}",
            headers=admin_headers,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )
        assert admin_list.status_code == 200, admin_list.text
        candidates = _candidate_tool_names_for_workflow(workflow)
        registered_name = next(
            (
                tool.get("name")
                for tool in _parse_mcp_response(admin_list)["result"].get("tools", [])
                if tool.get("name") in candidates
            ),
            None,
        )
        assert registered_name is not None, "fixture admin could not resolve tool name"

        customer_headers = {
            "Authorization": f"Bearer {customer_org_platform_admin.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        all_search = e2e_client.post(
            "/mcp",
            headers=customer_headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "bifrost_search_capabilities",
                    "arguments": {
                        "query": agent["name"],
                        "discovery_scope": "all",
                    },
                },
            },
        )
        assert all_search.status_code == 200, all_search.text
        all_search_payload = _parse_mcp_response(all_search)
        all_search_data = all_search_payload["result"].get("structuredContent")
        assert isinstance(all_search_data, dict), all_search_payload
        assert all_search_data["code"] == "IMPERSONATION_FORBIDDEN"

        customer_list = e2e_client.post(
            f"/mcp/{agent['id']}",
            headers=customer_headers,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )
        assert customer_list.status_code == 200, customer_list.text
        visible_names = {
            tool.get("name")
            for tool in _parse_mcp_response(customer_list)["result"].get("tools", [])
        }
        assert registered_name not in visible_names, (
            "customer-org Platform Admin role leaked cross-org tool metadata"
        )

        call_resp = e2e_client.post(
            f"/mcp/{agent['id']}",
            headers=customer_headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": registered_name, "arguments": {}},
            },
        )
        assert call_resp.status_code == 200, call_resp.text
        payload = _parse_mcp_response(call_resp)
        if "error" in payload:
            return
        result = payload.get("result")
        assert result is None or result.get("isError", False) or not any(
            "ok" in block.get("text", "")
            for block in result.get("content", [])
            if isinstance(block, dict)
        ), "customer-org Platform Admin role executed a cross-org tool"
