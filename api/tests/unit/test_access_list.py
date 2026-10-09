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
import functools
import importlib
import inspect
import pkgutil
import re
import subprocess
import sys
import textwrap
import types
import typing
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


def _in_scanned_package(module_name: str) -> bool:
    """``shared.``, ``src.`` and the ``bifrost`` SDK package (the workflow
    runtime calls into ``src.``, and routes and consumers call into it)."""
    return module_name.startswith(("shared", "src.", "bifrost.")) or module_name == "bifrost"


def _in_scope(obj: object) -> bool:
    """A module, class or function that lives in a scanned package."""
    name = obj.__name__ if inspect.ismodule(obj) else getattr(obj, "__module__", "") or ""
    return _in_scanned_package(name)


def _resolve_name(name: str, local_imports: dict[str, tuple[str, str]], func_globals: dict) -> object | None:
    """The in-scope object a name in a function's source refers to: a
    function-local ``from x import y`` first, then the module globals."""
    if name in local_imports:
        module_name, orig_name = local_imports[name]
        if not _in_scanned_package(module_name):
            return None
        try:
            mod = importlib.import_module(module_name)
            target = getattr(mod, orig_name, None)
            return target if target is not None else importlib.import_module(f"{module_name}.{orig_name}")
        except Exception:
            return None
    candidate = func_globals.get(name)
    return candidate if candidate is not None and _in_scope(candidate) else None


def _lookup(name: str, local_imports: dict[str, tuple[str, str]], func_globals: dict) -> object | None:
    """The object a name refers to, in or out of scope (an annotation alias
    such as ``CurrentUser`` is a ``typing`` object wrapping an in-scope class)."""
    if name in local_imports:
        module_name, orig_name = local_imports[name]
        try:
            return getattr(importlib.import_module(module_name), orig_name, None)
        except Exception:
            return None
    return func_globals.get(name)


def _class_in_annotation_object(obj: object) -> type | None:
    """The in-scope class an annotation object names: the class itself, or
    the class inside ``Annotated[...]``, ``Optional[...]`` or ``X | None``."""
    if inspect.isclass(obj) and _in_scope(obj):
        return obj
    if typing.get_origin(obj) in (typing.Annotated, typing.Union, types.UnionType):
        for arg in typing.get_args(obj):
            cls = _class_in_annotation_object(arg)
            if cls is not None:
                return cls
    return None


def _annotated_class(annotation: ast.expr | None, lookup) -> type | None:
    """The in-scope class an annotation in source names, string
    annotations included (``from __future__ import annotations``)."""
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        try:
            annotation = ast.parse(annotation.value, mode="eval").body
        except SyntaxError:
            return None
    if isinstance(annotation, ast.BinOp):
        return _annotated_class(annotation.left, lookup) or _annotated_class(annotation.right, lookup)
    if isinstance(annotation, ast.Subscript):
        inner = annotation.slice.elts[0] if isinstance(annotation.slice, ast.Tuple) else annotation.slice
        return _annotated_class(inner, lookup)
    if isinstance(annotation, ast.Attribute) and isinstance(annotation.value, ast.Name):
        return _class_in_annotation_object(getattr(lookup(annotation.value.id), annotation.attr, None))
    if isinstance(annotation, ast.Name):
        return _class_in_annotation_object(lookup(annotation.id))
    return None


def _function_node(tree: ast.AST) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """The outermost function definition in a parsed function source."""
    return next((n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))), None)


def _parameter_classes(tree: ast.AST, local_imports: dict, func_globals: dict) -> dict[str, object]:
    """Blind spot B5: parameters whose annotation names an in-scope class
    (``user: CurrentUser`` → ``UserPrincipal``), so ``user.m()`` is followed."""
    node = _function_node(tree)
    if node is None:
        return {}
    args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    classes = {a.arg: _annotated_class(a.annotation, lambda n: _lookup(n, local_imports, func_globals)) for a in args}
    return {name: cls for name, cls in classes.items() if cls is not None}


@functools.cache
def _returned_class(func: object) -> type | None:
    """Blind spot B5: the in-scope class a function returns: its return
    annotation or, unannotated, the class of a ``return Cls(...)``."""
    try:
        tree = _parse_source(inspect.getsource(func))
    except (OSError, TypeError):
        return None
    local_imports = _local_imports(tree)
    func_globals = getattr(func, "__globals__", {})
    node = _function_node(tree)
    cls = _annotated_class(node.returns if node else None, lambda n: _lookup(n, local_imports, func_globals))
    if cls is not None:
        return cls
    for ret in ast.walk(tree):
        if isinstance(ret, ast.Return) and isinstance(ret.value, ast.Call) and isinstance(ret.value.func, ast.Name):
            candidate = _resolve_name(ret.value.func.id, local_imports, func_globals)
            if inspect.isclass(candidate):
                return candidate
    return None


def _called_class(call: ast.expr, resolve) -> type | None:
    """The class of the object a call expression produces: ``Cls(...)``, or
    ``f(...)`` for a function ``f`` that returns an in-scope class (B5)."""
    if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)):
        return None
    target = resolve(call.func.id)
    if inspect.isclass(target):
        return target
    return _returned_class(target) if inspect.isfunction(target) else None


def _call_targets(func: object, tree: ast.AST) -> list[object]:
    """The in-scope functions a function calls, by these call shapes:
    ``f()``; ``module.f()``; ``Cls()`` (its ``__init__``); ``Cls.m()``,
    ``Cls(...).m()``, ``var.m()`` where ``var = Cls(...)`` in the same
    function, and ``self.m()``/``cls.m()`` inside a method of ``Cls``.
    ``f(...).m()``, ``var.m()`` where ``var = [await] f(...)``, and
    ``param.m()`` for an annotated parameter follow the class ``f`` returns
    or the annotation names (B5); reading a property on any of these runs
    its getter (B8). Only the named member is followed, never the rest of
    a class."""
    local_imports = _local_imports(tree)
    func_globals = getattr(func, "__globals__", {})

    def resolve(name: str) -> object | None:
        return _resolve_name(name, local_imports, func_globals)

    qual_parts = getattr(func, "__qualname__", "").split(".")
    owner = func_globals.get(qual_parts[0]) if len(qual_parts) == 2 else None
    instances: dict[str, object] = _parameter_classes(tree, local_imports, func_globals)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            value = node.value.value if isinstance(node.value, ast.Await) else node.value
            cls = _called_class(value, resolve)
            if cls is not None:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                instances |= {t.id: cls for t in targets if isinstance(t, ast.Name)}
    def holder_of(base: ast.expr) -> object | None:
        if isinstance(base, ast.Name) and base.id in ("self", "cls"):
            holder = owner
        elif isinstance(base, ast.Name):
            holder = instances.get(base.id) or resolve(base.id)
        elif isinstance(base, ast.Call):
            holder = _called_class(base, resolve)
        else:
            return None
        return holder if (inspect.ismodule(holder) or inspect.isclass(holder)) and _in_scope(holder) else None

    called = {id(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
    found: dict[str, object] = {}
    for node in ast.walk(tree):
        target: object | None = None
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            target = resolve(node.func.id)
            if inspect.isclass(target):
                target = target.__init__
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            holder = holder_of(node.func.value)
            if holder is not None:
                target = getattr(holder, node.func.attr, None)
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load) and id(node) not in called:
            # Blind spot B8: reading a property runs its getter.
            holder = holder_of(node.value)
            static = inspect.getattr_static(holder, node.attr, None) if inspect.isclass(holder) else None
            if isinstance(static, property):
                target = static.fget
            elif isinstance(static, functools.cached_property):
                target = static.func
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
    "PLATFORM_ADMIN_ROLE_NAMES",  # role names read as platform admin (B8)
}
# Keywords that elevate a repository, principal or service unless passed a
# literal False (blind spot B4: a computed value elevates as surely as True).
_ELEVATING_KEYWORDS = {"is_superuser", "is_platform_admin", "bypass"}
# Parameters that carry a flag the caller decided (blind spot B3).
_FLAG_PARAMETER = re.compile(
    r"is_superuser|is_platform_admin|is_provider_org|bypass|scope_bypass|caller_is_superuser|\w+_is_platform_admin"
)
# The shared subject of engine, service and embed tokens, and the
# predicates that test for it (blind spot B7).
_SENTINEL_IDENTITIES = {"SYSTEM_USER_UUID", "SYSTEM_USER_ID", "ENGINE_USER_ID"}
_SENTINEL_PREDICATES = {"is_engine_user", "is_service_principal"}
# Principal classes whose flag-reading methods and properties are elevated
# tokens wherever they are used (blind spot B8).
_PRINCIPAL_CLASSES = (
    "src.core.principal.UserPrincipal",
    "src.services.authorization.enforce.Caller",
    "src.services.authorization.context.AuthorizationContext",
    "src.services.mcp_server.server.MCPContext",
    "src.services.authorization.explain.RunUser",
)
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


def _terminal_name(expr: ast.AST) -> str | None:
    """``x`` for ``x``, ``a.b.x`` and ``str(x)``/``UUID(x)``."""
    if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name) and expr.func.id in ("str", "UUID"):
        return _terminal_name(expr.args[0]) if len(expr.args) == 1 else None
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        return expr.attr
    return None


def _string_keyed_flag_read(node: ast.AST) -> str | None:
    """Blind spot B1: a flag read by string key, ``getattr(x, '<flag>')``,
    ``x.get('<flag>')`` or ``x['<flag>']`` (an attribute scan misses all three)."""
    key: ast.AST | None = None
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id == "getattr" and len(node.args) >= 2:
            key = node.args[1]
        elif isinstance(node.func, ast.Attribute) and node.func.attr == "get" and node.args:
            key = node.args[0]
    elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
        key = node.slice
    if isinstance(key, ast.Constant) and key.value in _ELEVATED_ATTRS:
        return f"'{key.value}'"
    return None


def _elevating_keyword(node: ast.AST) -> str | None:
    """Blind spot B4: an ``is_superuser=``/``is_platform_admin=``/``bypass=``
    keyword whose value is anything but literal False (``not external``,
    ``is_admin``, ``self._has_scope_bypass`` elevate as surely as True)."""
    if not (isinstance(node, ast.keyword) and node.arg in _ELEVATING_KEYWORDS):
        return None
    value = node.value
    if isinstance(value, ast.Constant) and value.value is False:
        return None
    return f"{node.arg}=True" if isinstance(value, ast.Constant) and value.value is True else f"{node.arg}=<computed>"


def _sentinel_identity_check(node: ast.AST) -> str | None:
    """Blind spot B7: a comparison with the engine/system subject, or a call
    to a predicate that tests for it. Engine, service and embed tokens all
    carry that subject, so the comparison decides as much as a flag."""
    if isinstance(node, ast.Compare):
        for operand in (node.left, *node.comparators):
            name = _terminal_name(operand)
            if name in _SENTINEL_IDENTITIES:
                return f"== {name}"
    if isinstance(node, ast.Call):
        name = _terminal_name(node.func)
        if name in _SENTINEL_PREDICATES:
            return f"{name}()"
    return None


def _condition_tests(tree: ast.AST) -> list[ast.AST]:
    """The expressions a branch is decided on: if/while/ternary/assert
    tests, the operands of a boolean expression, comprehension filters."""
    tests: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.While, ast.IfExp, ast.Assert)):
            tests.append(node.test)
        elif isinstance(node, ast.BoolOp):
            tests.extend(node.values)
        elif isinstance(node, ast.comprehension):
            tests.extend(node.ifs)
    return tests


def _flag_parameter_branches(tree: ast.AST) -> list[tuple[ast.AST, str]]:
    """Blind spot B3: a branch on a parameter whose name carries a flag
    (``is_superuser``, ``bypass``, ``actor_is_platform_admin``, ...), so a
    helper deciding on a flag its caller passed is a site of its own."""
    params = {
        arg.arg
        for func in ast.walk(tree)
        if isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
        for arg in (*func.args.posonlyargs, *func.args.args, *func.args.kwonlyargs)
        if _FLAG_PARAMETER.fullmatch(arg.arg)
    }
    return [
        (node, f"param {node.id}")
        for test in _condition_tests(tree)
        for node in ast.walk(test)
        if isinstance(node, ast.Name) and node.id in params
    ]


def _has_source(func: object) -> bool:
    try:
        inspect.getsource(func)
    except (OSError, TypeError):
        return False
    return True


def _member_functions(cls: type) -> dict[str, object]:
    """The functions a class defines itself in source: methods, and the
    getters of properties and cached properties (not generated dataclass
    methods)."""
    members: dict[str, object] = {}
    for name, value in list(vars(cls).items()):
        if name.startswith("__annotate"):
            continue
        func = getattr(value, "fget", None) or getattr(value, "func", None) or getattr(value, "__func__", value)
        if inspect.isfunction(func) and _has_source(func):
            members[name] = func
    return members


def _reads_a_flag(tree: ast.AST, names: set[str]) -> bool:
    """Whether a source reads a flag, own copies included (``self.<flag>``)."""
    return any(
        (isinstance(node, ast.Attribute) and node.attr in names and isinstance(node.ctx, ast.Load))
        or (isinstance(node, ast.Name) and node.id in _ELEVATED_NAMES)
        or _string_keyed_flag_read(node)
        for node in ast.walk(tree)
    )


_SOURCE_ROOTS = ("src", "shared", "bifrost")


@functools.cache
def _source_modules() -> tuple[tuple[str, ast.Module], ...]:
    """``(module name, parsed source)`` for every file in the scanned packages."""
    found: list[tuple[str, ast.Module]] = []
    for root in _SOURCE_ROOTS:
        for path in sorted((_API_ROOT / root).rglob("*.py")):
            rel = path.relative_to(_API_ROOT).with_suffix("")
            parts = rel.parts[:-1] if rel.name == "__init__" else rel.parts
            found.append((".".join(parts), ast.parse(path.read_text())))
    return tuple(found)


def _class_defs(prefix: str, body: list[ast.stmt]) -> list[tuple[str, ast.ClassDef]]:
    """``(qualified name, node)`` for every class defined outside a
    function, nested classes and classes under a module-level ``if`` included."""
    found: list[tuple[str, ast.ClassDef]] = []
    for node in body:
        if isinstance(node, ast.ClassDef):
            found.append((f"{prefix}.{node.name}", node))
            found.extend(_class_defs(f"{prefix}.{node.name}", node.body))
        elif isinstance(node, (ast.If, ast.Try)):
            found.extend(_class_defs(prefix, [*node.body, *node.orelse, *getattr(node, "finalbody", [])]))
    return found


def _owned_nodes(module: str, tree: ast.Module) -> dict[str, list[ast.AST]]:
    """Every node of a module grouped by its owner: the outermost function
    (``module.Cls.method``), else the module-level name it is assigned to
    (``module.NAME``), else ``module.<module>``."""
    owned: dict[str, list[ast.AST]] = {}

    def visit(body: list[ast.stmt], prefix: str) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                owned.setdefault(f"{prefix}.{node.name}", []).extend(ast.walk(node))
            elif isinstance(node, ast.ClassDef):
                visit(node.body, f"{prefix}.{node.name}")
            elif isinstance(node, (ast.If, ast.Try)):
                visit([*node.body, *node.orelse, *getattr(node, "finalbody", [])], prefix)
                for handler in getattr(node, "handlers", []):
                    visit(handler.body, prefix)
            else:
                target = node.targets[0] if isinstance(node, ast.Assign) and len(node.targets) == 1 else None
                target = node.target if isinstance(node, ast.AnnAssign) else target
                name = target.id if isinstance(target, ast.Name) and prefix == module else "<module>"
                owner = prefix if prefix != module else f"{module}.{name}"
                owned.setdefault(owner, []).extend(ast.walk(node))

    visit(tree.body, module)
    return owned


@functools.cache
def _nodes_by_owner() -> dict[str, list[ast.AST]]:
    """``_owned_nodes`` for every scanned module."""
    owned: dict[str, list[ast.AST]] = {}
    for module, tree in _source_modules():
        owned |= _owned_nodes(module, tree)
    return owned


def _is_property(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(_terminal_name(d) in ("property", "cached_property") for d in func.decorator_list)


@functools.cache
def _principal_token_members() -> frozenset[str]:
    """Blind spot B8: members that decide on a flag out of sight. Each
    method and property of a principal class, and each property of any
    class, that reads a flag (directly or through another such member) is
    an elevated token itself: ``user.has_platform_admin_grant()`` and
    ``self._has_scope_bypass`` decide as much as ``user.is_superuser``."""
    candidates: list[tuple[str, ast.AST]] = [
        (member.name, member)
        for module, tree in _source_modules()
        for qualname, cls in _class_defs(module, tree.body)
        for member in cls.body
        if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not member.name.startswith("__")
        and (qualname in _PRINCIPAL_CLASSES or _is_property(member))
    ]
    tokens: set[str] = set()
    while True:
        names = _ELEVATED_ATTRS | _ELEVATED_NAMES | tokens
        grown = {name for name, node in candidates if _reads_a_flag(node, names)} - _ELEVATED_ATTRS - _ELEVATED_NAMES
        if grown <= tokens:
            return frozenset(tokens)
        tokens |= grown


def _elevated_token_nodes(tree: ast.AST) -> list[tuple[ast.AST, str]]:
    """``(node, token)`` for every elevated token in a parsed source."""
    found: list[tuple[ast.AST, str]] = []
    attrs = _ELEVATED_ATTRS | _ELEVATED_NAMES | _principal_token_members()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in attrs:
            # Writing the flag applies a decision made where the value came
            # from; self.is_superuser is the object's copy of a flag its
            # constructor was given. Both call sites are scanned instead
            # (and every constructor call site must be: see B11).
            is_own_copy = (
                isinstance(node.value, ast.Name) and node.value.id in ("self", "cls") and node.attr in _ELEVATED_ATTRS
            )
            if not (isinstance(node.ctx, ast.Store) or is_own_copy):
                found.append((node, node.attr))
        elif isinstance(node, ast.Name) and node.id in _ELEVATED_NAMES:
            found.append((node, node.id))
        else:
            token = _elevating_keyword(node) or _string_keyed_flag_read(node) or _sentinel_identity_check(node)
            if token:
                found.append((node, token))
    found.extend(_flag_parameter_branches(tree))
    return found


def _elevated_tokens_in(source: str) -> set[str]:
    return {token for _node, token in _elevated_token_nodes(_parse_source(source))}


# ---------------------------------------------------------------------------
# Whole-source inventories: shapes that matter wherever they are, reached
# from a scanned entry or not. Each rule maps a node to a short token; the
# inventory is every owner (function or module-level name) with a token.
# ---------------------------------------------------------------------------


def _token_minting(node: ast.AST) -> str | None:
    """Blind spot B2: a dict literal with a flag key whose value is not
    literal False (token claims and hand-off dicts a principal is rebuilt
    from), and every ``create_access_token(`` call."""
    if isinstance(node, ast.Dict):
        for key, value in zip(node.keys, node.values):
            is_false = isinstance(value, ast.Constant) and value.value is False
            if isinstance(key, ast.Constant) and key.value in _ELEVATED_ATTRS and not is_false:
                return f"{{'{key.value}': ...}}"
    if isinstance(node, ast.Call) and _terminal_name(node.func) == "create_access_token":
        return "create_access_token()"
    return None


def _flag_policy_seed(node: ast.AST) -> str | None:
    """Blind spot B9: a policy body condition on a flag, ``{"user": "<flag>"}``
    (the shape of ``when: {user: is_platform_admin}``)."""
    if isinstance(node, ast.Dict):
        for key, value in zip(node.keys, node.values):
            if (
                isinstance(key, ast.Constant)
                and key.value == "user"
                and isinstance(value, ast.Constant)
                and value.value in _ELEVATED_ATTRS
            ):
                return f"user: {value.value}"
    return None


# Redis commands that read a value back (blind spot B10). The ones only a
# Redis client has count on any receiver; the generic ones (get, exists, ...)
# count on a receiver that is visibly a Redis client.
_REDIS_ONLY_READS = {"hget", "hgetall", "hmget", "xrange", "xrevrange", "xread", "smembers", "sismember", "blpop"}
_REDIS_GENERIC_READS = {"get", "getdel", "mget", "exists", "lrange"}


def _redis_reads(nodes: list[ast.AST]) -> list[str]:
    """Blind spot B10: Redis reads in one owner's nodes. A receiver is a
    Redis client when its expression names redis (``self._redis``,
    ``redis_client``) or it is a local bound from a function named for
    redis or the redis module (``r = await get_shared_redis()``,
    ``async with get_redis() as r``, ``client = redis.from_url(...)``)."""

    def opens_redis(expr: ast.expr) -> bool:
        if not isinstance(expr, ast.Call):
            return False
        module = expr.func.value if isinstance(expr.func, ast.Attribute) else None
        return "redis" in (_terminal_name(expr.func) or "").lower() or (
            isinstance(module, ast.Name) and module.id in ("redis", "aioredis")
        )

    bound: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.Assign):
            if opens_redis(node.value.value if isinstance(node.value, ast.Await) else node.value):
                bound |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.withitem) and isinstance(node.optional_vars, ast.Name):
            if opens_redis(node.context_expr):
                bound.add(node.optional_vars.id)
    reads: list[str] = []
    for node in nodes:
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        method, receiver = node.func.attr, node.func.value
        visibly_redis = "redis" in ast.unparse(receiver).lower() or (
            isinstance(receiver, ast.Name) and receiver.id in bound
        )
        if method in _REDIS_ONLY_READS or (method in _REDIS_GENERIC_READS and visibly_redis):
            reads.append(f"{ast.unparse(receiver)}.{method}")
    return reads


_SUBPROCESS_CALLS = {"run", "Popen", "check_call", "check_output", "call", "create_subprocess_exec"}
# Package tools run code the package author wrote (setup.py, npm scripts).
_PACKAGE_TOOL = re.compile(r"pip|pip3|npm|npx|yarn|pnpm|uv")


def _unscrubbed_package_subprocess(node: ast.AST) -> str | None:
    """Blind spot B12: a subprocess that runs a package tool (its argv names
    ``pip``, ``npm``, ``npx``, ``yarn``, ``pnpm`` or ``uv``) and passes no
    ``env=``, so the tool's install and build scripts inherit every
    credential in the platform's environment."""
    if not (isinstance(node, ast.Call) and _terminal_name(node.func) in _SUBPROCESS_CALLS):
        return None
    receiver = node.func.value if isinstance(node.func, ast.Attribute) else None
    if not (isinstance(receiver, ast.Name) and receiver.id in ("subprocess", "asyncio")):
        return None
    if any(kw.arg == "env" for kw in node.keywords):
        return None
    tools = [
        const.value
        for arg in node.args
        for const in ast.walk(arg)
        if isinstance(const, ast.Constant) and isinstance(const.value, str) and _PACKAGE_TOOL.fullmatch(const.value)
    ]
    return f"{_terminal_name(node.func)}({tools[0]})" if tools else None


def _redis_inventory() -> dict[str, set[str]]:
    """Owner → Redis reads (B10) across ``src`` and ``shared`` (the server)
    and ``bifrost`` (the workflow runtime and the write-buffer flush)."""
    return {owner: set(reads) for owner, nodes in _nodes_by_owner().items() if (reads := _redis_reads(nodes))}


def _own_flag_classes(classes: list[tuple[str, ast.ClassDef]]) -> set[str]:
    """Blind spot B11: names of the classes whose methods read their own
    flag copy (``self.is_superuser``), and of every subclass of one."""
    names = {
        cls.name
        for _qualname, cls in classes
        if any(
            isinstance(node, ast.Attribute)
            and node.attr in _ELEVATED_ATTRS
            and isinstance(node.ctx, ast.Load)
            and isinstance(node.value, ast.Name)
            and node.value.id in ("self", "cls")
            for node in ast.walk(cls)
        )
    }
    while True:
        grown = {
            cls.name
            for _q, cls in classes
            if any(_terminal_name(b.value if isinstance(b, ast.Subscript) else b) in names for b in cls.bases)
        } - names
        if not grown:
            return names
        names |= grown


def _constructor_sites(class_names: set[str]) -> dict[str, set[str]]:
    """Class name → owners that construct it (``Cls(...)``/``mod.Cls(...)``)."""
    sites: dict[str, set[str]] = {}
    for owner, nodes in _nodes_by_owner().items():
        for node in nodes:
            if isinstance(node, ast.Call) and _terminal_name(node.func) in class_names:
                sites.setdefault(_terminal_name(node.func), set()).add(owner)
    return sites


def _inventory(rule, roots: tuple[str, ...] = _SOURCE_ROOTS) -> dict[str, set[str]]:
    """Owner → tokens for every owner in the scanned packages under
    ``roots`` where a per-node rule fires."""
    found: dict[str, set[str]] = {}
    for owner, nodes in _nodes_by_owner().items():
        if not owner.startswith(tuple(f"{root}." for root in roots)):
            continue
        tokens = {token for node in nodes if (token := rule(node))}
        if tokens:
            found[owner] = tokens
    return found


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


# ---------------------------------------------------------------------------
# Blind spot B6: entry points that are not an access-list route or tool.
# Each returns label → root functions; their reach is scanned like a route's.
# ---------------------------------------------------------------------------


def _unbound(func: object) -> object:
    """The plain function behind a bound method or a decorator wrapper
    (``@asynccontextmanager`` lifespans)."""
    return inspect.unwrap(getattr(func, "__func__", func))


def _in_scope_objects(value: object) -> list[object]:
    """In-scope objects a middleware holds: the value itself, or one level
    down (the MCP bearer backend holds the auth provider as its verifier)."""
    if _in_scope(type(value)):
        return [value]
    return [held for held in vars(value).values() if _in_scope(type(held))] if hasattr(value, "__dict__") else []


def _object_methods(obj: object) -> list[object]:
    """Every method an in-scope object's class defines, its ``__call__``
    included: whatever holds the object can call any of them."""
    return [
        func
        for cls in type(obj).__mro__
        if _in_scope(cls)
        for name, func in _member_functions(cls).items()
        if not name.startswith("__") or name == "__call__"
    ]


def _middleware_roots(middleware: list) -> list[object]:
    """The functions a Starlette middleware stack runs: in-scope middleware
    classes (``dispatch``/``__call__``), ``@app.middleware`` functions, and
    in-scope objects passed to a middleware."""
    roots: list[object] = []
    for mw in middleware:
        if _in_scope(mw.cls):
            roots.extend(getattr(mw.cls, name) for name in ("dispatch", "__call__") if name in vars(mw.cls))
        for value in mw.kwargs.values():
            if inspect.isfunction(value) and _in_scope(value):
                roots.append(value)
            else:
                for held in _in_scope_objects(value):
                    roots.extend(_object_methods(held))
    return roots


def _mount_roots() -> dict[str, list[object]]:
    """Starlette routes and mounts outside the access list: each mounted
    app's in-scope ASGI layers, routes, middleware (FastMCP ``on_*``
    middleware included) and lifespan, plus the dynamically registered MCP
    ``WorkflowTool.run``."""
    from starlette.routing import Mount, Route

    from src.services.mcp_server import server as mcp_server

    roots: dict[str, list[object]] = {
        "MCP WorkflowTool.run": [mcp_server.WorkflowTool.run],
        "APP middleware": _middleware_roots(app.user_middleware),
    }
    for route in app.routes:
        if isinstance(route, Route) and not isinstance(route, APIRoute) and _in_scope(_unbound(route.endpoint)):
            roots[f"ROUTE {route.path}"] = [_unbound(route.endpoint)]
        if not isinstance(route, Mount):
            continue
        layers: list[object] = []
        layer: object | None = route.app
        while layer is not None:
            if _in_scope(type(layer)):
                layers.append(type(layer).__call__)
            layers.extend(
                _unbound(r.endpoint)
                for r in getattr(layer, "routes", ())
                if isinstance(r, Route) and _in_scope(_unbound(r.endpoint))
            )
            layers.extend(_middleware_roots(getattr(layer, "user_middleware", [])))
            fastmcp_server = getattr(getattr(layer, "state", None), "fastmcp_server", None)
            for mw in getattr(fastmcp_server, "middleware", ()):
                if _in_scope(type(mw)):
                    layers.extend(_object_methods(mw))
            lifespan = getattr(layer, "lifespan", None)
            if inspect.isfunction(lifespan) and _in_scope(lifespan):
                layers.append(lifespan)
            layer = getattr(layer, "app", None)
        roots[f"MOUNT {route.path or '/'}"] = layers
    return roots


def _consumer_classes() -> list[type]:
    """Every RabbitMQ consumer the worker runs."""
    import src.worker.app  # noqa: F401 - registers every consumer class the worker runs
    from src.jobs.rabbitmq import _AbstractConsumer

    found: list[type] = []
    pending = [_AbstractConsumer]
    while pending:
        for sub in pending.pop().__subclasses__():
            found.append(sub)
            pending.append(sub)
    return found


def _scheduler_job_functions() -> list[object]:
    """The functions the scheduler hands to ``add_job`` (positionally or in
    ``args=[...]``)."""
    module = importlib.import_module("src.scheduler.main")
    tree = ast.parse(Path(inspect.getfile(module)).read_text())
    local_imports = _local_imports(tree)
    found: dict[str, object] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _terminal_name(node.func) == "add_job"):
            continue
        candidates = list(node.args)
        for kw in node.keywords:
            if kw.arg == "args" and isinstance(kw.value, (ast.List, ast.Tuple)):
                candidates.extend(kw.value.elts)
        for candidate in candidates:
            if isinstance(candidate, ast.Name):
                func = _resolve_name(candidate.id, local_imports, vars(module))
                if inspect.isfunction(func):
                    found[_qualname(func)] = func
    return [found[key] for key in sorted(found)]


def _module_functions(module: types.ModuleType) -> list[object]:
    """Every function and method a module defines."""
    found: list[object] = []
    for value in vars(module).values():
        if getattr(value, "__module__", None) != module.__name__:
            continue
        if inspect.isfunction(value):
            found.append(value)
        elif inspect.isclass(value):
            found.extend(_member_functions(value).values())
    return found


def _sdk_package_functions() -> list[object]:
    """Every function in the ``bifrost`` package: the code workflow runs
    import, and the client the CLI runs."""
    import bifrost

    found: list[object] = []
    for info in pkgutil.walk_packages(bifrost.__path__, "bifrost."):
        found.extend(_module_functions(importlib.import_module(info.name)))
    return found


def _extra_entry_roots() -> dict[str, list[object]]:
    """Blind spot B6: every entry point the access list does not hold, as
    label → root functions. The authentication resolvers and the execution
    worker process are here too: they construct principals and contexts
    whose own flag copies the scan otherwise trusts (B11)."""
    from src.jobs.platform.registry import list_platform_job_definitions
    from src.main import lifespan
    from src.services.execution import template_process

    roots = _mount_roots()
    roots |= {f"JOB {d.job_type}": [d.handler] for d in list_platform_job_definitions()}
    roots |= {
        f"CONSUMER {cls.__name__}": [cls.process_message]
        for cls in _consumer_classes()
        if "process_message" in vars(cls) and not getattr(cls.process_message, "__isabstractmethod__", False)
    }
    roots |= {f"SCHEDULE {func.__qualname__}": [func] for func in _scheduler_job_functions()}
    roots["STARTUP lifespan"] = [lifespan]
    roots["AUTH resolvers"] = [*_GATE_FUNCS, auth_mod.get_current_user_optional, auth_mod.get_current_user_ws]
    roots["WORKER process"] = [template_process._template_subprocess_entry]
    roots["SDK bifrost package"] = _sdk_package_functions()
    return roots


def _extra_entry_functions() -> dict[str, list[tuple[str, str]]]:
    """``(qualified name, source)`` of every function each extra entry
    reaches, minus the plumbing (as ``_entry_functions`` for a route)."""
    reached: dict[str, list[tuple[str, str]]] = {}
    for label, roots in _extra_entry_roots().items():
        visited: set[int] = set()
        found = [pair for root in roots for pair in _reachable_functions(_unbound(root), None, visited)]
        reached[label] = [(_qualname(f), s) for f, s in found if _qualname(f) not in _ELEVATED_PLUMBING]
    return reached


# Functions that mention an elevated token without deciding anything about
# the caller. Each must be agreed by a reviewer; never list a function that
# branches on the flag to allow or widen something (the plumbing test below
# fails if a listed function uses a token in a condition, or computes an
# elevation anywhere: see ``_plumbing_disqualifiers``).
_ELEVATED_PLUMBING: dict[str, str] = {
    "shared.sdk_users.list_users": "Filters the listed users by their own flag (?type=platform); not the caller's.",
    "src.routers.files.test_file_policy_access": (
        "Reports whether the tested principal passes workspace access; the caller is gated by the route."
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


def _plumbing_disqualifiers(tree: ast.AST) -> set[str]:
    """What keeps a function off the plumbing and flag-carrier lists: an
    elevated token inside a branch condition (if/while/ternary/assert/
    boolean expression/comprehension filter), or anywhere an elevating
    keyword (B4), a string-keyed flag read (B1) or a sentinel identity
    check (B7) — computed elevation decides as much as a branch does."""
    in_a_test = {id(node) for test in _condition_tests(tree) for node in ast.walk(test)}
    found = {token for node, token in _elevated_token_nodes(tree) if id(node) in in_a_test}
    for node in ast.walk(tree):
        token = _elevating_keyword(node) or _string_keyed_flag_read(node) or _sentinel_identity_check(node)
        if token:
            found.add(token)
    return found


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


@pytest.fixture(scope="module")
def scanned_functions(entry_functions) -> dict[str, list[tuple[str, str]]]:
    """Entry label → reached ``(qualified name, source)``: every access-list
    entry plus every extra entry point (B6)."""
    by_target = {_entry_target(entry): entry_functions[entry.key] for entry in ACCESS_LIST}
    return by_target | _extra_entry_functions()


# Every elevated-check site, mapped to what replaces the flag at the cutover:
#   "reach": the flag only widens which organizations or rows the caller
#            sees; it becomes the run user's reach.
#   "route": the site is a route's own gate (a superuser/bypass dependency
#            or the evaluator's admin short-circuit); the entry's named
#            permission replaces it.
#   "delete": a follow-up change removes the branch outright.
#   "cutover": closed at the cutover (workflows stop calling as superuser,
#            Redis-held authority goes).
#   "system": internal work on an object an entry already authorized (a
#            platform job, an import); it moves to the platform service
#            principal at the cutover.
#   a permission string: the power the flag unlocks. No such permission is
#            held by the User or Platform Operator role.
# A site with several checks maps to a tuple of these.
_ELEVATED_SITES: dict[str, str | tuple[str, ...]] = {
    "bifrost._context.resolve_scope": "cutover",
    "bifrost.cli._run_direct": "cutover",
    "bifrost.solution_dev.function_host.set_dev_execution_context": "cutover",
    "shared.claims.preresolve._load_source_policies": "system",
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
    "shared.scope_resolver.has_scope_bypass": "reach",
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
    "shared.sdk_config.resolve_sdk_scope": "reach",
    "shared.sdk_context.get_sdk_context": "reach",
    "shared.sdk_execution_reads.get_sdk_execution": ("executions.read.all", "executions.read"),
    "shared.sdk_execution_reads.list_sdk_executions": ("executions.read.all", "reach"),
    "shared.sdk_execution_reads.list_sdk_workflows": "reach",
    "shared.sdk_forms.check_form_access": "reach",
    "shared.sdk_forms.get_sdk_form": ("reach", "forms.read"),
    "shared.sdk_forms.list_sdk_forms": ("reach", "forms.read"),
    "shared.sdk_integrations.delete_sdk_integration_mapping": "reach",
    "shared.sdk_integrations.get_sdk_integration_dict": "reach",
    "shared.sdk_integrations.get_sdk_integration_mapping_dict": "reach",
    "shared.sdk_integrations.list_sdk_integration_mappings": "reach",
    "shared.sdk_integrations.refresh_sdk_oauth_token": "reach",
    "shared.sdk_integrations.upsert_sdk_integration_mapping": "reach",
    "shared.sdk_table_metadata.delete_sdk_table": "reach",
    "shared.sdk_table_metadata.list_sdk_tables": "reach",
    "shared.sdk_users._authorize_update": "privilegedaccess.readwrite",
    "shared.sdk_users.bulk_update_users": ("privilegedaccess.readwrite", "userlifecycle.readwrite"),
    "shared.sdk_users.create_user": "privilegedaccess.readwrite",
    "shared.sdk_users.update_user": "privilegedaccess.readwrite",
    "shared.sdk_video.can_read_platform_job": ("platformjobs.read.all", "platformjobs.readwrite.all"),
    "shared.sdk_workflow_execution.cancel_scheduled_sdk_execution": "executions.readwrite.all",
    "shared.sdk_workflow_execution.execute_sdk_workflow": ("repository.readwrite", "users.impersonate", "reach"),
    "shared.system_account_guard.is_system_account": "cutover",
    "shared.table_document_writes.resolve_attribution": "tableattribution.readwrite",
    "shared.table_resolution.assert_explicit_scope_targets_table": "reach",
    "shared.table_resolution.get_table_or_404": "reach",
    "src.core.auth.get_current_engine_or_bypass_user": "route",
    "src.core.auth.get_current_superuser": "route",
    "src.core.auth.get_current_user_optional": "cutover",
    "src.core.auth.get_current_user_ws": "cutover",
    "src.core.org_filter.resolve_org_filter": "reach",
    "src.core.org_filter.resolve_target_org": "reach",
    "src.core.principal.UserPrincipal.has_platform_admin_grant": "delete",
    "src.jobs.consumers.agent_run._caller_to_principal": "cutover",
    "src.jobs.platform.application_publish.run_application_publish": "system",
    "src.jobs.schedulers.deferred_execution_promoter.promote_due_executions": "reach",
    "src.repositories.applications.ApplicationRepository.update_application": "apps.readwrite",
    "src.repositories.oauth.OAuthTokenRepository.get_org_level_for_provider": "reach",
    "src.repositories.org_scoped.OrgScopedRepository._authenticated_tier_grants": "reach",
    "src.repositories.users.UserRepository.has_any_users": "cutover",
    "src.routers.agent_runs._is_platform_admin": "agentruns.readwrite.all",
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
    "src.routers.auth.get_current_user_info": "cutover",
    "src.routers.auth.register_user": "cutover",
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
    "src.routers.mcp.get_mcp_asgi_app": "cutover",
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
    "src.routers.metrics._get_recent_failures": "reach",
    "src.routers.metrics.get_metrics": "reach",
    "src.routers.notifications.dismiss_notification": "platform.readwrite",
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
    "src.routers.profile.delete_avatar": "cutover",
    "src.routers.profile.get_profile": "cutover",
    "src.routers.profile.update_profile": "cutover",
    "src.routers.profile.upload_avatar": "cutover",
    "src.routers.roles.get_role": "route",
    "src.routers.roles.list_roles": "route",
    "src.routers.sdk_modules._engine_module_scope": "cutover",
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
    "src.routers.websocket._load_policies_for_table": "system",
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
    "src.services.artifacts.ArtifactService.get_authorized": "artifacts.read.all",
    "src.services.artifacts.ArtifactService.list_workspace": "artifacts.read.all",
    "src.services.artifacts.ArtifactService.resolve_workspace_path": "artifacts.read.all",
    "src.services.artifacts.ArtifactService.store": "artifacts.readwrite.all",
    "src.services.authorization.enforce.Caller.is_platform_admin": "reach",
    "src.services.authorization.enforce.decide_for": "route",
    "src.services.authorization.enforce.permitted_organizations": "reach",
    "src.services.authorization.enforce.require_unprotected": "privilegedaccess.readwrite",
    "src.services.authorization.evaluator.decide": "route",
    "src.services.authorization.explain.RunUser.is_platform_admin": "reach",
    "src.services.authorization.explain.in_reach": "reach",
    "src.services.authorization.privilege.may_change_role_assignment": "privilegedaccess.readwrite",
    "src.services.chat_artifacts.execute_artifact_tool": "artifacts.read.all",
    "src.services.chat_runs._load_authorized_agent": "reach",
    "src.services.chat_runs.create_chat_run": "reach",
    "src.services.docs_indexer.index_platform_docs": "route",
    "src.services.embeddings.reindex.run_reindex_for_group": "system",
    "src.services.execution.agent_run_access.agent_run_visibility_conditions": ("agentruns.read.all", "reach"),
    "src.services.execution.agent_workflow_tools.execute_agent_workflow_tool": "reach",
    "src.services.execution.async_executor._publish_pending": "reach",
    "src.services.execution.async_executor.enqueue_code_execution": "reach",
    "src.services.execution.async_executor.enqueue_system_workflow_execution": "reach",
    "src.services.execution.async_executor.enqueue_workflow_execution": "reach",
    "src.services.execution.autonomous_agent_executor.AutonomousAgentExecutor._execute_knowledge_search": "reach",
    "src.services.execution.autonomous_agent_executor.AutonomousAgentExecutor._execute_system_tool": "reach",
    "src.services.execution.autonomous_agent_executor.AutonomousAgentExecutor._execute_tool": "reach",
    "src.services.execution.engine.execute": "cutover",
    "src.services.execution.service.execute_tool": "reach",
    "src.services.execution.worker.run_execution": "cutover",
    "src.services.file_policy_service.FilePolicyService._principal_matches_org": "reach",
    "src.services.file_policy_service.FilePolicyService.is_allowed": "reach",
    "src.services.identities.require_delegation": "delete",
    "src.services.manifest_import.ManifestResolver._index_agents_from_manifest": "system",
    "src.services.manifest_import.ManifestResolver._resolve_file_policy": "system",
    "src.services.manifest_import.ManifestResolver._resolve_table": "system",
    "src.services.mcp_server.gateway.MCPAgentGatewayService._agent_repo": "reach",
    "src.services.mcp_server.gateway.MCPAgentGatewayService._authorize_discovery_scope": "reach",
    "src.services.mcp_server.gateway.MCPAgentGatewayService._dispatch_delegation": "reach",
    "src.services.mcp_server.gateway.MCPAgentGatewayService._dispatch_system_tool": "reach",
    "src.services.mcp_server.gateway.MCPAgentGatewayService._dispatch_workflow": "reach",
    "src.services.mcp_server.gateway.MCPAgentGatewayService._has_scope_bypass": "reach",
    "src.services.mcp_server.gateway.MCPAgentGatewayService.get_execution": ("executions.read.all", "agentruns.read.all", "reach"),
    "src.services.mcp_server.middleware.ToolFilterMiddleware.on_call_tool": "cutover",
    "src.services.mcp_server.middleware.ToolFilterMiddleware.on_list_tools": "cutover",
    "src.services.mcp_server.server.MCPContext.has_scope_bypass": "reach",
    "src.services.mcp_server.server.WorkflowTool.run": "artifacts.read.all",
    "src.services.mcp_server.server._execute_workflow_tool_impl": "reach",
    "src.services.mcp_server.server._get_context_from_token": "cutover",
    "src.services.mcp_server.server._get_runtime_context": "cutover",
    "src.services.mcp_server.tool_access.MCPToolAccessService._build_workflow_repo": "reach",
    "src.services.mcp_server.tool_access.MCPToolAccessService._check_agent_access": "reach",
    "src.services.mcp_server.tool_access.MCPToolAccessService._get_accessible_agents": "reach",
    "src.services.mcp_server.tool_access.MCPToolAccessService.get_accessible_tools": "reach",
    "src.services.mcp_server.tool_access.MCPToolAccessService.get_tools_for_agent": "reach",
    "src.services.mcp_server.tools._org_scope.mcp_write_scope_bypass": "reach",
    "src.services.mcp_server.tools.apps.push_files": "repository.readwrite",
    "src.services.mcp_server.tools.code_editor._check_read_scope": "repository.read",
    "src.services.mcp_server.tools.code_editor._check_write_scope": "repository.readwrite",
    "src.services.mcp_server.tools.knowledge.search_knowledge": "reach",
    "src.services.policy_rule_service.PolicyRuleService._get": "reach",
    "src.services.solution_scope.derive_execution_solution_scope": "cutover",
    "src.services.solution_scope.is_engine_user": "cutover",
    "src.services.solution_scope.is_service_principal": "reach",
    "src.services.solution_scope.resolve_solution_table_by_name": "reach",
    "src.services.solution_scope.resolve_trustworthy_caller": "cutover",
    "src.services.solutions.deploy.SolutionDeployer._upsert_file_policies": "system",
    "src.services.solutions.deploy.SolutionDeployer._upsert_tables": "system",
    "src.services.solutions.workspace_bundle_import.WorkspaceBundleImporter._set_config_values": "system",
    "src.services.solutions.zip_install._apply_config_values": "system",
    "src.services.table_policy_loader.load_resolved_table_policies": "system",
    "src.services.user_access_map._place_for": "reach",
    "src.services.user_access_map.build_access_map": "cutover",
    "src.services.user_provisioning.ensure_user_provisioned": "cutover",
    "src.services.user_role_assignments._assignable_roles": "privilegedaccess.readwrite",
    "src.services.user_role_assignments.authorization_summary": "cutover",
    "src.services.user_role_assignments.boundary_placement": "reach",
    "src.services.user_role_assignments.check_boundaries": "reach",
    "src.services.user_role_assignments.check_role_change": "privilegedaccess.readwrite",
    "src.services.user_role_assignments.replace_role_assignments": "privilegedaccess.readwrite",
}
_SITE_KINDS = {"reach", "route", "delete", "cutover", "system"}

# Where a token is minted or a principal is handed on (blind spot B2), and
# what replaces the flag claim: "cutover" (the claim or hand-off goes with
# the flag checks) or "route" (a token minted with no flag claim).
_TOKEN_MINTING_SITES: dict[str, str] = {
    "shared.sdk_workflow_execution.insert_scheduled_execution": "cutover",
    "src.core.redis_client.RedisClient.set_pending_execution": "cutover",
    "src.core.security.authenticate_engine": "cutover",
    "src.core.security.create_embed_access_token": "route",
    "src.core.security.mint_engine_token": "cutover",
    "src.core.security.mint_service_token": "route",
    "src.jobs.consumers.workflow_execution.WorkflowExecutionConsumer.process_message": "cutover",
    "src.routers.auth._generate_login_tokens": "cutover",
    "src.routers.auth.mfa_initial_verify": "cutover",
    "src.routers.auth.refresh_token": "cutover",
    "src.routers.auth.register_user": "cutover",
    "src.routers.auth.setup_passkey_verify": "cutover",
    "src.routers.mfa.verify_mfa": "cutover",
    "src.routers.oauth_sso.oauth_callback": "cutover",
    "src.services.agent_executor.AgentExecutor.chat": "cutover",
    "src.services.execution.agent_run_service.enqueue_agent_run": "cutover",
    "src.services.mcp_server.auth.BifrostAuthProvider._callback": "cutover",
    "src.services.mcp_server.auth.BifrostAuthProvider._token": "cutover",
    "src.services.mcp_server.auth.BifrostAuthProvider.verify_token": "cutover",
    "src.services.mcp_server.tools._http_bridge._token_from_context": "cutover",
}
_MINTING_KINDS = {"cutover", "route"}

# Owners that only echo a flag into a response, a report, a request body or
# a field rule (B2's dict shape, but nothing is minted or handed on). Each
# is guarded like plumbing.
_FLAG_CARRIERS: dict[str, str] = {
    "bifrost._execution_context.ExecutionContext.to_public_dict": "Shows workflow code the run's own admin bit.",
    "bifrost.users.users.create": "Sends the requested user's flag to the API, which decides.",
    "shared.sdk_context.get_sdk_context": "Reports the caller's own flag in the SDK context response.",
    "shared.sdk_users.UPDATE_FIELD_PERMISSIONS": "Names the permission that changing a user's flag needs.",
    "shared.sdk_users.create_user": "Records the created user's own flag in the audit event.",
    "src.routers.mcp.mcp_status": "Reports the caller's own flag in the MCP status response.",
    "src.services.authorization.explain._run_user_step": "Copies the run user's flag into an explain trace.",
}

# Platform-seeded policy bodies that condition on a flag (blind spot B9);
# a follow-up change checks the Platform Admin role ID instead.
_FLAG_POLICY_SEEDS: dict[str, str] = {
    "shared.file_policies.make_seed_admin_bypass": "delete",
    "shared.policies.probe.make_seed_admin_bypass": "delete",
    "src.services.policy_rule_service._BUILTINS": "delete",
}

# Every Redis read (blind spot B10, ``_redis_reads``) is classified. An
# authority read feeds identity, authorization or code (who a run is for,
# a role, a sign-in code, a module's source, an embed's grant, a result a
# waiting caller trusts); workflow code can write Redis today, so each one
# closes at the cutover.
_REDIS_AUTHORITY_READS: dict[str, str] = {
    "bifrost._logging.read_logs_from_stream": "cutover",
    "bifrost._sync.flush_pending_changes": "cutover",
    "shared.form_runtime.load_startup_result": "cutover",
    "shared.form_runtime.validate_embed_upload_references": "cutover",
    "shared.role_cache.get_user_roles": "cutover",
    "shared.sdk_agent_runs.get_sdk_agent_run": "cutover",
    "src.core.embed_middleware.EmbedScopeMiddleware.dispatch": "cutover",
    "src.core.module_cache.get_module": "cutover",
    "src.core.module_cache.get_module_resolution_cache": "cutover",
    "src.core.module_cache_sync._get_cached_module_resolution": "cutover",
    "src.core.module_cache_sync._get_exact_scoped_module": "cutover",
    "src.core.module_cache_sync.get_module_sync": "cutover",
    "src.core.redis_client.RedisClient.get_endpoint_workflow_cache": "cutover",
    "src.core.redis_client.RedisClient.get_pending_execution": "cutover",
    "src.core.redis_client.RedisClient.get_workflow_metadata_cache": "cutover",
    "src.core.redis_client.RedisClient.wait_for_result": "cutover",
    "src.core.requirements_cache.get_requirements": "cutover",
    "src.core.requirements_cache.get_requirements_sync": "cutover",
    "src.jobs.consumers.agent_run.AgentRunConsumer.process_message": "cutover",
    "src.repositories.organizations.OrganizationRepository._get_from_cache": "cutover",
    "src.routers.auth.authorize_device": "cutover",
    "src.routers.auth.exchange_device_token": "cutover",
    "src.routers.oauth_sso.oauth_callback": "cutover",
    "src.routers.websocket.can_access_execution": "cutover",
    "src.services.app_storage.AppStorageService.get_render_cache": "cutover",
    "src.services.execution.agent_run_service.wait_for_agent_run_result": "cutover",
    "src.services.execution.engine._service_supervisor": "cutover",
    "src.services.github_sync.GitHubSyncService.load_connect_preview": "cutover",
    "src.services.mcp_server.auth.BifrostAuthProvider._callback": "cutover",
    "src.services.mcp_server.auth.BifrostAuthProvider._token": "cutover",
    "src.services.notification_service.NotificationService._is_admin_notification": "cutover",
    "src.services.notification_service.NotificationService.dismiss_notification": "cutover",
    "src.services.notification_service.NotificationService.get_notification": "cutover",
    "src.services.notification_service.NotificationService.get_user_notifications": "cutover",
    "src.services.passkey_service.PasskeyService.verify_authentication": "cutover",
    "src.services.passkey_service.PasskeyService.verify_registration": "cutover",
    "src.services.passkey_service.PasskeyService.verify_setup_registration": "cutover",
}
# Redis reads whose value never reaches a principal, a token, an
# authorization decision or code loading.
_REDIS_PLAIN_READS: dict[str, str] = {
    "bifrost._logging.flush_logs_to_postgres": "Moves a finished run's log lines into the database.",
    "shared.role_cache.invalidate_role": "Scans role-cache entries to delete them.",
    "src.core.cache.data_provider_cache.get_cached_result": "Cached data-provider options for a form field.",
    "src.core.cache.keys._current_global_version": "Cache version counter.",
    "src.core.locks.DistributedLockService.extend_lock": "Lock owner token compared before extending.",
    "src.core.locks.DistributedLockService.get_lock_info": "Lock diagnostics.",
    "src.core.locks.DistributedLockService.release_lock": "Lock owner token compared before releasing.",
    "src.core.module_cache.clear_module_cache": "Lists cached module keys to delete them.",
    "src.core.pubsub.replay_chat_run_events": "Replays a chat run's events to a subscriber authorized for the run.",
    "src.core.rate_limit.RateLimiter.get_remaining": "Rate-limit counter.",
    "src.core.redis_client.RedisClient.check_agent_run_cancel_flag": "Cancel flag; only stops a run.",
    "src.core.redis_client.RedisClient.get": "Generic accessor; each caller reads through a redis-named receiver and is classified itself.",
    "src.core.redis_client.RedisClient.get_active_execution": "Which worker holds a running execution (timeouts, cancel).",
    "src.core.redis_client.RedisClient.set_pending_cancelled": "Marks a queued run cancelled.",
    "src.core.redis_client.RedisClient.update_pending_execution": (
        "Read-modify-write of a queued run's record; its identity is trusted only where the run reads it."
    ),
    "src.core.repo_dirty.get_repo_dirty_since": "Workspace dirty timestamp.",
    "src.jobs.consumers.agent_run.AgentRunConsumer._cancel_watcher": "Cancel flag; only stops a run.",
    "src.jobs.schedulers.worker_metrics_sampling.sample_worker_metrics": "Worker metrics.",
    "src.jobs.schedulers.workflow_operation_usage_flush._drain_day_key": "Operation usage counters.",
    "src.repositories.config.ConfigRepository.merged_for_sdk": "Cached org config values, written from the database.",
    "src.routers.agent_runs.cancel_agent_run": "Marks a queued agent run cancelled.",
    "src.routers.events._get_rate_limited_count": "Rate-limit counter.",
    "src.routers.files.list_active_watchers": "Active file watchers (diagnostics).",
    "src.routers.health.check_redis": "Health probe.",
    "src.routers.jobs.get_job_status": "Status of a job the caller started.",
    "src.routers.packages.get_packages_from_workers": "Installed packages reported by workers.",
    "src.routers.platform.workers.get_pool": "Worker pool diagnostics.",
    "src.routers.platform.workers.get_pool_stats": "Worker pool diagnostics.",
    "src.routers.platform.workers.list_pools": "Worker pool diagnostics.",
    "src.routers.platform.workers.recycle_all_processes": "Checks a pool exists before asking it to recycle.",
    "src.routers.platform.workers.recycle_process": "Checks a pool exists before asking it to recycle.",
    "src.services.ai_usage_service._notify_missing_pricing": "Notification de-duplication marker.",
    "src.services.ai_usage_service.get_cached_pricing": "Model pricing cache.",
    "src.services.ai_usage_service.get_usage_totals": "AI usage totals cache.",
    "src.services.ai_usage_service.get_used_models": "Models seen in usage.",
    "src.services.embeddings.reindex.is_cancelled": "Cancel flag; only stops a reindex.",
    "src.services.execution.autonomous_agent_executor.AutonomousAgentExecutor._check_cancelled": (
        "Cancel flag; only stops a run."
    ),
    "src.services.execution.install_progress.report_phase": "Package install progress.",
    "src.services.execution.queue_tracker.get_all_pending_executions": "Queue positions for display.",
    "src.services.notification_service.NotificationService.find_admin_notification_by_title": (
        "De-duplicates an admin notification before creating one."
    ),
    "src.services.notification_service.NotificationService.update_notification": (
        "Rewrites a notification's status for its writer."
    ),
    "src.services.service_claim.ServiceClaimLoop._clear_reported_memory": "Service memory report.",
    "src.services.service_claim.ServiceClaimLoop._drain_ready": "Service ready flag.",
    "src.services.service_claim.ServiceClaimLoop._report_memory": "Service memory report.",
    "src.services.service_log_flush.flush_attempt_logs": "Moves a service attempt's log lines into the database.",
    "src.services.service_memory.read_service_memory": "Service memory report.",
}

# Package-tool subprocesses that pass no ``env=`` (blind spot B12), mapped
# to "hardening". Empty: every one passes a scrubbed environment.
_UNSCRUBBED_SUBPROCESS: dict[str, str] = {}


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

    def test_every_elevated_check_site_names_its_replacement(self, scanned_functions) -> None:
        sites: dict[str, tuple[set[str], set[str]]] = {}
        for target, functions in scanned_functions.items():
            for where, source in functions:
                tokens = _elevated_site_tokens(source)
                if tokens:
                    site_tokens, targets = sites.setdefault(where, (set(), set()))
                    site_tokens |= tokens
                    targets.add(target)
        unregistered = []
        for where in sorted(set(sites) - set(_ELEVATED_SITES)):
            tokens, targets = sites[where]
            examples = "; ".join(sorted(targets)[:3])
            unregistered.append(f"{where} | {', '.join(sorted(tokens))} | {len(targets)} entries: {examples}")
        stale = sorted(set(_ELEVATED_SITES) - set(sites))
        assert not unregistered and not stale, (
            f"{len(unregistered)} elevated-check sites with no replacement in _ELEVATED_SITES "
            "(qualified function | tokens | entries that reach it):\n"
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
            "_ELEVATED_SITES replacements that are not reach/route/delete/cutover/system/a permission:\n"
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
            deciding = _plumbing_disqualifiers(_parse_source(inspect.getsource(func)))
            if deciding:
                problems.append(f"{qualname}: decides on {sorted(deciding)}")
        assert not problems, "_ELEVATED_PLUMBING entries that are stale or decide something:\n" + "\n".join(
            problems
        )


def _registry_drift(found: set[str], registered: set[str]) -> list[str]:
    """``unregistered: x`` / ``stale: y`` lines for an inventory against its registry."""
    return [f"unregistered: {name}" for name in sorted(found - registered)] + [
        f"stale: {name}" for name in sorted(registered - found)
    ]


class TestElevatedInventories:
    """Shapes the reachability scan cannot be trusted with are inventoried
    across the whole source instead: token minting and principal hand-offs
    (B2), flag-conditioned seed policies (B9), Redis reads (B10), package
    tools run with the platform's environment (B12), and objects whose own
    flag copy the scan trusts (B11). Each registry fails on an unregistered
    owner and on a stale entry."""

    def test_every_token_minting_site_is_registered(self) -> None:
        found = _inventory(_token_minting)
        drift = _registry_drift(set(found), set(_TOKEN_MINTING_SITES) | set(_FLAG_CARRIERS))
        both = sorted(set(_TOKEN_MINTING_SITES) & set(_FLAG_CARRIERS))
        assert not drift and not both, (
            "token minting / flag hand-off owners vs _TOKEN_MINTING_SITES + _FLAG_CARRIERS:\n"
            + "\n".join(drift + [f"in both: {name}" for name in both])
        )

    def test_minting_replacements_are_cutover_or_route(self) -> None:
        invalid = sorted(f"{k}: {v}" for k, v in _TOKEN_MINTING_SITES.items() if v not in _MINTING_KINDS)
        assert not invalid, "_TOKEN_MINTING_SITES values must be cutover or route:\n" + "\n".join(invalid)

    def test_flag_carriers_only_echo_or_are_registered_sites(self) -> None:
        problems = []
        owned = _nodes_by_owner()
        for qualname in sorted(_FLAG_CARRIERS):
            if qualname not in owned:
                problems.append(f"{qualname}: no such function or module-level name")
                continue
            deciding = _plumbing_disqualifiers(owned[qualname][0])
            if deciding and qualname not in _ELEVATED_SITES:
                problems.append(f"{qualname}: decides on {sorted(deciding)} but is not in _ELEVATED_SITES")
        assert not problems, "_FLAG_CARRIERS entries that are stale or decide something:\n" + "\n".join(problems)

    def test_every_flag_conditioned_seed_policy_is_registered(self) -> None:
        drift = _registry_drift(set(_inventory(_flag_policy_seed)), set(_FLAG_POLICY_SEEDS))
        invalid = sorted(k for k, v in _FLAG_POLICY_SEEDS.items() if v != "delete")
        assert not drift and not invalid, (
            "seeded policy bodies on a flag vs _FLAG_POLICY_SEEDS (value: delete):\n" + "\n".join(drift + invalid)
        )

    def test_every_redis_read_is_classified(self) -> None:
        drift = _registry_drift(set(_redis_inventory()), set(_REDIS_AUTHORITY_READS) | set(_REDIS_PLAIN_READS))
        both = sorted(set(_REDIS_AUTHORITY_READS) & set(_REDIS_PLAIN_READS))
        invalid = sorted(k for k, v in _REDIS_AUTHORITY_READS.items() if v != "cutover")
        assert not drift and not both and not invalid, (
            "Redis reads vs _REDIS_AUTHORITY_READS (cutover) + _REDIS_PLAIN_READS (reason):\n"
            + "\n".join(drift + [f"in both: {n}" for n in both] + [f"not cutover: {n}" for n in invalid])
        )

    def test_package_tool_subprocesses_pass_an_environment(self) -> None:
        drift = _registry_drift(set(_inventory(_unscrubbed_package_subprocess, ("src", "shared"))), set(_UNSCRUBBED_SUBPROCESS))
        invalid = sorted(k for k, v in _UNSCRUBBED_SUBPROCESS.items() if v != "hardening")
        assert not drift and not invalid, (
            "package-tool subprocesses with no env= vs _UNSCRUBBED_SUBPROCESS (hardening):\n"
            + "\n".join(drift + invalid)
        )

    def test_own_flag_copies_are_built_only_where_the_scan_looks(self, scanned_functions) -> None:
        scanned = {where for functions in scanned_functions.values() for where, _source in functions}
        scanned |= set(_ELEVATED_PLUMBING)
        classes = [c for module, tree in _source_modules() for c in _class_defs(module, tree.body)]
        outside = sorted(
            f"{cls}: {owner}"
            for cls, owners in _constructor_sites(_own_flag_classes(classes)).items()
            for owner in owners
            if owner not in scanned
        )
        assert not outside, (
            "classes whose self.<flag> reads the scan trusts are constructed outside every scanned entry "
            "(add the entry point, or the flag copy goes unchecked):\n" + "\n".join(outside)
        )


class TestScannerBlindSpots:
    """Each scanner extension catches its shape in a minimal source."""

    @staticmethod
    def _nodes(source: str) -> list[ast.AST]:
        return list(ast.walk(ast.parse(textwrap.dedent(source))))

    def test_b1_string_keyed_flag_reads_are_tokens(self) -> None:
        source = """
            def check(user, claims):
                if getattr(user, "is_platform_admin", False) or claims.get("is_superuser"):
                    return claims["is_provider_org"]
                claims["is_superuser"] = False
        """
        assert _elevated_tokens_in(textwrap.dedent(source)) == {
            "'is_platform_admin'",
            "'is_superuser'",
            "'is_provider_org'",
        }

    def test_b2_flag_claims_and_token_minting_are_inventoried(self) -> None:
        minted = {t for n in self._nodes("create_access_token({'sub': u.id, 'is_superuser': u.is_superuser})") if (t := _token_minting(n))}
        assert minted == {"create_access_token()", "{'is_superuser': ...}"}
        assert not [n for n in self._nodes("x = {'is_superuser': False}") if _token_minting(n)]

    def test_b3_a_branch_on_a_flag_parameter_is_a_token(self) -> None:
        source = """
            def may_change(target, actor_is_platform_admin, bypass, verbose):
                send(bypass)
                if actor_is_platform_admin and verbose:
                    return target
        """
        assert _elevated_tokens_in(textwrap.dedent(source)) == {"param actor_is_platform_admin"}

    def test_b4_computed_elevating_keywords_are_tokens(self) -> None:
        source = """
            def repos(db, external, admin):
                Repo(db, is_superuser=not external)
                Service(db, bypass=admin)
                Repo(db, is_superuser=False)
        """
        assert _elevated_tokens_in(textwrap.dedent(source)) == {"is_superuser=<computed>", "bypass=<computed>"}

    def test_b5_returned_and_annotated_classes_are_followed(self) -> None:
        from src.core.principal import UserPrincipal
        from src.routers.mcp import _gateway_service
        from src.services.mcp_server.gateway import MCPAgentGatewayService

        lookup = {"UserPrincipal": UserPrincipal, "Optional": typing.Optional, "CurrentUser": auth_mod.CurrentUser}.get
        for annotation in ("UserPrincipal | None", "Optional[UserPrincipal]", "CurrentUser", "'UserPrincipal'"):
            assert _annotated_class(ast.parse(annotation, mode="eval").body, lookup) is UserPrincipal, annotation
        call = ast.parse("_gateway_service(user)", mode="eval").body
        assert _called_class(call, {"_gateway_service": _gateway_service}.get) is MCPAgentGatewayService

    def test_b6_middleware_and_scheduler_entries_are_found(self) -> None:
        from starlette.middleware import Middleware

        from src.core.embed_middleware import EmbedScopeMiddleware

        roots = _middleware_roots([Middleware(EmbedScopeMiddleware)])
        assert [_qualname(r) for r in roots] == ["src.core.embed_middleware.EmbedScopeMiddleware.dispatch"]
        assert "promote_due_executions" in {f.__name__ for f in _scheduler_job_functions()}
        labels = set(_extra_entry_roots())
        for label in ("MCP WorkflowTool.run", "MOUNT /", "JOB application.deploy", "CONSUMER AgentRunConsumer",
                      "STARTUP lifespan", "AUTH resolvers", "WORKER process", "SDK bifrost package"):
            assert label in labels, label

    def test_b7_sentinel_identity_checks_are_tokens(self) -> None:
        source = """
            def attribute(user, other):
                if user.user_id == SYSTEM_USER_UUID or str(SYSTEM_USER_ID) == other:
                    return is_engine_user(user)
        """
        assert _elevated_tokens_in(textwrap.dedent(source)) == {
            "== SYSTEM_USER_UUID",
            "== SYSTEM_USER_ID",
            "is_engine_user()",
        }

    def test_b8_flag_reading_members_are_tokens_and_properties_are_followed(self) -> None:
        assert {"has_platform_admin_grant", "_has_scope_bypass"} <= _principal_token_members()
        source = """
            def gate(self, user):
                return self._has_scope_bypass or user.has_platform_admin_grant() or self.is_superuser
        """
        assert _elevated_tokens_in(textwrap.dedent(source)) == {"_has_scope_bypass", "has_platform_admin_grant"}
        from src.services.mcp_server.gateway import MCPAgentGatewayService

        method = MCPAgentGatewayService._authorize_discovery_scope
        targets = {_qualname(t) for t in _call_targets(method, _parse_source(inspect.getsource(method)))}
        assert "src.services.mcp_server.gateway.MCPAgentGatewayService._has_scope_bypass" in targets

    def test_b9_flag_conditioned_policy_bodies_are_inventoried(self) -> None:
        seeded = {t for n in self._nodes("P = {'when': {'user': 'is_platform_admin'}}") if (t := _flag_policy_seed(n))}
        assert seeded == {"user: is_platform_admin"}
        assert not [n for n in self._nodes("P = {'when': {'user': 'email'}}") if _flag_policy_seed(n)]

    def test_b10_redis_reads_are_recognised_by_receiver(self) -> None:
        source = """
            async def read(self, key, cache):
                r = await get_shared_redis()
                await r.get(key)
                pending = await self._redis_client.get_pending_execution(key)
                pending.get("user_id")
                await self._redis.hget(key, "f")
                async with get_redis() as conn:
                    await conn.exists(key)
                cache.get(key)
        """
        assert _redis_reads(self._nodes(source)) == ["r.get", "self._redis.hget", "conn.exists"]

    def test_b11_own_flag_classes_include_subclasses(self) -> None:
        tree = ast.parse(textwrap.dedent("""
            class Base:
                def widen(self):
                    return self.is_superuser
            class Child(Base[int]):
                pass
            class Other:
                pass
        """))
        assert _own_flag_classes(_class_defs("m", tree.body)) == {"Base", "Child"}

    def test_b12_package_tools_without_an_environment_are_found(self) -> None:
        def found(source: str) -> set[str]:
            return {t for n in self._nodes(source) if (t := _unscrubbed_package_subprocess(n))}

        assert found("subprocess.run(['npm', 'install'])") == {"run(npm)"}
        assert found("asyncio.create_subprocess_exec(sys.executable, '-m', 'pip', 'install', 'x')") == {
            "create_subprocess_exec(pip)"
        }
        assert not found("subprocess.run(['npm', 'install'], env=package_tool_env())")
        assert not found("subprocess.run(['git', 'status'])")


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
