"""Derive SDK permission candidates from shipped transports and the access list.

Only platform SDK source is parsed here. Workspace source is handled by the
separate bounded analyzer. This avoids another hand-maintained SDK permission
catalog, and keeps conditional resource-policy and secret decisions visible.
"""

from __future__ import annotations

import ast
import hashlib
import re
from functools import lru_cache
from pathlib import Path

from src.models.contracts.access_list import AccessClass
from src.services.access_list import ACCESS_LIST


_SDK_MODULES = (
    "agents",
    "ai",
    "api",
    "artifacts",
    "config",
    "events",
    "executions",
    "files",
    "forms",
    "integrations",
    "knowledge",
    "organizations",
    "roles",
    "tables",
    "users",
    "webhooks",
    "workflows",
)
_TRANSPORTS = {"engine_request", "engine_request_sync", "request"}
_METHODS = {
    "get": "GET",
    "post": "POST",
    "put": "PUT",
    "patch": "PATCH",
    "delete": "DELETE",
}


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    return ""


def _paths(node: ast.AST, variables: dict[str, set[str]]) -> set[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value.split("?", 1)[0]}
    if isinstance(node, ast.Name):
        return variables.get(node.id, set())
    if isinstance(node, ast.IfExp):
        return _paths(node.body, variables) | _paths(node.orelse, variables)
    if isinstance(node, ast.JoinedStr):
        parts = []
        for part in node.values:
            if isinstance(part, ast.Constant):
                parts.append(str(part.value))
            elif isinstance(part, ast.FormattedValue):
                # Query suffixes change scope, never the route identity.
                if isinstance(part.value, ast.Call) and _dotted(
                    part.value.func
                ).endswith("_query"):
                    continue
                parts.append("{}")
        return {"".join(parts).split("?", 1)[0]}
    return set()


def _function_nodes(
    tree: ast.Module,
) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    functions = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions[node.name] = node
        elif isinstance(node, ast.ClassDef):
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    functions[f"{node.name}.{member.name}"] = member
    return functions


@lru_cache(maxsize=1)
def _sdk_bindings() -> tuple[dict[str, tuple[tuple[str, str], ...]], str]:
    bindings: dict[str, tuple[tuple[str, str], ...]] = {}
    digest = hashlib.sha256()
    sdk_root = Path(__file__).resolve().parents[1] / "bifrost"
    for module in _SDK_MODULES:
        raw = (sdk_root / f"{module}.py").read_bytes()
        digest.update(module.encode())
        digest.update(raw)
        functions = _function_nodes(ast.parse(raw))
        direct: dict[str, set[tuple[str, str]]] = {}
        edges: dict[str, set[str]] = {}
        for name, function in functions.items():
            variables: dict[str, set[str]] = {}
            for node in ast.walk(function):
                if isinstance(node, ast.Assign):
                    value = _paths(node.value, variables)
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            variables.setdefault(target.id, set()).update(value)
            routes: set[tuple[str, str]] = set()
            helpers: set[str] = set()
            for node in ast.walk(function):
                if not isinstance(node, ast.Call):
                    continue
                target = _dotted(node.func)
                tail = target.rsplit(".", 1)[-1]
                paths = set()
                method = None
                if tail in _TRANSPORTS and len(node.args) >= 2:
                    method_node = node.args[0]
                    if isinstance(method_node, ast.Constant) and isinstance(
                        method_node.value, str
                    ):
                        method = method_node.value.upper()
                    paths = _paths(node.args[1], variables)
                elif tail in _METHODS and node.args:
                    method = _METHODS[tail]
                    paths = _paths(node.args[0], variables)
                if method:
                    routes.update(
                        (method, path) for path in paths if path.startswith("/api/")
                    )
                if target.startswith(("self.", "cls.")) and "." in name:
                    target = name.split(".", 1)[0] + "." + tail
                if target in functions:
                    helpers.add(target)
            direct[name] = routes
            edges[name] = helpers
        for name in functions:
            if "." not in name or name.rsplit(".", 1)[-1].startswith("_"):
                continue
            pending = [name]
            seen: set[str] = set()
            routes: set[tuple[str, str]] = set()
            while pending:
                current = pending.pop()
                if current in seen:
                    continue
                seen.add(current)
                routes.update(direct[current])
                pending.extend(edges[current] - seen)
            key = f"bifrost.{module}.{name.rsplit('.', 1)[-1]}"
            bindings[key] = tuple(sorted(routes))
    for entry in ACCESS_LIST:
        digest.update(
            f"{entry.key}:{entry.access_class}:{entry.permission}:{entry.boundary}".encode()
        )
    return bindings, digest.hexdigest()


def sdk_permission_fingerprint() -> str:
    """Version proposals when SDK transports or authorization catalog change."""
    return _sdk_bindings()[1]


def permissions_for_sdk_calls(calls: list[str]) -> tuple[list[str], list[str]]:
    """Return narrow named candidates and explicit context-dependent decisions."""
    bindings, _ = _sdk_bindings()
    routes = {
        (entry.method, re.sub(r"\{[^}]+\}", "{}", entry.path)): entry
        for entry in ACCESS_LIST
        if entry.method and entry.path
    }
    permissions: set[str] = set()
    unknowns: set[str] = set()
    for call in sorted(set(calls)):
        bound = bindings.get(call)
        if not bound:
            unknowns.add(f"No static platform transport mapping for SDK call {call}")
            continue
        for method, path in bound:
            entry = routes.get((method, path))
            if entry is None:
                unknowns.add(f"No canonical access entry for {call}: {method} {path}")
            elif entry.access_class == AccessClass.TABLE_POLICY:
                unknowns.add(
                    f"Resource policy must be reviewed for {call}: {method} {entry.path}"
                )
            elif entry.access_class == AccessClass.PERMISSION and entry.permission:
                permissions.add(entry.permission)
                if entry.permission == "secrets.read":
                    unknowns.add(
                        f"Review whether {call} reads a secret and which organization supplies it"
                    )
            elif entry.permission:
                # A personal route's .all power widens ownership; invoking the
                # SDK method does not by itself establish that widening is needed.
                unknowns.add(
                    f"Review ownership-dependent authority for {call}: {method} {entry.path}"
                )
    return sorted(permissions), sorted(unknowns)
