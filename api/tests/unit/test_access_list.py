"""Tests for the R2a-1 access list.

Proves the access list is complete (every REST route, WebSocket route, and
MCP tool has exactly one entry, and no entry points at a route/tool that no
longer exists), that each entry's ``current_gate`` matches ONLY what the
route's dependency tree mechanically enforces today (never narrowed by an
inline check — that's ``inline_checks``' job), that every ``inline_checks``
token is actually reachable from the handler's source, that entries naming
a permission are internally consistent and agree with the operation
catalog, that MCP tools inherit their bound REST route's entry,
that every route/tool admitting provider-org non-admins beyond a customer
member (``engine_or_bypass`` gate, or a ``has_scope_bypass``/
``mcp_write_scope_bypass`` inline check) records an ``intended_change``,
that the generated JSON projection is fresh, and that no write route in the
personal/own_private_agent classes has snuck onto a platform-managed
entity outside the reviewed allow-list.
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

from shared.builtin_roles import PLATFORM_OPERATOR_PERMISSIONS, USER_BASE_PERMISSIONS
from src.core import auth as auth_mod
from src.main import app
from src.models.contracts.access_list import (
    INLINE_CHECK_TOKENS,
    AccessClass,
    AccessEntry,
    CurrentGate,
    InlineEffect,
)
from src.models.contracts.permissions import parse_permission
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
_PROVIDER_BYPASS_EFFECTS = {InlineEffect.WIDENS_FOR_BYPASS, InlineEffect.DENY_UNLESS_BYPASS}


def _admits_provider_bypass(entry) -> bool:
    return (
        entry.current_gate == CurrentGate.ENGINE_OR_BYPASS
        or bool(set(entry.inline_checks) & _PROVIDER_BYPASS_TOKENS)
        or entry.inline_effect in _PROVIDER_BYPASS_EFFECTS
    )


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


def _in_scope(obj: object) -> bool:
    """A module, class or function that lives in ``shared.``/``src.``."""
    name = obj.__name__ if inspect.ismodule(obj) else getattr(obj, "__module__", "") or ""
    return name.startswith("shared") or name.startswith("src.")


def _resolve_name(name: str, local_imports: dict[str, tuple[str, str]], func_globals: dict) -> object | None:
    """The in-scope object a name in a function's source refers to: a
    function-local ``from x import y`` first, then the module globals."""
    if name in local_imports:
        module_name, orig_name = local_imports[name]
        if not (module_name.startswith("shared") or module_name.startswith("src.")):
            return None
        try:
            mod = importlib.import_module(module_name)
            target = getattr(mod, orig_name, None)
            return target if target is not None else importlib.import_module(f"{module_name}.{orig_name}")
        except Exception:
            return None
    candidate = func_globals.get(name)
    return candidate if candidate is not None and _in_scope(candidate) else None


def _call_targets(func: object, tree: ast.AST) -> list[object]:
    """The in-scope functions a function calls, by these call shapes:
    ``f()``; ``module.f()``; ``Cls()`` (its ``__init__``); ``Cls.m()``,
    ``Cls(...).m()``, ``var.m()`` where ``var = Cls(...)`` in the same
    function, and ``self.m()``/``cls.m()`` inside a method of ``Cls``. Only
    the named method is followed, never the rest of a class."""
    local_imports = _local_imports(tree)
    func_globals = getattr(func, "__globals__", {})

    def resolve(name: str) -> object | None:
        return _resolve_name(name, local_imports, func_globals)

    qual_parts = getattr(func, "__qualname__", "").split(".")
    owner = func_globals.get(qual_parts[0]) if len(qual_parts) == 2 else None
    instances: dict[str, object] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(node.value, ast.Call):
            cls = resolve(node.value.func.id) if isinstance(node.value.func, ast.Name) else None
            if inspect.isclass(cls):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                instances |= {t.id: cls for t in targets if isinstance(t, ast.Name)}
    found: dict[str, object] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        call = node.func
        target: object | None = None
        if isinstance(call, ast.Name):
            target = resolve(call.id)
            if inspect.isclass(target):
                target = target.__init__
        elif isinstance(call, ast.Attribute):
            base = call.value
            holder: object | None = None
            if isinstance(base, ast.Name) and base.id in ("self", "cls"):
                holder = owner
            elif isinstance(base, ast.Name):
                holder = instances.get(base.id) or resolve(base.id)
            elif isinstance(base, ast.Call) and isinstance(base.func, ast.Name):
                holder = resolve(base.func.id)
            if (inspect.ismodule(holder) or inspect.isclass(holder)) and _in_scope(holder):
                target = getattr(holder, call.attr, None)
        target = getattr(target, "__func__", target)
        if inspect.isfunction(target) and _in_scope(target):
            found[f"{target.__module__}.{target.__qualname__}"] = target
    return [found[key] for key in sorted(found)]


def _parse_source(source: str) -> ast.Module:
    """Parse a function's source. ``dedent`` cannot strip a method whose
    multi-line string runs left of its body, so that one is parsed inside a
    block instead."""
    try:
        return ast.parse(textwrap.dedent(source))
    except SyntaxError:
        return ast.parse("if True:\n" + source)


def _reachable_sources(func: object, depth: int = 1) -> list[str]:
    """The function's own source, plus up to ``depth`` hops of calls it
    makes to a function that lives in ``shared.``/``src.`` (the call shapes
    in ``_call_targets``) — the "service it calls one level down" the R2a-1
    spec asks the inline-check scan to cover. Token-minting helpers are
    excluded (see ``_NOISE_FUNC_NAMES``); a class is never descended into
    wholesale (too coarse — an unrelated method would pollute the scan),
    only the method a call names.
    """
    return [source for _func, source in _reachable_functions(func, depth)]


def _reachable_functions(
    func: object, depth: int | None, _visited: set[int] | None = None
) -> list[tuple[object, str]]:
    """``(function, source)`` for the function and every function it reaches
    by the rules in ``_reachable_sources``, up to ``depth`` hops
    (``None``: unbounded, each function visited once)."""
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
    found: list[tuple[object, str]] = [(func, source)]
    if depth is not None and depth <= 0:
        return found
    for target in _call_targets(func, _parse_source(source)):
        found.extend(_reachable_functions(target, None if depth is None else depth - 1, _visited))
    return found


def _inline_checks(route: APIRoute) -> tuple[str, ...]:
    blob_parts = _reachable_sources(route.endpoint, depth=1)
    for dep_func in _custom_dependency_callables(route):
        blob_parts.extend(_reachable_sources(dep_func, depth=1))
    blob = "\n".join(blob_parts)
    return tuple(sorted({t for t in INLINE_CHECK_TOKENS if re.search(rf"\b{re.escape(t)}\b", blob)}))


# ---------------------------------------------------------------------------
# Elevated checks: every superuser / platform-admin / provider-org / scope-
# bypass decision anywhere in a route's call chain needs a named permission.
# ---------------------------------------------------------------------------

# Attribute reads on a principal/user that decide something about the caller.
_ELEVATED_ATTRS = {
    "is_superuser",  # principal.is_superuser / user.is_superuser: the platform-admin bit
    "is_platform_admin",  # the same bit under its principal/context name
    "is_provider_org",  # provider-org membership (one half of scope bypass)
}
# Names whose use is itself an elevated decision.
_ELEVATED_NAMES = {
    "has_scope_bypass",  # platform admin OR provider-org member
    "mcp_write_scope_bypass",  # the MCP-tool analog of has_scope_bypass
    "CurrentSuperuser",  # superuser-only dependency (Annotated alias)
    "RequirePlatformAdmin",  # superuser-only dependency (Depends alias)
    "get_current_superuser",  # the superuser-only resolver itself
    "CurrentEngineOrBypassUser",  # engine credentials or scope-bypass human
    "get_current_engine_or_bypass_user",  # the engine-or-bypass resolver itself
}
# Keywords that, passed a literal True, act as superuser regardless of caller.
_ELEVATED_TRUE_KEYWORDS = {"is_superuser", "is_platform_admin"}
# Using one of these is the route's dependency gate: the resolver is the
# check site, and the route's own permission is what replaces it.
_ELEVATED_DEPENDENCY_NAMES = {
    "CurrentSuperuser",
    "RequirePlatformAdmin",
    "get_current_superuser",
    "CurrentEngineOrBypassUser",
    "get_current_engine_or_bypass_user",
}
_GATE_RESOLVERS = {
    CurrentGate.SUPERUSER: auth_mod.get_current_superuser,
    CurrentGate.ENGINE_OR_BYPASS: auth_mod.get_current_engine_or_bypass_user,
}


def _qualname(func: object) -> str:
    return f"{getattr(func, '__module__', '?')}.{getattr(func, '__qualname__', '?')}"


def _elevated_token_nodes(tree: ast.AST) -> list[tuple[ast.AST, str]]:
    """``(node, token)`` for every elevated token in a parsed source."""
    found: list[tuple[ast.AST, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in _ELEVATED_ATTRS | _ELEVATED_NAMES:
            # Writing the flag applies a decision made where the value came
            # from; self.is_superuser is the object's copy of a flag its
            # constructor was given. Both call sites are scanned instead.
            is_own_copy = isinstance(node.value, ast.Name) and node.value.id in ("self", "cls")
            if not (isinstance(node.ctx, ast.Store) or is_own_copy):
                found.append((node, node.attr))
        elif isinstance(node, ast.Name) and node.id in _ELEVATED_NAMES:
            found.append((node, node.id))
        elif (
            isinstance(node, ast.keyword)
            and node.arg in _ELEVATED_TRUE_KEYWORDS
            and isinstance(node.value, ast.Constant)
            and node.value.value is True
        ):
            found.append((node, f"{node.arg}=True"))
    return found


def _elevated_tokens_in(source: str) -> set[str]:
    return {token for _node, token in _elevated_token_nodes(_parse_source(source))}


def _elevated_site_tokens(source: str) -> set[str]:
    """The tokens that make a function an elevated-check site: a read used
    to decide, widen, or hand the decision to a callee. Not a site: a flag
    copied into a dict literal or f-string (claims, responses, audit and
    log details), or a use of an elevated dependency (the gate resolver is
    that site)."""
    tree = _parse_source(source)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    tokens: set[str] = set()
    for node, token in _elevated_token_nodes(tree):
        if token in _ELEVATED_DEPENDENCY_NAMES:
            continue
        ancestor = parents.get(node)
        while ancestor is not None and not isinstance(ancestor, (ast.stmt, ast.Dict, ast.JoinedStr)):
            ancestor = parents.get(ancestor)
        if not isinstance(ancestor, (ast.Dict, ast.JoinedStr)):
            tokens.add(token)
    return tokens


def _route_functions(route: APIRoute | WebSocketRoute, visited: set[int]) -> list[tuple[object, str]]:
    """A REST or WebSocket route's handler and non-canonical dependencies,
    transitively, plus its gate resolver when the dependency gate is
    elevated (the resolver alone: its own callees are authentication)."""
    found: list[tuple[object, str]] = []
    for root in [route.endpoint, *_custom_dependency_callables(route)]:
        found.extend(_reachable_functions(root, None, visited))
    resolver = _GATE_RESOLVERS.get(_dependency_gate(route))
    if resolver is not None:
        found.extend(_reachable_functions(resolver, 0, visited))
    return found


def _entry_functions(entry: AccessEntry, routes: dict) -> list[tuple[str, str]]:
    """``(qualified name, source)`` of every function an entry reaches, minus
    the plumbing in ``_ELEVATED_PLUMBING``. A REST/WebSocket entry scans its
    route; an MCP tool scans its function and, when bound to a REST route in
    the operation catalog, that route too (a thin HTTP wrapper reaches the
    route's checks over HTTP, which the source scan cannot follow)."""
    from src.services.mcp_server.server import get_system_tool_function

    visited: set[int] = set()
    found: list[tuple[object, str]] = []
    if entry.mcp_tool is None:
        found.extend(_route_functions(routes[entry.key], visited))
    else:
        tool = get_system_tool_function(entry.mcp_tool)
        if tool is not None:
            found.extend(_reachable_functions(tool, None, visited))
        op = _catalog_by_mcp().get(entry.mcp_tool)
        if op is not None and op.rest is not None and (op.rest.method, op.rest.path) in routes:
            found.extend(_route_functions(routes[(op.rest.method, op.rest.path)], visited))
    return [(_qualname(func), source) for func, source in found if _qualname(func) not in _ELEVATED_PLUMBING]


# Functions that mention an elevated token without deciding anything about
# the caller. Each must be agreed by a reviewer; never list a function that
# branches on the flag to allow or widen something (the plumbing test below
# fails if a listed function uses a token in a condition).
_ELEVATED_PLUMBING: dict[str, str] = {
    "src.routers.auth.get_current_user_info": "Copies the caller's own flag into the /auth/me response.",
    "src.routers.auth.register_user": "Copies the new user's flag into their token claims and response.",
    "src.routers.profile.get_profile": "Copies the caller's own flag into the profile response.",
    "src.routers.profile.update_profile": "Copies the caller's own flag into the profile response.",
    "src.routers.profile.upload_avatar": "Copies the caller's own flag into the profile response.",
    "src.routers.profile.delete_avatar": "Copies the caller's own flag into the profile response.",
    "src.services.user_role_assignments.authorization_summary": (
        "Reports the caller's own flag in their authorization summary."
    ),
    "src.services.user_provisioning.ensure_user_provisioned": (
        "Bootstrap: the first account is created as platform admin; reads no caller's privilege."
    ),
    "shared.claims.preresolve._load_source_policies": (
        "Loads a claim source's policy rules as system before the caller is evaluated against them."
    ),
    "src.routers.websocket._load_policies_for_table": (
        "Loads a table's policy rules as system before the subscriber is evaluated against them."
    ),
    "src.services.table_policy_loader.load_resolved_table_policies": (
        "Loads a table's policy rules as system before the caller is evaluated against them."
    ),
    "shared.sdk_users.list_users": "Filters the listed users by their own flag (?type=platform); not the caller's.",
    "src.routers.files.test_file_policy_access": (
        "Reports whether the tested principal passes workspace access; the caller is gated by the route."
    ),
    "src.services.user_access_map.build_access_map": (
        "Reports the mapped user's own admin status in their access map."
    ),
}


def _resolve_qualname(qualname: str) -> object | None:
    """The function a ``module.Qual.name`` string names, or None."""
    parts = qualname.split(".")
    for split in range(len(parts) - 1, 0, -1):
        try:
            obj: object = importlib.import_module(".".join(parts[:split]))
        except ImportError:
            continue
        for attr in parts[split:]:
            obj = getattr(obj, attr, None)
            if obj is None:
                return None
        return obj
    return None


def _elevated_tokens_in_conditions(source: str) -> set[str]:
    """Elevated tokens used inside a branch condition (if/while/ternary/
    assert/boolean expression) — the shape of a decision, not a copy."""
    tree = _parse_source(source)
    tokens: set[str] = set()
    for node in ast.walk(tree):
        tests: list[ast.AST] = []
        if isinstance(node, (ast.If, ast.While, ast.IfExp, ast.Assert)):
            tests.append(node.test)
        elif isinstance(node, ast.BoolOp):
            tests.extend(node.values)
        for test in tests:
            tokens |= _elevated_tokens_in(ast.unparse(test))
    return tokens


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
            # An evaluator entry's dependency is CurrentActiveUser; the
            # evaluator half is checked by TestEvaluatorEnforcement.
            expected = (
                CurrentGate.AUTHENTICATED
                if entry.current_gate == CurrentGate.EVALUATOR
                else entry.current_gate
            )
            if actual != expected:
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
        widening = {InlineEffect.WIDENS_FOR_SUPERUSER, InlineEffect.WIDENS_FOR_BYPASS}
        for entry in ACCESS_LIST:
            if entry.access_class == AccessClass.PERMISSION:
                assert entry.permission, entry
            if entry.permission:
                assert entry.boundary, entry
            else:
                assert entry.boundary is None, entry
            if entry.permission and entry.access_class != AccessClass.PERMISSION:
                assert entry.inline_effect in widening, entry

    def test_catalogued_permission_matches_action_scopes(self) -> None:
        catalog_by_id = {op.operation_id: op for op in OPERATION_CATALOG}
        mismatches = []
        for entry in ACCESS_LIST:
            if entry.operation_id is None or entry.permission is None:
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
        unknown = []
        for entry in ACCESS_LIST:
            if entry.permission is None:
                continue
            try:
                parse_permission(entry.permission)
            except ValueError as exc:
                unknown.append((entry.key, str(exc)))
        assert not unknown, f"permission outside the grammar or PERMISSION_DOMAINS: {unknown}"

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
        # Organizations domain: creating an organization has no existing
        # organization to apply to, so it is Platform-boundary; reading,
        # updating and disabling one apply to that organization. The MCP
        # tools inherit the same split (authenticated transport floor).
        ("organizations", CurrentGate.SUPERUSER),
        ("organizations", CurrentGate.AUTHENTICATED),
        ("organizations", CurrentGate.EVALUATOR),
        # required-instructions (settings, folded in above) has a
        # platform-wide GET/PUT and a deliberate per-org
        # /organizations/{organization_id} variant (see reason text).
    }

    def test_same_domain_same_gate_entries_share_one_boundary(self) -> None:
        groups: dict[tuple[str, CurrentGate], set[str]] = {}
        for entry in ACCESS_LIST:
            if entry.permission is None:
                continue
            domain = parse_permission(entry.permission).domain
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


# The enforcement helpers that name an evaluator entry by its operation key
# (``src.services.authorization.enforce``). In a module that calls one, the
# operation keys are the string constants passed to those calls, passed to
# any call as an ``operation=`` keyword, or bound to a module-level
# ``*_OPERATION`` name or a local ``operation`` variable (how a handler names
# the key it passes along to a helper).
_ENFORCE_CALLS = {"require_operation", "authorize_operation", "operation_reach", "allows_operation"}


def _enforced_operation_keys() -> set[str]:
    keys: set[str] = set()
    for root in ("src", "shared"):
        for path in sorted((_API_ROOT / root).rglob("*.py")):
            tree = ast.parse(path.read_text(), filename=str(path))
            calls = [
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and (node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None))
                in _ENFORCE_CALLS
            ]
            if not calls or path.name == "enforce.py":
                continue
            for call in calls:
                keys |= {
                    arg.value
                    for arg in call.args
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                }
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    keys |= {
                        kw.value.value
                        for kw in node.keywords
                        if kw.arg == "operation"
                        and isinstance(kw.value, ast.Constant)
                        and isinstance(kw.value.value, str)
                    }
            for node in ast.walk(tree):
                if not (
                    isinstance(node, ast.Assign)
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)
                ):
                    continue
                for target in node.targets:
                    if isinstance(target, ast.Name) and (
                        target.id == "operation" or target.id.endswith("_OPERATION")
                    ):
                        keys.add(node.value.value)
    return keys


class TestEvaluatorEnforcement:
    """An ``evaluator`` entry is a route cut over to the R3 evaluator: the
    code must decide it through the enforcement helper, by the entry's
    operation key, and every key the code decides by must be an evaluator
    entry."""

    def test_every_evaluator_entry_is_enforced(self) -> None:
        from src.services.authorization.enforce import operation_key

        enforced = _enforced_operation_keys()
        missing = [
            operation_key(entry)
            for entry in ACCESS_LIST
            if entry.current_gate == CurrentGate.EVALUATOR and operation_key(entry) not in enforced
        ]
        assert not missing, f"evaluator entries no handler or service decides: {missing}"

    def test_every_enforced_key_is_an_evaluator_entry(self) -> None:
        from src.services.authorization.enforce import operation_key

        evaluator_keys = {
            operation_key(entry)
            for entry in ACCESS_LIST
            if entry.current_gate == CurrentGate.EVALUATOR and entry.mcp_tool is None
        }
        operation_like = {key for key in _enforced_operation_keys() if "." in key or " /" in key}
        stray = sorted(operation_like - evaluator_keys)
        assert not stray, f"enforcement helper called with a key that is not an evaluator entry: {stray}"

    def test_evaluator_entries_are_permission_class_rest_routes(self) -> None:
        wrong = [
            entry.key
            for entry in ACCESS_LIST
            if entry.current_gate == CurrentGate.EVALUATOR
            and (entry.mcp_tool is not None or entry.access_class != AccessClass.PERMISSION)
        ]
        assert not wrong, f"evaluator entries must be permission-class REST routes: {wrong}"


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


class TestInlineEffect:
    """`inline_effect` says what an inline check does to the caller; the R2c
    decision matrix reads it, so it must be present wherever a check is."""

    def test_required_when_inline_checks_are_present(self) -> None:
        with pytest.raises(ValueError, match="inline_effect is required"):
            AccessEntry(
                method="GET",
                path="/x",
                access_class=AccessClass.PERSONAL,
                current_gate=CurrentGate.AUTHENTICATED,
                inline_checks=("is_superuser",),
                reason="test",
            )

    def test_may_stand_without_tokens_when_the_check_is_deeper(self) -> None:
        entry = AccessEntry(
            method="GET",
            path="/x",
            access_class=AccessClass.PERSONAL,
            current_gate=CurrentGate.AUTHENTICATED,
            inline_effect=InlineEffect.DENY_UNLESS_SUPERUSER,
            reason="test",
        )
        assert entry.inline_checks == ()

    def test_every_entry_with_inline_checks_has_an_effect(self) -> None:
        missing = [e.key for e in ACCESS_LIST if e.inline_checks and e.inline_effect is None]
        assert not missing, f"entries with inline_checks but no inline_effect: {missing}"

    def test_entries_with_an_effect_but_no_tokens_say_where_the_check_lives(self) -> None:
        unexplained = [
            e.key
            for e in ACCESS_LIST
            if e.inline_effect is not None and not e.inline_checks and "one-hop" not in e.reason
        ]
        assert not unexplained, f"inline_effect without inline_checks needs a reason naming where: {unexplained}"

    def test_bound_mcp_tools_share_their_rest_routes_effect(self, entries_by_key) -> None:
        catalog_by_mcp = _catalog_by_mcp()
        mismatches = []
        for name in _mcp_tool_ids():
            op = catalog_by_mcp.get(name)
            if op is None or op.rest is None:
                continue
            rest_entry = entries_by_key.get((op.rest.method, op.rest.path))
            mcp_entry = entries_by_key.get(name)
            if rest_entry is None or mcp_entry is None or mcp_entry.inline_effect is None:
                continue
            if rest_entry.inline_effect not in (None, mcp_entry.inline_effect):
                mismatches.append((name, mcp_entry.inline_effect, rest_entry.inline_effect))
        assert not mismatches, f"MCP tool effect differs from its bound REST route: {mismatches}"


class TestPermissionOnOtherClasses:
    """A permission on a class other than ``permission`` gates only the
    elevated branch, so it needs a boundary and a widening effect."""

    def _personal(self, **kwargs) -> AccessEntry:
        return AccessEntry(
            method="GET",
            path="/x",
            access_class=AccessClass.PERSONAL,
            current_gate=CurrentGate.AUTHENTICATED,
            reason="test",
            **kwargs,
        )

    def test_accepted_with_a_boundary_and_a_widening_effect(self) -> None:
        entry = self._personal(
            inline_effect=InlineEffect.WIDENS_FOR_SUPERUSER,
            permission="platformjobs.read.all",
            boundary="organization",
        )
        assert entry.permission == "platformjobs.read.all"

    def test_rejected_without_a_widening_effect(self) -> None:
        with pytest.raises(ValueError, match="gates only the elevated branch"):
            self._personal(
                inline_effect=InlineEffect.NO_CALLER_EFFECT,
                permission="platformjobs.read.all",
                boundary="organization",
            )

    def test_rejected_without_a_boundary(self) -> None:
        with pytest.raises(ValueError, match="boundary is required when permission is set"):
            self._personal(inline_effect=InlineEffect.WIDENS_FOR_SUPERUSER, permission="platformjobs.read.all")

    def test_boundary_without_a_permission_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="boundary must be unset"):
            self._personal(boundary="organization")


class TestIntendedChangeCoverage:
    """Any entry that admits a provider-org non-admin beyond a customer
    member — engine_or_bypass gate, an inline has_scope_bypass /
    mcp_write_scope_bypass check, or an inline effect that admits or widens
    for scope-bypass callers — must say so via intended_change, unless
    its reason already states why permanent provider-org access is
    intended (none currently do)."""

    def test_bypass_admitting_entries_have_intended_change(self) -> None:
        missing = []
        for entry in ACCESS_LIST:
            admits_bypass = _admits_provider_bypass(entry)
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
            admits_bypass = _admits_provider_bypass(entry)
            if entry.intended_change and not admits_bypass:
                extra.append(entry.key)
        assert not extra, f"intended_change set without a bypass-admitting gate/check: {extra}"


def _entry_target(entry: AccessEntry) -> str:
    return f"MCP {entry.mcp_tool}" if entry.mcp_tool else f"{entry.method} {entry.path}"


@pytest.fixture(scope="module")
def entry_functions() -> dict:
    """Every entry's reached functions, scanned once for both rules below."""
    routes: dict = {(m, p): r for m, p, r in _rest_routes()}
    routes |= {("WS", _openapi_path(r.path)): r for r in app.routes if isinstance(r, WebSocketRoute)}
    return {entry.key: _entry_functions(entry, routes) for entry in ACCESS_LIST}


# Every elevated-check site, mapped to what replaces the flag at the cutover:
#   "reach": the flag only widens which organizations or rows the caller
#            sees; it becomes the run user's reach.
#   "route": the site is a route's own gate (a superuser/bypass dependency
#            or the evaluator's admin short-circuit); the entry's named
#            permission replaces it.
#   "delete": a follow-up change removes the branch outright.
#   "cutover": closed at the cutover (workflows stop calling as superuser,
#            Redis-held authority goes).
#   a permission string: the power the flag unlocks. No such permission is
#            held by the User or Platform Operator role.
# A site with several checks maps to a tuple of these.
_ELEVATED_SITES: dict[str, str | tuple[str, ...]] = {
    "shared.event_emission.emit_topic_event": "reach",
    "shared.execution_timeseries.get_execution_time_series": "reach",
    "shared.external_access.resolve_external_claim": "delete",
    "shared.file_access.authorize_file_policy": ("repository.read", "repository.readwrite"),
    "shared.form_provider.execute_form_field_provider": "reach",
    "shared.form_publication._resolve_form_workflow": "reach",
    "shared.home.can_edit_collection": ("home.readwrite", "home.readwrite.all"),
    "shared.home.can_read_collection": "home.read.all",
    "shared.home.catalog": "reach",
    "shared.home.get_home": "home.readwrite",
    "shared.home.save_collection": "home.readwrite",
    "shared.pending_execution.get_pending_execution_fallback": ("executions.read.all", "reach"),
    "shared.scope_resolver.resolve_effective_scope": "reach",
    "shared.sdk_agent_runs.resolve_executable_agent": "reach",
    "shared.sdk_ai.complete_sdk_ai": "reach",
    "shared.sdk_artifact_generation.sdk_generate_image_artifact": "artifacts.readwrite.all",
    "shared.sdk_artifact_generation.sdk_render_document_artifact": "artifacts.readwrite.all",
    "shared.sdk_artifact_generation.sdk_render_spreadsheet_artifact": "artifacts.readwrite.all",
    "shared.sdk_artifact_generation.sdk_render_text_artifact": "artifacts.readwrite.all",
    "shared.sdk_artifacts.sdk_artifact_download_url": "artifacts.read.all",
    "shared.sdk_artifacts.sdk_list_artifacts": "artifacts.read.all",
    "shared.sdk_artifacts.sdk_read_artifact": "artifacts.read.all",
    "shared.sdk_artifacts.sdk_store_artifact": "artifacts.readwrite.all",
    "shared.sdk_config.get_sdk_config_value": "reach",
    "shared.sdk_config.list_sdk_config_values": "reach",
    "shared.sdk_context.get_sdk_context": "reach",
    "shared.sdk_execution_reads.get_sdk_execution": ("executions.read.all", "executions.read"),
    "shared.sdk_execution_reads.list_sdk_executions": ("executions.read.all", "reach"),
    "shared.sdk_execution_reads.list_sdk_workflows": "reach",
    "shared.sdk_forms.get_sdk_form": ("reach", "forms.read"),
    "shared.sdk_forms.list_sdk_forms": ("reach", "forms.read"),
    "shared.sdk_integrations.get_sdk_integration_dict": "reach",
    "shared.sdk_table_metadata.delete_sdk_table": "reach",
    "shared.sdk_users._authorize_update": "privilegedaccess.readwrite",
    "shared.sdk_users.bulk_update_users": ("privilegedaccess.readwrite", "userlifecycle.readwrite"),
    "shared.sdk_video.can_read_platform_job": ("platformjobs.read.all", "platformjobs.readwrite.all"),
    "shared.sdk_workflow_execution.cancel_scheduled_sdk_execution": "executions.readwrite.all",
    "shared.sdk_workflow_execution.execute_sdk_workflow": ("repository.readwrite", "users.impersonate", "reach"),
    "shared.table_document_writes.resolve_attribution": "tableattribution.readwrite",
    "shared.table_resolution.assert_explicit_scope_targets_table": "reach",
    "shared.table_resolution.get_table_or_404": "reach",
    "src.core.auth.get_current_engine_or_bypass_user": "route",
    "src.core.auth.get_current_superuser": "route",
    "src.core.org_filter.resolve_org_filter": "reach",
    "src.core.org_filter.resolve_target_org": "reach",
    "src.routers.agent_runs._require_own_private_agent_run": "agentruns.readwrite.all",
    "src.routers.agent_tuning._load_agent_with_access": ("agents.readwrite", "agents.readwrite.all"),
    "src.routers.agents.create_agent": "agents.readwrite",
    "src.routers.agents.delete_agent": ("agents.readwrite", "agents.readwrite.all"),
    "src.routers.agents.delete_agent_logo": ("agents.readwrite", "agents.readwrite.all"),
    "src.routers.agents.get_accessible_tools": "reach",
    "src.routers.agents.get_agent": ("reach", "agents.read", "agents.read.all"),
    "src.routers.agents.get_agent_delegations": ("reach", "agents.read", "agents.read.all"),
    "src.routers.agents.get_agent_logo": ("reach", "agents.read", "agents.read.all"),
    "src.routers.agents.get_agent_stats_endpoint": ("reach", "agents.read", "agents.read.all"),
    "src.routers.agents.get_agent_tools": ("reach", "agents.read", "agents.read.all"),
    "src.routers.agents.get_fleet_stats_endpoint": "reach",
    "src.routers.agents.list_agents": ("reach", "agents.read", "agents.read.all"),
    "src.routers.agents.promote_agent": ("agents.readwrite", "agents.readwrite.all"),
    "src.routers.agents.update_agent": ("agents.readwrite", "agents.readwrite.all"),
    "src.routers.agents.upload_agent_logo": ("agents.readwrite", "agents.readwrite.all"),
    "src.routers.app_code_files.get_application_for_write_or_404": ("reach", "apps.readwrite"),
    "src.routers.app_code_files.get_application_or_404": ("reach", "apps.read"),
    "src.routers.app_code_files.get_bundle_manifest": "apps.read",
    "src.routers.applications.batch_update_application_sdks": "reach",
    "src.routers.applications.create_application": "reach",
    "src.routers.applications.delete_application": ("reach", "apps.readwrite"),
    "src.routers.applications.export_application": ("reach", "apps.read"),
    "src.routers.applications.get_application": ("reach", "apps.read"),
    "src.routers.applications.get_application_by_id_or_404": ("reach", "apps.read"),
    "src.routers.applications.get_application_for_write_or_404": ("reach", "apps.readwrite"),
    "src.routers.applications.get_application_or_404": ("reach", "apps.read"),
    "src.routers.applications.get_draft": ("reach", "apps.read"),
    "src.routers.applications.list_applications": ("reach", "apps.read"),
    "src.routers.applications.replace_application_endpoint": ("reach", "apps.readwrite"),
    "src.routers.applications.rollback_application": ("reach", "apps.readwrite"),
    "src.routers.applications.save_draft": ("reach", "apps.readwrite"),
    "src.routers.applications.swap_application_slugs": ("reach", "apps.readwrite"),
    "src.routers.applications.update_application": ("reach", "apps.readwrite"),
    "src.routers.chat._check_agent_access": "reach",
    "src.routers.cli._resolve_sdk_org_id": "reach",
    "src.routers.cli.cli_create_table": "reach",
    "src.routers.cli.cli_delete_config": "reach",
    "src.routers.cli.cli_get_config": "reach",
    "src.routers.cli.cli_list_config": "reach",
    "src.routers.cli.cli_list_tables": "reach",
    "src.routers.cli.cli_set_config": "reach",
    "src.routers.cli.sdk_integrations_delete_mapping": "reach",
    "src.routers.cli.sdk_integrations_get": "reach",
    "src.routers.cli.sdk_integrations_get_mapping": "reach",
    "src.routers.cli.sdk_integrations_list_mappings": "reach",
    "src.routers.cli.sdk_integrations_refresh_token": "reach",
    "src.routers.cli.sdk_integrations_upsert_mapping": "reach",
    "src.routers.config.delete_config": "reach",
    "src.routers.config.get_config": "reach",
    "src.routers.config.get_config_by_id": "reach",
    "src.routers.config.set_config": "reach",
    "src.routers.config.update_config": "reach",
    "src.routers.endpoints._execute_sync": "reach",
    "src.routers.endpoints.execute_endpoint": "reach",
    "src.routers.executions.ExecutionRepository._to_pydantic": "executions.read",
    "src.routers.executions.ExecutionRepository.cancel_execution": "executions.readwrite.all",
    "src.routers.executions.ExecutionRepository.get_execution_logs": ("executions.read.all", "executions.read"),
    "src.routers.executions.ExecutionRepository.get_execution_result": "executions.read.all",
    "src.routers.executions.ExecutionRepository.get_execution_variables": "executions.read",
    "src.routers.files._test_principal": "filepolicies.read",
    "src.routers.files.set_file_policy": "reach",
    "src.routers.forms._authorize_form_runtime": ("reach", "forms.read"),
    "src.routers.forms.execute_startup_workflow": "reach",
    "src.routers.forms.get_form_field_options": "forms.read",
    "src.routers.forms.get_form_logo": ("reach", "forms.read"),
    "src.routers.forms.get_form_runtime": ("reach", "forms.read"),
    "src.routers.forms.submit_form": "reach",
    "src.routers.integrations.test_integration_connection": "reach",
    "src.routers.knowledge_sources.get_document": "reach",
    "src.routers.mcp._gateway_service": "reach",
    "src.routers.mcp.delete_mcp_config": "route",
    "src.routers.mcp.get_mcp_config": "route",
    "src.routers.mcp.list_mcp_tools": "route",
    "src.routers.mcp.update_mcp_config": "route",
    "src.routers.mcp_connections._enforce_can_write_org": "reach",
    "src.routers.mcp_connections._get_connection_or_404": "reach",
    "src.routers.mcp_connections.create_mcp_connection": "reach",
    "src.routers.mcp_connections.list_mcp_connections": "reach",
    "src.routers.mcp_servers.delete_mcp_server": "reach",
    "src.routers.mcp_servers.get_mcp_server": "reach",
    "src.routers.mcp_servers.list_mcp_servers": "reach",
    "src.routers.mcp_servers.update_mcp_server": "reach",
    "src.routers.metrics._compute_metrics_directly": "reach",
    "src.routers.metrics.get_metrics": "reach",
    "src.routers.notifications.get_notification": "platform.read",
    "src.routers.notifications.list_notifications": "platform.read",
    "src.routers.oauth_connections.authorize_connection": "reach",
    "src.routers.oauth_connections.cancel_authorization": "reach",
    "src.routers.oauth_connections.create_connection": "reach",
    "src.routers.oauth_connections.delete_connection": "reach",
    "src.routers.oauth_connections.get_connection": "reach",
    "src.routers.oauth_connections.get_credentials": "reach",
    "src.routers.oauth_connections.oauth_callback": "reach",
    "src.routers.oauth_connections.refresh_token": "reach",
    "src.routers.oauth_connections.update_connection": "reach",
    "src.routers.platform_jobs.list_platform_jobs": "platformjobs.read.all",
    "src.routers.policy_rules.list_policy_rules": "reach",
    "src.routers.roles.get_role": "route",
    "src.routers.roles.list_roles": "route",
    "src.routers.sdk_modules._module_source_caller": "route",
    "src.routers.tables.create_table": "reach",
    "src.routers.tables.list_tables": "reach",
    "src.routers.tables.update_table": "reach",
    "src.routers.tables.validate_policies": "reach",
    "src.routers.tools.list_tools": "reach",
    "src.routers.users.create_user": "privilegedaccess.readwrite",
    "src.routers.users.get_user_forms": ("reach", "forms.read"),
    "src.routers.users.update_user": "privilegedaccess.readwrite",
    "src.routers.websocket._file_org_and_scope": "reach",
    "src.routers.websocket._resolve_table_id": "reach",
    "src.routers.websocket.can_access_agent_run": ("agentruns.read.all", "reach"),
    "src.routers.websocket.can_access_app": ("reach", "apps.read"),
    "src.routers.websocket.can_access_execution": ("executions.read.all", "reach"),
    "src.routers.websocket.can_access_service": "platform.read",
    "src.routers.websocket.websocket_connect": ("reach", "platform.read"),
    "src.services.access_check_entry.run_user_may_open": "route",
    "src.services.access_check_policies.load_policy_principal": "reach",
    "src.services.agent_executor.AgentExecutor._execute_knowledge_search": "reach",
    "src.services.agent_executor.AgentExecutor._execute_system_tool": "reach",
    "src.services.agent_executor.AgentExecutor._execute_tool": "reach",
    "src.services.agent_executor.AgentExecutor._switch_agent": "reach",
    "src.services.authorization.enforce.decide_for": "route",
    "src.services.authorization.enforce.permitted_organizations": "reach",
    "src.services.authorization.enforce.require_unprotected": "privilegedaccess.readwrite",
    "src.services.authorization.evaluator.decide": "route",
    "src.services.authorization.explain.in_reach": "reach",
    "src.services.chat_artifacts.execute_artifact_tool": "artifacts.read.all",
    "src.services.chat_runs._load_authorized_agent": "reach",
    "src.services.chat_runs.create_chat_run": "reach",
    "src.services.docs_indexer.index_platform_docs": "route",
    "src.services.execution.agent_run_access.agent_run_visibility_conditions": ("agentruns.read.all", "reach"),
    "src.services.execution.agent_workflow_tools.execute_agent_workflow_tool": "reach",
    "src.services.execution.async_executor.enqueue_code_execution": "reach",
    "src.services.execution.async_executor.enqueue_system_workflow_execution": "reach",
    "src.services.execution.async_executor.enqueue_workflow_execution": "reach",
    "src.services.execution.autonomous_agent_executor.AutonomousAgentExecutor._execute_knowledge_search": "reach",
    "src.services.file_policy_service.FilePolicyService.is_allowed": "reach",
    "src.services.identities.require_delegation": "delete",
    "src.services.mcp_server.tools._org_scope.mcp_write_scope_bypass": "reach",
    "src.services.mcp_server.tools.code_editor._check_read_scope": "repository.read",
    "src.services.mcp_server.tools.code_editor._check_write_scope": "repository.readwrite",
    "src.services.mcp_server.tools.knowledge.search_knowledge": "reach",
    "src.services.policy_rule_service.PolicyRuleService._get": "reach",
    "src.services.solution_scope.is_service_principal": "reach",
    "src.services.solution_scope.resolve_solution_table_by_name": "reach",
    "src.services.user_access_map._place_for": "reach",
    "src.services.user_role_assignments._assignable_roles": "privilegedaccess.readwrite",
    "src.services.user_role_assignments.boundary_placement": "reach",
    "src.services.user_role_assignments.check_boundaries": "reach",
    "src.services.user_role_assignments.check_role_change": "privilegedaccess.readwrite",
    "src.services.user_role_assignments.replace_role_assignments": "privilegedaccess.readwrite",
}
_SITE_KINDS = {"reach", "route", "delete", "cutover"}


class TestEveryElevatedCheckHasAScope:
    """Every route and MCP tool names the permission that gates it, except
    public entry points, personal routes and table/file-policy data.
    ``own_private_agent`` always names one. An elevated check (superuser,
    platform admin, provider org, scope bypass) anywhere in any entry's call
    chain names its replacement in ``_ELEVATED_SITES`` — so neither a
    permissioned route nor a public or personal one can unlock something
    extra on a flag without saying what replaces it."""

    _SITE_REGISTERED = {AccessClass.PUBLIC, AccessClass.PERSONAL}
    _POLICY_GOVERNED = {AccessClass.TABLE_POLICY}

    def test_entries_name_a_permission_where_the_rule_requires_one(self, entry_functions) -> None:
        offenders: list[tuple[str, str]] = []
        for entry in ACCESS_LIST:
            if entry.permission or entry.access_class in self._POLICY_GOVERNED:
                continue
            cls = entry.access_class.value
            if entry.access_class in self._SITE_REGISTERED:
                unregistered = sorted(
                    where
                    for where, source in entry_functions[entry.key]
                    if where not in _ELEVATED_SITES and _elevated_site_tokens(source)
                )
                if unregistered:
                    detail = f"elevated-check sites not in _ELEVATED_SITES: {', '.join(unregistered)}"
                    offenders.append((cls, f"{_entry_target(entry)} | {cls} | {detail}"))
                continue
            offenders.append((cls, f"{_entry_target(entry)} | {cls} | needs a permission"))
        offenders.sort()
        assert not offenders, (
            f"{len(offenders)} entries fail the rule (METHOD path | class | why):\n"
            + "\n".join(line for _cls, line in offenders)
        )

    def test_every_elevated_check_site_names_its_replacement(self, entry_functions) -> None:
        sites: dict[str, tuple[set[str], set[str]]] = {}
        for entry in ACCESS_LIST:
            for where, source in entry_functions[entry.key]:
                tokens = _elevated_site_tokens(source)
                if tokens:
                    site_tokens, targets = sites.setdefault(where, (set(), set()))
                    site_tokens |= tokens
                    targets.add(_entry_target(entry))
        unregistered = []
        for where in sorted(set(sites) - set(_ELEVATED_SITES)):
            tokens, targets = sites[where]
            examples = "; ".join(sorted(targets)[:3])
            unregistered.append(f"{where} | {', '.join(sorted(tokens))} | {len(targets)} routes: {examples}")
        stale = sorted(set(_ELEVATED_SITES) - set(sites))
        assert not unregistered and not stale, (
            f"{len(unregistered)} elevated-check sites with no replacement in _ELEVATED_SITES "
            "(qualified function | tokens | routes that reach it):\n"
            + "\n".join(unregistered)
            + (f"\n_ELEVATED_SITES entries that are no longer sites: {stale}" if stale else "")
        )

    def test_registered_replacements_are_a_site_kind_or_a_permission(self) -> None:
        invalid = []
        for where, replacement in sorted(_ELEVATED_SITES.items()):
            items = (replacement,) if isinstance(replacement, str) else replacement
            if not items:
                invalid.append(f"{where}: empty tuple")
            for item in items:
                if item in _SITE_KINDS:
                    continue
                try:
                    parse_permission(item)
                except ValueError as exc:
                    invalid.append(f"{where}: {exc}")
        assert not invalid, (
            "_ELEVATED_SITES replacements that are not reach/route/delete/cutover/a permission:\n"
            + "\n".join(invalid)
        )

    def test_site_permissions_are_held_by_neither_user_nor_operator(self) -> None:
        everyday = USER_BASE_PERMISSIONS | PLATFORM_OPERATOR_PERMISSIONS
        held = sorted(
            f"{where}: {item}"
            for where, replacement in _ELEVATED_SITES.items()
            for item in ((replacement,) if isinstance(replacement, str) else replacement)
            if item in everyday
        )
        assert not held, (
            "_ELEVATED_SITES permissions that the User or Platform Operator role holds "
            "(a flag replacement must not be an everyday permission):\n" + "\n".join(held)
        )

    def test_plumbing_exclusions_exist_and_never_branch_on_a_flag(self) -> None:
        problems = []
        for qualname in sorted(_ELEVATED_PLUMBING):
            func = _resolve_qualname(qualname)
            if func is None or not inspect.isfunction(func):
                problems.append(f"{qualname}: no such function")
                continue
            branching = _elevated_tokens_in_conditions(inspect.getsource(func))
            if branching:
                problems.append(f"{qualname}: branches on {sorted(branching)}")
        assert not problems, "_ELEVATED_PLUMBING entries that are stale or decide something:\n" + "\n".join(
            problems
        )


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
# User-base-role rule: no personal/own_private_agent write route on a
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
    ("POST", "/api/workflows/executions/{execution_id}/cancel"): "Cancel an own/accessible execution.",
    ("POST", "/api/sdk/artifacts"): "Own execution-workspace artifact.",
    ("POST", "/api/sdk/artifacts/document"): "Own execution-workspace artifact.",
    ("POST", "/api/sdk/artifacts/spreadsheet"): "Own execution-workspace artifact.",
    ("POST", "/api/sdk/artifacts/text"): "Own execution-workspace artifact.",
    ("DELETE", "/api/notifications/{notification_id}"): "Own notification.",
    ("PATCH", "/api/profile"): "Own profile.",
    ("POST", "/api/profile/avatar"): "Own profile.",
    ("DELETE", "/api/profile/avatar"): "Own profile.",
    ("POST", "/api/profile/password"): "Own profile.",
    ("PUT", "/api/memory/settings"): "Own memory on/off setting.",
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
    ("POST", "/api/agent-runs/{run_id}/verdict"): "Own private agent's run (tuning).",
    ("DELETE", "/api/agent-runs/{run_id}/verdict"): "Own private agent's run (tuning).",
    ("POST", "/api/agent-runs/{run_id}/flag-conversation/message"): "Own private agent's run (tuning).",
    ("POST", "/api/agent-runs/{run_id}/cancel"): "Own agent run (or any, for a scope-bypass caller).",
    ("POST", "/api/chat/conversations"): "Own chat conversation.",
    ("DELETE", "/api/chat/conversations/{conversation_id}"): "Own chat conversation.",
    ("POST", "/api/chat/runs/{run_id}/cancel"): "Own chat conversation.",
    ("PATCH", "/api/chat/artifacts/{attachment_id}"): "Own chat artifact.",
    ("DELETE", "/api/chat/artifacts/{attachment_id}"): "Own chat artifact.",
    ("POST", "/api/chat/conversations/{conversation_id}/attachments"): "Own chat conversation.",
    ("DELETE", "/api/chat/conversations/{conversation_id}/attachments/{attachment_id}"): "Own chat conversation.",
    ("DELETE", "/api/me/mcp-connections/{connection_id}"): "Own MCP tool connection.",
    ("POST", "/api/platform-jobs/{job_id}/cancel"): "Own platform job (or any, for a platform admin).",
}


def test_personal_and_own_agent_writes_are_all_on_the_reviewed_allowlist() -> None:
    write_methods = {"POST", "PUT", "PATCH", "DELETE"}
    narrow_classes = {AccessClass.PERSONAL, AccessClass.OWN_PRIVATE_AGENT}
    offenders = [
        (entry.method, entry.path)
        for entry in ACCESS_LIST
        if entry.access_class in narrow_classes
        and entry.method in write_methods
        and (entry.method, entry.path) not in _REVIEWED_WRITE_ALLOWLIST
    ]
    assert not offenders, (
        "personal/own_private_agent write route not on the reviewed "
        f"allow-list — narrow the class or add it with a reason: {offenders}"
    )


def test_allowlist_has_no_unused_entries() -> None:
    actual = {
        (entry.method, entry.path)
        for entry in ACCESS_LIST
        if entry.access_class
        in {AccessClass.PERSONAL, AccessClass.OWN_PRIVATE_AGENT}
        and entry.method in {"POST", "PUT", "PATCH", "DELETE"}
    }
    unused = set(_REVIEWED_WRITE_ALLOWLIST) - actual
    assert not unused, f"allow-list entries with no matching access-list route: {unused}"
