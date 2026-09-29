"""Tests for the R2a-1 access list.

Proves the access list is complete (every REST route, WebSocket route, and
MCP tool has exactly one entry, and no entry points at a route/tool that no
longer exists), that each entry's ``current_gate`` matches ONLY what the
route's dependency tree mechanically enforces today (never narrowed by an
inline check — that's ``inline_checks``' job), that every ``inline_checks``
token is actually reachable from the handler's source, that
permission-class entries are internally consistent and agree with the
operation catalog, that MCP tools inherit their bound REST route's entry,
that every route/tool admitting provider-org non-admins beyond a customer
member (``engine_or_bypass`` gate, or a ``has_scope_bypass``/
``mcp_write_scope_bypass`` inline check) records an ``intended_change``,
that the generated JSON projection is fresh, and that no write route in the
personal/execute/own_private_agent classes has snuck onto a
platform-managed entity outside the reviewed allow-list.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from starlette.routing import WebSocketRoute

from src.core import auth as auth_mod
from src.main import app
from src.models.contracts.access_list import (
    INLINE_CHECK_TOKENS,
    AccessClass,
    CurrentGate,
)
from src.services.access_list import ACCESS_LIST
from src.services.mcp_server.server import get_system_tools
from src.services.operation_catalog import OPERATION_CATALOG

_API_ROOT = Path(__file__).resolve().parents[2]

# The dependency-tree functions that fully define `current_gate`. Matched by
# identity, never by name-in-string.
_GATE_FUNCS = {
    auth_mod.get_current_user: CurrentGate.AUTHENTICATED,
    auth_mod.get_current_active_user: CurrentGate.AUTHENTICATED,
    auth_mod.get_execution_context: CurrentGate.AUTHENTICATED,
    auth_mod.get_current_superuser: CurrentGate.SUPERUSER,
    auth_mod.get_current_engine_or_bypass_user: CurrentGate.ENGINE_OR_BYPASS,
}

# JWT/token-minting helpers embed principal fields (is_superuser, ...) as
# claims, not as authorization checks — excluded from the inline-check scan
# so claim construction isn't mistaken for a check.
_NOISE_FUNC_NAMES = {
    "create_access_token",
    "create_embed_access_token",
    "mint_service_token",
    "mint_engine_token",
    "decode_token",
}

# The two tokens that specifically indicate a provider-org bypass (as
# opposed to a platform-admin-only check): calling has_scope_bypass or
# mcp_write_scope_bypass admits a provider-org non-admin, not just a true
# platform admin.
_PROVIDER_BYPASS_TOKENS = {"has_scope_bypass", "mcp_write_scope_bypass"}


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
    """The gate the route's dependency tree enforces — and ONLY that.

    Matches dependency callables by identity (not by name-in-string), and
    recurses the whole tree so a gate nested behind another dependency is
    still found. This is never narrowed by an inline check in the handler
    body; that's a separate, additive fact (see ``_inline_checks``).
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


def _custom_dependency_callables(route: APIRoute) -> list[object]:
    """Non-canonical dependency callables in the tree (e.g. a custom auth
    dependency that does more than the four canonical resolvers, such as
    sdk_modules.py's ``_module_source_caller``)."""
    found: list[object] = []
    seen: set[int] = set()

    def walk(dependant: object) -> None:
        call = getattr(dependant, "call", None)
        if (
            call not in _GATE_FUNCS
            and callable(call)
            and inspect.isfunction(call)
        ):
            mod = getattr(call, "__module__", "") or ""
            if (mod.startswith("src.") or mod.startswith("shared.")) and id(call) not in seen:
                seen.add(id(call))
                found.append(call)
        for sub in getattr(dependant, "dependencies", ()):
            walk(sub)

    walk(route.dependant)
    return found


def _local_imports(tree: ast.AST) -> dict[str, tuple[str, str]]:
    imports: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                local = alias.asname or alias.name
                imports[local] = (node.module, alias.name)
    return imports


def _called_names(tree: ast.AST) -> set[str]:
    return {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def _reachable_sources(func: object, depth: int = 1, _visited: set[int] | None = None) -> list[str]:
    """The function's own source, plus up to ``depth`` hops of calls it
    makes to a locally-imported or module-global function that lives in
    ``shared.``/``src.`` — the "service it calls one level down" the R2a-1
    spec asks the inline-check scan to cover. Token-minting helpers are
    excluded (see ``_NOISE_FUNC_NAMES``); classes are not descended into
    (too coarse — an unrelated method would pollute the scan).
    """
    if _visited is None:
        _visited = set()
    key = id(func)
    if key in _visited:
        return []
    _visited.add(key)
    if getattr(func, "__name__", "") in _NOISE_FUNC_NAMES:
        return []
    try:
        source = inspect.getsource(func)
    except (OSError, TypeError):
        return []
    sources = [source]
    if depth <= 0:
        return sources
    try:
        tree = ast.parse(textwrap.dedent(source))
    except SyntaxError:
        return sources
    local_imports = _local_imports(tree)
    called = _called_names(tree)
    func_globals = getattr(func, "__globals__", {})
    for name in called:
        target = None
        if name in local_imports:
            module_name, orig_name = local_imports[name]
            if module_name.startswith("shared") or module_name.startswith("src."):
                try:
                    mod = importlib.import_module(module_name)
                    target = getattr(mod, orig_name, None)
                except Exception:
                    target = None
        elif name in func_globals:
            candidate = func_globals[name]
            mod_attr = getattr(candidate, "__module__", "") or ""
            if mod_attr.startswith("shared") or mod_attr.startswith("src."):
                target = candidate
        if target is not None and inspect.isfunction(target):
            sources.extend(_reachable_sources(target, depth - 1, _visited))
    return sources


def _inline_checks(route: APIRoute) -> tuple[str, ...]:
    blob_parts = _reachable_sources(route.endpoint, depth=1)
    for dep_func in _custom_dependency_callables(route):
        blob_parts.extend(_reachable_sources(dep_func, depth=1))
    blob = "\n".join(blob_parts)
    return tuple(sorted({t for t in INLINE_CHECK_TOKENS if re.search(rf"\b{re.escape(t)}\b", blob)}))


def _mcp_tool_ids() -> set[str]:
    return {str(tool["id"]) for tool in get_system_tools()}


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
        missing = []
        for route in app.routes:
            if isinstance(route, WebSocketRoute):
                path = _openapi_path(route.path)
                if ("WS", path) not in entries_by_key:
                    missing.append(path)
        assert not missing, f"WebSocket routes missing an access-list entry: {missing}"

    def test_no_stale_rest_entries(self, rest_routes) -> None:
        live = {(method, path) for method, path, _route in rest_routes}
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
    def test_rest_current_gate_matches_dependency_tree_only(self, rest_routes, entries_by_key) -> None:
        """current_gate must equal the DEPENDENCY-derived gate — never
        narrowed by an inline check. A route whose handler does an extra
        is_superuser-style check on top of `authenticated` still records
        current_gate=authenticated; the extra check lives in inline_checks.
        """
        mismatches = []
        for method, path, route in rest_routes:
            entry = entries_by_key.get((method, path))
            if entry is None:
                continue
            actual = _dependency_gate(route)
            if actual != entry.current_gate:
                mismatches.append((method, path, entry.current_gate, actual))
        assert not mismatches, (
            "access-list current_gate disagrees with the route's actual dependency gate "
            f"(method, path, recorded, actual): {mismatches}"
        )

    def test_mcp_current_gate_is_authenticated(self, entries_by_key) -> None:
        """MCP tools all sit behind the FastMCP OAuth session — the transport-
        level floor is `authenticated` for every one of them; anything
        narrower is an inline_checks fact, not a different current_gate."""
        wrong = [
            entry.mcp_tool
            for entry in ACCESS_LIST
            if entry.mcp_tool is not None and entry.current_gate != CurrentGate.AUTHENTICATED
        ]
        assert not wrong, f"MCP entries with current_gate != authenticated: {wrong}"

    def test_inline_checks_tokens_are_reachable_from_source(self, rest_routes) -> None:
        """Every token in inline_checks must actually be found by the same
        one-hop reachable-source scan the generator uses — so a removed
        check fails this test, not just a stale docstring."""
        by_key = {(m, p): r for m, p, r in rest_routes}
        mismatches = []
        for entry in ACCESS_LIST:
            if not entry.inline_checks or entry.method is None:
                continue
            route = by_key.get((entry.method, entry.path))
            if route is None:
                continue
            actual = set(_inline_checks(route))
            missing = set(entry.inline_checks) - actual
            if missing:
                mismatches.append((entry.method, entry.path, sorted(missing)))
        assert not mismatches, (
            f"inline_checks tokens not found in reachable source (method, path, missing): {mismatches}"
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

    def test_permission_domains_are_in_the_closed_vocabulary(self) -> None:
        # AccessEntry's own validator already enforces this at construction
        # time (see PERMISSION_DOMAINS); re-assert here for the same reason
        # as test_permission_entries_have_permission_and_boundary above.
        from src.models.contracts.permissions import PERMISSION_DOMAINS

        unknown = []
        for entry in ACCESS_LIST:
            if entry.permission is None:
                continue
            domain, _, _action = entry.permission.rpartition(".")
            if domain not in PERMISSION_DOMAINS:
                unknown.append((entry.key, domain))
        assert not unknown, f"permission domain outside PERMISSION_DOMAINS: {unknown}"

    # A (domain, current_gate) group is allowed mixed boundaries only when the
    # domain is one of the intentionally-collapsed multi-resource buckets
    # (settings/metrics/platform) or genuinely holds two differently-scoped
    # sub-resources under the same gate — each entry's own `reason` states
    # which. Any OTHER (domain, gate) group must use one boundary throughout:
    # the same resource's read and write routes can't arbitrarily disagree.
    _ALLOWED_MIXED_BOUNDARY_GROUPS = {
        # Settings bucket: some sub-resources are inherently global (AI
        # pricing, branding), others inherently per-org (OAuth SSO config,
        # embed secrets, workflow signing keys) — see the domain description.
        ("settings", CurrentGate.SUPERUSER),
        # Metrics bucket: ROI reports are per-org; the rest (audit,
        # cross-org dashboards, scheduler diagnostics) are platform-wide.
        ("metrics", CurrentGate.SUPERUSER),
        # Platform bucket: the org-scoped external-service registry
        # (/api/services/*) sits alongside genuinely global maintenance/
        # packages/github/kubernetes/worker admin.
        ("platform", CurrentGate.SUPERUSER),
        # Roles domain: /api/roles/* is genuinely org-cascaded; the two
        # /api/users/{id}/roles|forms reads are flat global lookups with no
        # org filter (see their reason text).
        ("roles", CurrentGate.SUPERUSER),
        # required-instructions (settings, folded in above) has a
        # platform-wide GET/PUT and a deliberate per-org
        # /organizations/{organization_id} variant (see reason text).
    }

    def test_same_domain_same_gate_entries_share_one_boundary(self) -> None:
        groups: dict[tuple[str, CurrentGate], set[str]] = {}
        for entry in ACCESS_LIST:
            if entry.permission is None:
                continue
            domain, _, _action = entry.permission.rpartition(".")
            groups.setdefault((domain, entry.current_gate), set()).add(entry.boundary)
        unjustified = {
            key: boundaries
            for key, boundaries in groups.items()
            if len(boundaries) > 1 and key not in self._ALLOWED_MIXED_BOUNDARY_GROUPS
        }
        assert not unjustified, (
            "same (permission domain, current_gate) group uses more than one "
            f"boundary with no listed justification: {unjustified}"
        )


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


class TestIntendedChangeCoverage:
    """Any entry that admits a provider-org non-admin beyond a customer
    member — engine_or_bypass gate, or an inline has_scope_bypass /
    mcp_write_scope_bypass check — must say so via intended_change, unless
    its reason already states why permanent provider-org access is
    intended (none currently do)."""

    def test_bypass_admitting_entries_have_intended_change(self) -> None:
        missing = []
        for entry in ACCESS_LIST:
            admits_bypass = (
                entry.current_gate == CurrentGate.ENGINE_OR_BYPASS
                or bool(set(entry.inline_checks) & _PROVIDER_BYPASS_TOKENS)
            )
            if admits_bypass and not entry.intended_change:
                missing.append(entry.key)
        assert not missing, (
            f"entries admitting provider-org bypass with no intended_change: {missing}"
        )

    def test_intended_change_only_on_bypass_admitting_entries(self) -> None:
        """Catch drift the other way too: intended_change should not be set
        on an entry that doesn't actually admit bypass — it would be a
        stale note left over from a removed check."""
        extra = []
        for entry in ACCESS_LIST:
            admits_bypass = (
                entry.current_gate == CurrentGate.ENGINE_OR_BYPASS
                or bool(set(entry.inline_checks) & _PROVIDER_BYPASS_TOKENS)
            )
            if entry.intended_change and not admits_bypass:
                extra.append(entry.key)
        assert not extra, f"intended_change set without a bypass-admitting gate/check: {extra}"


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
    ("POST", "/api/oauth/connections"): "Own OAuth connection.",
    ("PUT", "/api/oauth/connections/{connection_name}"): "Own OAuth connection.",
    ("DELETE", "/api/oauth/connections/{connection_name}"): "Own OAuth connection.",
    ("POST", "/api/oauth/connections/{connection_name}/authorize"): "Own OAuth connection.",
    ("POST", "/api/oauth/connections/{connection_name}/cancel"): "Own OAuth connection.",
    ("POST", "/api/oauth/connections/{connection_name}/refresh"): "Own OAuth connection.",
    ("POST", "/api/oauth/callback/{connection_name}"): "Own OAuth connection.",
    ("POST", "/api/sdk/ai/complete"): "Workflow SDK call made during execution.",
    ("POST", "/api/sdk/ai/stream"): "Workflow SDK call made during execution.",
    ("POST", "/api/sdk/artifacts"): "Own execution-workspace artifact.",
    ("POST", "/api/sdk/artifacts/document"): "Own execution-workspace artifact.",
    ("POST", "/api/sdk/artifacts/spreadsheet"): "Own execution-workspace artifact.",
    ("POST", "/api/sdk/artifacts/text"): "Own execution-workspace artifact.",
    ("POST", "/api/sdk/artifacts/image"): "Own execution-workspace artifact.",
    ("POST", "/api/sdk/artifacts/video"): "Own execution-workspace artifact.",
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
    ("POST", "/api/agents/{agent_id}/tuning-session"): "Own private agent (tuning).",
    ("POST", "/api/agents/{agent_id}/tuning-session/dry-run"): "Own private agent (tuning).",
    ("POST", "/api/agents/{agent_id}/tuning-session/apply"): "Own private agent (tuning).",
    ("POST", "/api/agent-runs/{run_id}/rerun"): "Own/accessible agent run.",
    ("POST", "/api/agent-runs/{run_id}/cancel"): "Own/accessible agent run.",
    ("POST", "/api/agent-runs/{run_id}/dry-run"): "Own/accessible agent run.",
    ("POST", "/api/agent-runs/enqueue"): "Own agent run.",
    ("POST", "/api/agent-runs/execute"): "Own agent run.",
    ("POST", "/api/chat/conversations"): "Own chat conversation.",
    ("DELETE", "/api/chat/conversations/{conversation_id}"): "Own chat conversation.",
    ("POST", "/api/chat/runs"): "Own chat conversation.",
    ("POST", "/api/chat/runs/{run_id}/cancel"): "Own chat conversation.",
    ("PATCH", "/api/chat/artifacts/{attachment_id}"): "Own chat artifact.",
    ("DELETE", "/api/chat/artifacts/{attachment_id}"): "Own chat artifact.",
    ("POST", "/api/chat/conversations/{conversation_id}/attachments"): "Own chat conversation.",
    ("DELETE", "/api/chat/conversations/{conversation_id}/attachments/{attachment_id}"): "Own chat conversation.",
    ("POST", "/api/chat/conversations/{conversation_id}/messages"): "Own chat conversation.",
    ("POST", "/api/mcp/gateway/agents/{agent_id}/tools/{tool_ref}/execute"): "Executes a tool through the MCP gateway.",
    ("DELETE", "/api/me/mcp-connections/{connection_id}"): "Own MCP tool connection.",
    ("POST", "/api/platform-jobs/{job_id}/cancel"): "Own platform job (or any, for a platform admin).",
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
