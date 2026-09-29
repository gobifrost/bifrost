"""Tests for the R2a-1 access list.

Proves the access list is complete (every REST route and MCP tool has
exactly one entry, and no entry points at a route/tool that no longer
exists), that each entry's ``current_gate`` matches what the route's
dependency tree (or MCP tool's inline scope-bypass check) actually enforces
today, that permission-class entries are internally consistent and agree
with the operation catalog, that MCP tools inherit their bound REST route's
entry, that the generated JSON projection is fresh, and that no write
route in the personal/execute/own_private_agent classes has snuck onto a
platform-managed entity outside the reviewed allow-list.
"""

from __future__ import annotations

import inspect
import re
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.routing import APIRoute

from src.core import auth as auth_mod
from src.main import app
from src.models.contracts.access_list import AccessClass, CurrentGate
from src.services.access_list import ACCESS_LIST
from src.services.mcp_server.server import get_system_tools
from src.services.operation_catalog import OPERATION_CATALOG

_API_ROOT = Path(__file__).resolve().parents[2]
_REPO_ROOT = _API_ROOT.parent

_INLINE_TOKENS = ("is_superuser", "is_platform_admin", "has_scope_bypass", "is_provider_org")

_GATE_FUNCS = {
    auth_mod.get_current_user: CurrentGate.AUTHENTICATED,
    auth_mod.get_current_active_user: CurrentGate.AUTHENTICATED,
    auth_mod.get_execution_context: CurrentGate.AUTHENTICATED,
    auth_mod.get_current_superuser: CurrentGate.SUPERUSER,
    auth_mod.get_current_engine_or_bypass_user: CurrentGate.ENGINE_OR_BYPASS,
}


def _openapi_path(path: str) -> str:
    """Normalize a Starlette route path to the form OpenAPI publishes."""
    return re.sub(r"\{([^}:]+):[^}]+\}", r"{\1}", path)


def _rest_routes() -> list[tuple[str, str, APIRoute]]:
    rows = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in sorted(route.methods or ()):
            if method in ("HEAD", "OPTIONS"):
                continue
            rows.append((method, _openapi_path(route.path), route))
    return rows


def _dependency_gate(route: APIRoute) -> CurrentGate:
    """Derive the strongest gate actually enforced by the route's dependency tree.

    Matches dependency callables by identity (not by name-in-string), and
    recurses the whole tree so a gate nested behind another dependency is
    still found.
    """
    found: set[CurrentGate] = set()

    def walk(dependant: object) -> None:
        call = getattr(dependant, "call", None)
        if call in _GATE_FUNCS:
            found.add(_GATE_FUNCS[call])
        for sub in getattr(dependant, "dependencies", ()):
            walk(sub)

    walk(route.dependant)
    if CurrentGate.SUPERUSER in found:
        return CurrentGate.SUPERUSER
    if CurrentGate.ENGINE_OR_BYPASS in found:
        return CurrentGate.ENGINE_OR_BYPASS
    if CurrentGate.AUTHENTICATED in found:
        return CurrentGate.AUTHENTICATED
    return CurrentGate.NONE


def _inline_tokens(route: APIRoute) -> list[str]:
    try:
        source = inspect.getsource(route.endpoint)
    except (OSError, TypeError):
        return []
    return sorted(t for t in _INLINE_TOKENS if re.search(rf"\b{re.escape(t)}\b", source))


def _actual_gate(route: APIRoute) -> CurrentGate:
    base = _dependency_gate(route)
    if base != CurrentGate.NONE and _inline_tokens(route):
        return CurrentGate.INLINE
    return base


def _mcp_tool_ids() -> set[str]:
    return {str(tool["id"]) for tool in get_system_tools()}


def _catalog_by_rest() -> dict[tuple[str, str], object]:
    return {(op.rest.method, op.rest.path): op for op in OPERATION_CATALOG if op.rest}


def _catalog_by_mcp() -> dict[str, object]:
    return {op.mcp.name: op for op in OPERATION_CATALOG if op.mcp}


@pytest.fixture(scope="module")
def rest_routes() -> list[tuple[str, str, APIRoute]]:
    return _rest_routes()


@pytest.fixture(scope="module")
def entries_by_key() -> dict:
    return {entry.key: entry for entry in ACCESS_LIST}


class TestCompleteness:
    def test_no_duplicate_entries(self) -> None:
        keys = [entry.key for entry in ACCESS_LIST]
        assert len(keys) == len(set(keys)), "duplicate AccessEntry keys"

    def test_every_rest_route_has_an_entry(self, rest_routes, entries_by_key) -> None:
        missing = [
            (method, path)
            for method, path, _route in rest_routes
            if (method, path) not in entries_by_key
        ]
        assert not missing, f"REST routes missing an access-list entry: {missing}"

    def test_every_mcp_tool_has_an_entry(self, entries_by_key) -> None:
        missing = [name for name in _mcp_tool_ids() if name not in entries_by_key]
        assert not missing, f"MCP tools missing an access-list entry: {missing}"

    def test_websocket_routes_have_entries(self, entries_by_key) -> None:
        from starlette.routing import WebSocketRoute

        missing = []
        for route in app.routes:
            if isinstance(route, WebSocketRoute):
                path = _openapi_path(route.path)
                if ("WS", path) not in entries_by_key:
                    missing.append(path)
        assert not missing, f"WebSocket routes missing an access-list entry: {missing}"

    def test_no_stale_rest_entries(self, rest_routes) -> None:
        live = {(method, path) for method, path, _route in rest_routes}
        from starlette.routing import WebSocketRoute

        live |= {("WS", _openapi_path(r.path)) for r in app.routes if isinstance(r, WebSocketRoute)}
        stale = [
            (entry.method, entry.path)
            for entry in ACCESS_LIST
            if entry.method is not None and (entry.method, entry.path) not in live
        ]
        assert not stale, f"access-list entries point at routes that no longer exist: {stale}"

    def test_no_stale_mcp_entries(self) -> None:
        live = _mcp_tool_ids()
        stale = [
            entry.mcp_tool
            for entry in ACCESS_LIST
            if entry.mcp_tool is not None and entry.mcp_tool not in live
        ]
        assert not stale, f"access-list entries point at MCP tools that no longer exist: {stale}"


class TestGateAgreement:
    def test_rest_current_gate_matches_dependency_tree(self, rest_routes, entries_by_key) -> None:
        mismatches = []
        for method, path, route in rest_routes:
            entry = entries_by_key.get((method, path))
            if entry is None:
                continue
            actual = _actual_gate(route)
            if actual != entry.current_gate:
                mismatches.append((method, path, entry.current_gate, actual))
        assert not mismatches, (
            "access-list current_gate disagrees with the route's actual dependency gate "
            f"(method, path, recorded, actual): {mismatches}"
        )

    def test_inline_entries_source_actually_contains_a_check_token(self, rest_routes) -> None:
        by_key = {(m, p): r for m, p, r in rest_routes}
        missing_token = []
        for entry in ACCESS_LIST:
            if entry.current_gate != CurrentGate.INLINE or entry.method is None:
                continue
            route = by_key.get((entry.method, entry.path))
            if route is None:
                continue
            if not _inline_tokens(route):
                missing_token.append((entry.method, entry.path))
        assert not missing_token, (
            f"entries marked current_gate=inline but with no inline check token in source: {missing_token}"
        )


class TestConsistency:
    def test_permission_entries_have_permission_and_boundary(self) -> None:
        # AccessEntry's own validator already enforces this at construction
        # time; re-assert here so a future relaxation of the model doesn't
        # silently drop the guarantee.
        for entry in ACCESS_LIST:
            if entry.access_class == AccessClass.PERMISSION:
                assert entry.permission, entry
                assert entry.boundary, entry
            else:
                assert entry.permission is None, entry
                assert entry.boundary is None, entry

    def test_catalogued_permission_matches_action_scopes(self) -> None:
        catalog_by_id = {op.operation_id: op for op in OPERATION_CATALOG}
        mismatches = []
        for entry in ACCESS_LIST:
            if entry.operation_id is None or entry.access_class != AccessClass.PERMISSION:
                continue
            op = catalog_by_id.get(entry.operation_id)
            if op is None:
                mismatches.append((entry.operation_id, "no such catalog operation"))
                continue
            if entry.permission not in op.action_scopes:
                mismatches.append((entry.operation_id, entry.permission, op.action_scopes))
        assert not mismatches, f"permission not in catalogued action_scopes vocabulary: {mismatches}"


class TestMcpMatchesRest:
    def test_bound_mcp_tools_inherit_their_rest_entry(self, entries_by_key) -> None:
        catalog_by_mcp = _catalog_by_mcp()
        tool_ids = _mcp_tool_ids()
        mismatches = []
        for name in tool_ids:
            op = catalog_by_mcp.get(name)
            if op is None or op.rest is None:
                continue
            rest_key = (op.rest.method, op.rest.path)
            rest_entry = entries_by_key.get(rest_key)
            mcp_entry = entries_by_key.get(name)
            if rest_entry is None or mcp_entry is None:
                continue
            if (mcp_entry.access_class, mcp_entry.permission, mcp_entry.boundary) != (
                rest_entry.access_class,
                rest_entry.permission,
                rest_entry.boundary,
            ):
                mismatches.append((name, rest_key))
        assert not mismatches, f"MCP tool entry disagrees with its bound REST route's entry: {mismatches}"


class TestGeneratedJsonFreshness:
    def test_access_list_json_is_fresh(self) -> None:
        result = subprocess.run(
            [sys.executable, str(_API_ROOT / "scripts/generate_access_list.py"), "--check"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"docs/generated/access-list.json is stale — run "
            f"api/scripts/generate_access_list.py.\n{result.stdout}\n{result.stderr}"
        )


# ---------------------------------------------------------------------------
# User-base-role rule: no personal/execute/own_private_agent write route on a
# platform-managed entity, except this reviewed allow-list.
# ---------------------------------------------------------------------------
_REVIEWED_WRITE_ALLOWLIST: dict[tuple[str, str], str] = {
    ("POST", "/api/home/collections"): "Own home-dashboard collection.",
    ("PUT", "/api/home/collections/{collection_id}"): "Own home-dashboard collection.",
    ("DELETE", "/api/home/collections/{collection_id}"): "Own home-dashboard collection.",
    ("PUT", "/api/home/preferences/{resource_key}"): "Own home-dashboard preference.",
    ("POST", "/auth/logout"): "Own session.",
    ("POST", "/auth/revoke-all"): "Own sessions.",
    ("POST", "/auth/device/authorize"): "Own device-code authorization.",
    ("POST", "/auth/mfa/totp/setup"): "Own MFA enrollment.",
    ("POST", "/auth/mfa/totp/verify"): "Own MFA enrollment.",
    ("DELETE", "/auth/mfa"): "Own MFA enrollment.",
    ("POST", "/auth/mfa/recovery-codes/regenerate"): "Own MFA recovery codes.",
    ("DELETE", "/auth/mfa/trusted-devices/{device_id}"): "Own trusted device.",
    ("DELETE", "/auth/mfa/trusted-devices"): "Own trusted devices.",
    ("DELETE", "/auth/oauth/accounts/{provider}"): "Own linked OAuth account.",
    ("POST", "/auth/passkeys/register/options"): "Own passkey.",
    ("POST", "/auth/passkeys/register/verify"): "Own passkey.",
    ("DELETE", "/auth/passkeys/{passkey_id}"): "Own passkey.",
    ("POST", "/api/executions/{execution_id}/cancel"): "Cancel an own/accessible execution.",
    ("POST", "/api/workflows/execute"): "Run a workflow the caller can already reach.",
    ("POST", "/api/workflows/executions/{execution_id}/cancel"): "Cancel an own/accessible execution.",
    ("POST", "/api/forms/{form_id}/captcha/challenge"): "Form runtime — submitting the form.",
    ("POST", "/api/forms/{form_id}/submissions"): "Form runtime — submitting the form.",
    ("POST", "/api/forms/{form_id}/startup"): "Form runtime — loading the form.",
    ("POST", "/api/forms/{form_id}/fields/{field_name}/options"): "Form runtime — loading field options.",
    ("POST", "/api/forms/{form_id}/upload"): "Form runtime — uploading a submission attachment.",
    ("POST", "/api/platform-jobs/{job_id}/cancel"): "Cancel an own/accessible platform job.",
    ("POST", "/api/oauth/connections"): "Own OAuth connection.",
    ("PUT", "/api/oauth/connections/{connection_name}"): "Own OAuth connection.",
    ("DELETE", "/api/oauth/connections/{connection_name}"): "Own OAuth connection.",
    ("POST", "/api/oauth/connections/{connection_name}/authorize"): "Own OAuth connection.",
    ("POST", "/api/oauth/connections/{connection_name}/cancel"): "Own OAuth connection.",
    ("POST", "/api/oauth/connections/{connection_name}/refresh"): "Own OAuth connection.",
    ("POST", "/api/oauth/callback/{connection_name}"): "Own OAuth connection.",
    ("POST", "/api/sdk/config/get"): "Workflow SDK call made during execution.",
    ("POST", "/api/sdk/config/set"): "Workflow SDK call made during execution.",
    ("POST", "/api/sdk/config/list"): "Workflow SDK call made during execution.",
    ("POST", "/api/sdk/config/delete"): "Workflow SDK call made during execution.",
    ("POST", "/api/sdk/integrations/get"): "Workflow SDK call made during execution.",
    ("POST", "/api/sdk/integrations/list_mappings"): "Workflow SDK call made during execution.",
    ("POST", "/api/sdk/integrations/get_mapping"): "Workflow SDK call made during execution.",
    ("POST", "/api/sdk/integrations/upsert_mapping"): "Workflow SDK call made during execution.",
    ("POST", "/api/sdk/integrations/delete_mapping"): "Workflow SDK call made during execution.",
    ("POST", "/api/sdk/integrations/refresh_token"): "Workflow SDK call made during execution.",
    ("POST", "/api/sdk/artifacts"): "Workflow SDK call made during execution.",
    ("POST", "/api/sdk/artifacts/document"): "Workflow SDK call made during execution.",
    ("POST", "/api/sdk/artifacts/spreadsheet"): "Workflow SDK call made during execution.",
    ("POST", "/api/sdk/artifacts/text"): "Workflow SDK call made during execution.",
    ("POST", "/api/sdk/artifacts/image"): "Workflow SDK call made during execution.",
    ("POST", "/api/sdk/artifacts/video"): "Workflow SDK call made during execution.",
    ("POST", "/api/sdk/ai/complete"): "Workflow SDK call made during execution.",
    ("POST", "/api/sdk/ai/stream"): "Workflow SDK call made during execution.",
    ("POST", "/api/sdk/knowledge/search"): "Workflow SDK call made during execution.",
    ("POST", "/api/sdk/tables/create"): "Workflow SDK call made during execution.",
    ("POST", "/api/sdk/tables/list"): "Workflow SDK call made during execution.",
    ("DELETE", "/api/notifications/{notification_id}"): "Own notification.",
    ("PATCH", "/api/profile"): "Own profile.",
    ("POST", "/api/profile/avatar"): "Own profile.",
    ("DELETE", "/api/profile/avatar"): "Own profile.",
    ("POST", "/api/profile/password"): "Own profile.",
    ("POST", "/api/memory"): "Own memory entry.",
    ("POST", "/api/memory/search"): "Own memory entries.",
    ("DELETE", "/api/memory/{memory_id}"): "Own memory entry.",
    ("POST", "/api/agents"): "Own private agent.",
    ("PUT", "/api/agents/{agent_id}"): "Own private agent.",
    ("DELETE", "/api/agents/{agent_id}"): "Own private agent.",
    ("POST", "/api/agents/{agent_id}/promote"): "Own private agent.",
    ("POST", "/api/agents/{agent_id}/logo"): "Own private agent.",
    ("DELETE", "/api/agents/{agent_id}/logo"): "Own private agent.",
    ("POST", "/api/agent-runs/{run_id}/rerun"): "Own/accessible agent run.",
    ("POST", "/api/agent-runs/{run_id}/cancel"): "Own/accessible agent run.",
    ("POST", "/api/agent-runs/{run_id}/verdict"): "Own/accessible agent run.",
    ("DELETE", "/api/agent-runs/{run_id}/verdict"): "Own/accessible agent run.",
    ("POST", "/api/agent-runs/{run_id}/flag-conversation/message"): "Own/accessible agent run.",
    ("POST", "/api/agent-runs/{run_id}/regenerate-summary"): "Own/accessible agent run.",
    ("POST", "/api/agent-runs/{run_id}/dry-run"): "Own/accessible agent run.",
    ("POST", "/api/agent-runs/enqueue"): "Own agent run.",
    ("POST", "/api/agent-runs/execute"): "Own agent run.",
    ("POST", "/api/agents/{agent_id}/tuning-session"): "Interactive tuning preview on an accessible agent.",
    ("POST", "/api/agents/{agent_id}/tuning-session/dry-run"): "Interactive tuning preview on an accessible agent.",
    ("POST", "/api/agents/{agent_id}/tuning-session/apply"): "Interactive tuning preview on an accessible agent.",
    ("POST", "/api/chat/conversations"): "Own chat conversation.",
    ("DELETE", "/api/chat/conversations/{conversation_id}"): "Own chat conversation.",
    ("POST", "/api/chat/runs"): "Own chat conversation.",
    ("POST", "/api/chat/runs/{run_id}/cancel"): "Own chat conversation.",
    ("PATCH", "/api/chat/artifacts/{attachment_id}"): "Own chat artifact.",
    ("DELETE", "/api/chat/artifacts/{attachment_id}"): "Own chat artifact.",
    ("POST", "/api/chat/conversations/{conversation_id}/attachments"): "Own chat conversation.",
    ("DELETE", "/api/chat/conversations/{conversation_id}/attachments/{attachment_id}"): "Own chat conversation.",
    ("POST", "/api/chat/conversations/{conversation_id}/messages"): "Own chat conversation.",
    ("POST", "/api/integrations/{integration_id}/test"): "Live connectivity test, not a definition mutation.",
    ("POST", "/api/mcp/gateway/agents/{agent_id}/tools/{tool_ref}/execute"): "Executes a tool through the MCP gateway.",
    ("POST", "/api/events/emit"): "Triggers a platform event.",
    ("POST", "/api/mcp-connections"): "Own MCP tool connection.",
    ("PATCH", "/api/mcp-connections/{connection_id}"): "Own MCP tool connection.",
    ("DELETE", "/api/mcp-connections/{connection_id}"): "Own MCP tool connection.",
    ("PATCH", "/api/mcp-connections/{connection_id}/tools/{tool_id}"): "Own MCP tool connection.",
    ("POST", "/api/mcp-connections/{connection_id}/refresh-tools"): "Own MCP tool connection.",
    ("POST", "/api/mcp-connections/{connection_id}/connect"): "Own MCP tool connection.",
    ("DELETE", "/api/me/mcp-connections/{connection_id}"): "Own MCP tool connection.",
}


def test_personal_execute_own_agent_writes_are_all_on_the_reviewed_allowlist() -> None:
    write_methods = {"POST", "PUT", "PATCH", "DELETE"}
    narrow_classes = {
        AccessClass.PERSONAL,
        AccessClass.EXECUTE,
        AccessClass.OWN_PRIVATE_AGENT,
    }
    offenders = [
        (entry.method, entry.path)
        for entry in ACCESS_LIST
        if entry.access_class in narrow_classes
        and entry.method in write_methods
        and (entry.method, entry.path) not in _REVIEWED_WRITE_ALLOWLIST
    ]
    assert not offenders, (
        "personal/execute/own_private_agent write route not on the reviewed "
        f"allow-list — narrow the class or add it with a reason: {offenders}"
    )


def test_allowlist_has_no_unused_entries() -> None:
    actual = {
        (entry.method, entry.path)
        for entry in ACCESS_LIST
        if entry.access_class
        in {AccessClass.PERSONAL, AccessClass.EXECUTE, AccessClass.OWN_PRIVATE_AGENT}
        and entry.method in {"POST", "PUT", "PATCH", "DELETE"}
    }
    unused = set(_REVIEWED_WRITE_ALLOWLIST) - actual
    assert not unused, f"allow-list entries with no matching access-list route: {unused}"
