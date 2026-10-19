"""Conservative static analysis for workflow least-privilege conversion.

The converter must inspect workspace source without importing it: workflow code
has import-time effects and may not be trusted.  ``summarize_source`` therefore
turns one file into a bounded JSON call graph.  ``analyze_workflow`` follows that
graph through a caller-supplied repository resolver, retaining only the source
hashes that influenced its result.
"""

from __future__ import annotations

import ast
import builtins
import json
import sys
from dataclasses import dataclass
from functools import lru_cache
from importlib.metadata import packages_distributions
from typing import Any, Awaitable, Callable

from shared.workflow_conversion_sdk import sdk_call_contract, sdk_object_contract, sdk_object_is_data

ANALYZER_VERSION = "26"
MAX_SOURCE_BYTES = 2_000_000
MAX_AST_NODES = 256_000
MAX_EVENTS_PER_SCOPE = 2_048
MAX_DEPENDENCIES = 256
MAX_GRAPH_STEPS = 10_000
MAX_SDK_CALLS = 2_048
MAX_EVIDENCE = 4_096
MAX_SELECTOR_VALUE_CHARS = 2_000
_MODULE_INIT = "<import>"
_THIRD_PARTY_ROOTS = {
    "aiohttp",
    "babel",
    "bs4",
    "celery",
    "dateutil",
    "fastapi",
    "httpx",
    "numpy",
    "openai",
    "pydantic",
    "requests",
    "sqlalchemy",
    "starlette",
    "yaml",
}
_DATA_METHODS = {
    "bytes": frozenset({"decode", "hex", "split", "strip"}),
    "dict": frozenset(
        {"clear", "copy", "get", "items", "keys", "pop", "popitem", "setdefault", "update", "values"}
    ),
    "list": frozenset(
        {"append", "clear", "copy", "count", "extend", "index", "insert", "pop", "remove", "reverse", "sort"}
    ),
    "set": frozenset(
        {"add", "clear", "copy", "difference", "discard", "intersection", "pop", "remove", "union", "update"}
    ),
    "str": frozenset(
        {"capitalize", "casefold", "endswith", "find", "format", "index", "join", "lower", "lstrip", "replace", "rsplit", "rstrip", "split", "startswith", "strip", "title", "upper"}
    ),
    "tuple": frozenset({"count", "index"}),
}
_RESOURCE_SELECTOR_PARAMETERS = frozenset(
    {
        "name",
        "table",
        "table_id",
        "path",
        "location",
        "scope",
        "solution",
        "solution_id",
        "organization_id",
        "org_id",
        "workflow_id",
    }
)


@lru_cache(maxsize=1)
def _installed_package_roots() -> frozenset[str]:
    """Read installed-package metadata without importing workflow dependencies."""
    return frozenset(packages_distributions())


def _is_installed_third_party(module: str) -> bool:
    root = module.split(".")[0]
    return root in _THIRD_PARTY_ROOTS or root in _installed_package_roots()


def _is_sdk_module(module: str) -> bool:
    return module == "bifrost" or module.startswith("bifrost.")


_SAFE_BIFROST_DECORATORS = {
    "bifrost.service", "bifrost.workflow", "bifrost.tool", "bifrost.data_provider",
}


@dataclass(frozen=True)
class SourceFile:
    """A repository source file supplied by the conversion service."""

    key: str
    path: str
    source: str
    content_hash: str
    kind: str = "module"


def _dotted(
    node: ast.AST, bindings: dict[str, dict[str, Any]], handles: dict[str, str]
) -> str | None:
    if isinstance(node, ast.Name):
        if node.id in handles:
            return handles[node.id]
        binding = bindings.get(node.id)
        if binding and binding.get("module") and _is_sdk_module(binding["module"]):
            symbol = binding.get("symbol")
            return f"{binding['module']}.{symbol}" if symbol else binding["module"]
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted(node.value, bindings, handles)
        return f"{parent}.{node.attr}" if parent else None
    if isinstance(node, ast.Call):
        return _dotted(node.func, bindings, handles)
    if isinstance(node, ast.Await):
        return _dotted(node.value, bindings, handles)
    return None


def _sdk_call(target: str | None) -> str | None:
    if not target or not target.startswith("bifrost."):
        return None
    parts = target.split(".")
    if len(parts) == 2:
        return target if sdk_call_contract(target) is not None else None
    if len(parts) < 3:
        return None
    module = parts[1]
    method = parts[-1]
    if method == module and len(parts) == 3:
        return None
    return f"bifrost.{module}.{method}"


def _sdk_callable(target: str | None) -> str | None:
    """Return a declared SDK callable, excluding namespaces and DTO fields."""
    call = _sdk_call(target)
    return call if call is not None and sdk_call_contract(call) is not None else None


def _static_getattr_target(
    node: ast.AST, bindings: dict[str, dict[str, Any]], handles: dict[str, str]
) -> str | None:
    if not (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and isinstance(node.args[1].value, str)
    ):
        return None
    subject = _dotted(node.args[0], bindings, handles)
    return f"{subject}.{node.args[1].value}" if subject else None


def _is_context_data_field(target: str | None) -> bool:
    """Check a static field against the shipped public context contract."""
    if not target or not target.startswith("bifrost.context."):
        return False
    contract = sdk_object_contract("bifrost.context")
    if contract is None:
        return False
    field = target.rsplit(".", 1)[-1]
    return any(candidate.name == field for candidate in contract.fields)


def _is_known_sdk_call(target: str | None) -> bool:
    call = _sdk_call(target)
    return call is not None and sdk_call_contract(call) is not None


def _sdk_object_field_contract(target: str | None) -> Any | None:
    if not target or "." not in target:
        return None
    sdk_type, _, field_name = target.rpartition(".")
    contract = sdk_object_contract(sdk_type)
    if contract is None:
        parent = _sdk_object_field_contract(sdk_type)
        contract = sdk_object_contract(parent.sdk_type) if parent and parent.sdk_type else None
    if contract is None:
        return None
    return next((field for field in contract.fields if field.name == field_name), None)


def _sdk_field_method_call(target: str | None) -> str | None:
    """Resolve a method reached through one declared SDK object field."""
    if not target or "." not in target:
        return None
    field_target, _, method = target.rpartition(".")
    field = _sdk_object_field_contract(field_target)
    if field is None or not field.sdk_type:
        return None
    return f"{field.sdk_type}.{method}"


def _sdk_data_field_method(target: str | None) -> bool:
    """Recognize ordinary operations descending from a declared SDK data field."""
    if not target or "." not in target:
        return False
    field_target = target
    while "." in field_target:
        field_target, _, _ = field_target.rpartition(".")
        field = _sdk_object_field_contract(field_target)
        if field is None:
            continue
        return field.kind in {"json_object", "json_list", "scalar"} or (
            field.kind == "unknown" and field.sdk_type is None
        )
    return False


def _sdk_result_method(
    node: ast.Attribute,
    bindings: dict[str, dict[str, Any]],
    handles: dict[str, str],
) -> tuple[str | None, bool]:
    """Resolve a method on a direct SDK call result without flattening it."""
    value = node.value.value if isinstance(node.value, ast.Await) else node.value
    if not isinstance(value, ast.Call):
        return None, False
    call = _sdk_call(_dotted(value.func, bindings, handles))
    contract = sdk_call_contract(call) if call else None
    if contract is None:
        return None, False
    if contract.return_kind in {"json_object", "json_list", "scalar"}:
        return None, True
    if contract.return_kind in {"sdk_object", "sdk_credential"} and contract.sdk_type:
        return f"{contract.sdk_type}.{node.attr}", False
    return None, False


def _binding_for_expression(
    node: ast.AST, bindings: dict[str, dict[str, Any]], handles: dict[str, str]
) -> dict[str, Any] | None:
    if isinstance(node, ast.Call):
        return _binding_for_expression(node.func, bindings, handles)
    if isinstance(node, ast.Name):
        binding = bindings.get(node.id)
        return binding if binding and "module" in binding else None
    attributes: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        attributes.append(current.attr)
        current = current.value
    if isinstance(current, ast.Call):
        binding = _binding_for_expression(current.func, bindings, handles)
        if binding:
            attributes.reverse()
            if attributes:
                member = ".".join(
                    part
                    for part in (binding.get("member"), ".".join(attributes))
                    if part
                )
                return {**binding, "member": member}
    if isinstance(current, ast.Name):
        binding = bindings.get(current.id)
        if binding and "module" in binding and binding.get("symbol"):
            attributes.reverse()
            if attributes:
                member = ".".join(
                    part
                    for part in (binding.get("member"), ".".join(attributes))
                    if part
                )
                return {**binding, "member": member}
        if binding and "module" in binding and not binding.get("symbol"):
            attributes.reverse()
            module_tail = binding["module"].split(".")[1:]
            if attributes[: len(module_tail)] == module_tail:
                attributes = attributes[len(module_tail) :]
            if attributes:
                return {**binding, "symbol": ".".join(attributes)}
    return None


def _higher_order_callback(
    node: ast.Call, target: str | None
) -> tuple[str, ast.AST] | None:
    if target in {"map", "filter"} and node.args:
        callback = node.args[0]
    elif target in {"sorted", "min", "max"}:
        callback = next(
            (keyword.value for keyword in node.keywords if keyword.arg == "key"), None
        )
    elif isinstance(node.func, ast.Attribute) and node.func.attr == "sort":
        callback = next(
            (keyword.value for keyword in node.keywords if keyword.arg == "key"), None
        )
        target = "sort"
    else:
        return None
    if (
        callback is None
        or isinstance(callback, ast.Constant)
        and callback.value is None
    ):
        return None
    return target, callback


def _branch_binding_updates(statements: list[ast.stmt]) -> dict[str, tuple[Any, ...]]:
    """Return direct binding changes that make an ``if`` branch ambiguous."""
    updates: dict[str, tuple[Any, ...]] = {}
    for statement in statements:
        if isinstance(statement, ast.Import):
            for alias in statement.names:
                updates[alias.asname or alias.name.split(".")[0]] = (
                    "import",
                    alias.name,
                    0,
                    None,
                )
        elif isinstance(statement, ast.ImportFrom):
            for alias in statement.names:
                if alias.name != "*":
                    updates[alias.asname or alias.name] = (
                        "import",
                        statement.module or "",
                        statement.level,
                        alias.name,
                    )
        elif isinstance(statement, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = (
                statement.targets
                if isinstance(statement, ast.Assign)
                else [statement.target]
            )
            value = getattr(statement, "value", None)
            signature = _assignment_signature(value)
            for target in targets:
                if isinstance(target, ast.Name):
                    updates[target.id] = signature
    return updates


def _binding_signature(visitor: "_ScopeSummary", name: str) -> tuple[Any, ...] | None:
    if name in visitor.bindings:
        binding = visitor.bindings[name]
        if not binding.get("module"):
            return None
        return (
            "import",
            binding["module"],
            binding["level"],
            binding.get("symbol"),
        )
    if name in visitor.handles:
        return ("handle", visitor.handles[name])
    if name in visitor.local_aliases:
        return ("local", visitor.local_aliases[name])
    return None


def _unique_bindings(candidates: Any) -> tuple[dict[str, Any], ...]:
    """Deduplicate compact bindings, including nested constructor context."""
    unique: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        unique[json.dumps(candidate, sort_keys=True)] = candidate
    return tuple(unique.values())


def _continued_member(binding: dict[str, Any], continuation: str) -> str:
    """Append a bounded member continuation to one imported binding."""
    return ".".join(
        part for part in (binding.get("member"), continuation) if part
    )


def _literal_kind(node: ast.AST, values: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return values.get(node.id)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bytes):
            return "bytes"
        if isinstance(node.value, str):
            return "str"
        return None
    if isinstance(node, (ast.Dict, ast.DictComp)):
        return "dict"
    if isinstance(node, (ast.List, ast.ListComp)):
        return "list"
    if isinstance(node, (ast.Set, ast.SetComp)):
        return "set"
    if isinstance(node, ast.Tuple):
        return "tuple"
    if isinstance(node, ast.IfExp):
        body = _literal_kind(node.body, values)
        return body if body == _literal_kind(node.orelse, values) else None
    return None


def _annotation_data_kind(annotation: ast.AST | None) -> str | None:
    """Return one built-in data kind from a simple local type annotation."""
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        members = [annotation.left, annotation.right]
        present = [
            member for member in members
            if not (isinstance(member, ast.Constant) and member.value is None)
        ]
        if len(present) == 1:
            return _annotation_data_kind(present[0])
    if isinstance(annotation, ast.Name) and annotation.id in _DATA_METHODS:
        return annotation.id
    if isinstance(annotation, ast.Subscript) and isinstance(annotation.value, ast.Name):
        if annotation.value.id == "Optional":
            return _annotation_data_kind(annotation.slice)
        return annotation.value.id if annotation.value.id in _DATA_METHODS else None
    return None


def _is_callable_annotation(annotation: ast.AST | None) -> bool:
    if isinstance(annotation, ast.Name):
        return annotation.id == "Callable"
    if isinstance(annotation, ast.Attribute):
        return annotation.attr == "Callable"
    if isinstance(annotation, ast.Subscript):
        return _is_callable_annotation(annotation.value)
    return False


def _annotation_local_class(
    annotation: ast.AST | None, class_names: set[str]
) -> str | None:
    """Return one local class from a nullable field annotation."""
    if isinstance(annotation, ast.Name) and annotation.id in class_names:
        return annotation.id
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        candidates = {
            candidate
            for candidate in (
                _annotation_local_class(annotation.left, class_names),
                _annotation_local_class(annotation.right, class_names),
            )
            if candidate is not None
        }
        return next(iter(candidates)) if len(candidates) == 1 else None
    if isinstance(annotation, ast.Subscript) and isinstance(annotation.value, ast.Name):
        if annotation.value.id == "Optional":
            return _annotation_local_class(annotation.slice, class_names)
    return None


def _annotation_imported_class(
    annotation: ast.AST | None, bindings: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    """Return one imported class binding from a nullable field annotation."""
    if isinstance(annotation, ast.Name):
        binding = bindings.get(annotation.id)
        return dict(binding) if binding and binding.get("symbol") else None
    if isinstance(annotation, ast.Attribute):
        binding = _binding_for_expression(annotation, bindings, {})
        return dict(binding) if binding and binding.get("symbol") else None
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        candidates = _unique_bindings(
            candidate
            for candidate in (
                _annotation_imported_class(annotation.left, bindings),
                _annotation_imported_class(annotation.right, bindings),
            )
            if candidate is not None
        )
        return candidates[0] if len(candidates) == 1 else None
    if isinstance(annotation, ast.Subscript):
        value = annotation.value
        if isinstance(value, ast.Name) and value.id == "Optional":
            return _annotation_imported_class(annotation.slice, bindings)
    return None


def _self_member_path(node: ast.AST) -> tuple[str, ...] | None:
    """Return the bounded attribute path below ``self`` for one call target."""
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name) and current.id == "self" and parts:
        return tuple(reversed(parts))
    return None


def _self_field_assignments(
    target: ast.expr, value: ast.expr
) -> list[tuple[str, ast.expr]]:
    """Pair direct ``self`` fields with values, including parallel unpacking."""
    if (
        isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Name)
        and target.value.id == "self"
    ):
        return [(target.attr, value)]
    if (
        isinstance(target, (ast.Tuple, ast.List))
        and isinstance(value, (ast.Tuple, ast.List))
        and len(target.elts) == len(value.elts)
    ):
        return [
            assignment
            for nested_target, nested_value in zip(target.elts, value.elts)
            for assignment in _self_field_assignments(nested_target, nested_value)
        ]
    return []


def _nested_assignments(statements: list[ast.stmt]) -> list[ast.Assign | ast.AnnAssign]:
    """Collect assignments from statement blocks without entering nested definitions."""
    assignments: list[ast.Assign | ast.AnnAssign] = []
    pending = list(statements)
    while pending:
        statement = pending.pop()
        if isinstance(
            statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            continue
        if isinstance(statement, (ast.Assign, ast.AnnAssign)):
            assignments.append(statement)
        for child in ast.iter_child_nodes(statement):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(child, ast.stmt):
                pending.append(child)
    return assignments


def _local_class_return_chain(
    node: ast.AST, class_names: set[str]
) -> tuple[str, str, str] | None:
    """Return ``Class.method().field`` for a static local return continuation."""
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not (
        isinstance(current, ast.Call)
        and isinstance(current.func, ast.Attribute)
        and isinstance(current.func.value, ast.Name)
        and current.func.value.id in class_names
        and parts
    ):
        return None
    return (
        current.func.value.id,
        current.func.attr,
        ".".join(reversed(parts)),
    )


def _callable_target(
    node: ast.AST,
    bindings: dict[str, dict[str, Any]],
    handles: dict[str, str],
    local_functions: set[str],
) -> dict[str, Any] | None:
    """Keep one statically bound callback without retaining user values."""
    target = _dotted(node, bindings, handles)
    call = _sdk_callable(target)
    if call:
        return {"kind": "sdk", "call": call}
    if isinstance(node, ast.Name) and node.id in local_functions:
        return {"kind": "local", "name": node.id}
    binding = _binding_for_expression(node, bindings, handles)
    if binding and "module" in binding:
        if _is_sdk_module(binding["module"]):
            symbol = binding.get("symbol")
            return {
                "kind": "sdk_namespace",
                "target": (
                    f"{binding['module']}.{symbol}" if symbol else binding["module"]
                ),
            }
        return {"kind": "imported", **binding}
    return None


def _return_callable_fields(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    bindings: dict[str, dict[str, Any]],
    class_names: set[str],
) -> dict[str, str]:
    """Keep declared SDK callables passed by one direct local-class return."""
    collector = _DirectReturns()
    for statement in node.body:
        collector.visit(statement)
    contexts: list[dict[str, str]] = []
    for value in collector.values:
        value = value.value if isinstance(value, ast.Await) else value
        if not (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id in class_names
        ):
            return {}
        context: dict[str, str] = {}
        for keyword in value.keywords:
            if keyword.arg is None:
                continue
            call = _sdk_callable(_dotted(keyword.value, bindings, {}))
            if call:
                context[keyword.arg] = f"sdk:{call}"
        contexts.append(context)
    return contexts[0] if len(contexts) == 1 else {}


def _return_imported_instance(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    bindings: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Record one direct imported-class return without retaining user values."""
    collector = _DirectReturns()
    for statement in node.body:
        collector.visit(statement)
    instances: list[dict[str, Any]] = []
    for value in collector.values:
        value = value.value if isinstance(value, ast.Await) else value
        if not (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and (binding := bindings.get(value.func.id))
            and "module" in binding
            and binding.get("symbol")
        ):
            return None
        context = {
            keyword.arg: f"sdk:{call}"
            for keyword in value.keywords
            if keyword.arg is not None
            and (call := _sdk_callable(_dotted(keyword.value, bindings, {})))
        }
        instances.append({
            "module": binding["module"],
            "level": binding["level"],
            "symbol": binding["symbol"],
            "constructor_context": context,
        })
    if len(instances) != 1:
        return None
    return instances[0]


def _exported_data_values(tree: ast.Module) -> dict[str, str]:
    """Record bounded, literal module exports that cannot be classes."""
    values: dict[str, str] = {}
    for statement in tree.body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            values.pop(statement.name, None)
            continue
        if isinstance(statement, (ast.If, ast.Try, ast.TryStar)):
            branches = (
                [statement.body, statement.orelse, statement.finalbody]
                if isinstance(statement, (ast.Try, ast.TryStar))
                else [statement.body, statement.orelse]
            )
            for branch in branches:
                for name in _branch_binding_updates(branch):
                    values.pop(name, None)
            continue
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        value = statement.value
        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            kind = _literal_kind(value, {}) if value is not None else None
            if kind is None:
                values.pop(target.id, None)
            elif len(values) < MAX_EVENTS_PER_SCOPE:
                values[target.id] = kind
    return values


def _is_call_result(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) or (
        isinstance(node, ast.Await) and isinstance(node.value, ast.Call)
    )


def _assignment_signature(value: ast.AST | None) -> tuple[Any, ...]:
    if value is None:
        return ("assignment", None)
    if isinstance(value, (ast.Name, ast.Attribute, ast.Lambda)):
        return ("callable_assignment", ast.dump(value, include_attributes=False))
    if _is_call_result(value):
        return ("call_result_assignment", ast.dump(value, include_attributes=False))
    return ("data_assignment", ast.dump(value, include_attributes=False))


def _branch_value_kind(
    statements: list[ast.stmt], name: str, values: dict[str, str]
) -> str | None:
    for statement in reversed(statements):
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        targets = (
            statement.targets if isinstance(statement, ast.Assign) else [statement.target]
        )
        if any(isinstance(target, ast.Name) and target.id == name for target in targets):
            return _literal_kind(statement.value, values) if statement.value else None
    return None


def _is_safe_data_method(node: ast.AST, values: dict[str, str]) -> bool:
    if not isinstance(node, ast.Attribute):
        return False
    kind = _literal_kind(node.value, values)
    if kind is not None:
        if kind == "json":
            return True
        return node.attr in _DATA_METHODS.get(kind, ())
    return False


def _api_path_template(
    node: ast.AST,
    constants: dict[str, str],
    bindings: dict[str, dict[str, Any]] | None = None,
    handles: dict[str, str] | None = None,
) -> str | None:
    """Return a bounded generic-SDK route template without retaining inputs."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.partition("?")[0]
    if isinstance(node, ast.Name):
        value = constants.get(node.id)
        return value.partition("?")[0] if value is not None else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _api_path_template(node.left, constants, bindings, handles)
        if left is None:
            return None
        left_has_query = (
            isinstance(node.left, ast.Constant)
            and isinstance(node.left.value, str)
            and "?" in node.left.value
        )
        target = (
            _dotted(node.right.func, bindings or {}, handles or {})
            if isinstance(node.right, ast.Call)
            else None
        )
        if left_has_query or target == "bifrost.files._solution_query":
            return left
        return None
    if not isinstance(node, ast.JoinedStr):
        return None
    parts: list[str] = []
    for value in node.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            parts.append(value.value)
        elif isinstance(value, ast.FormattedValue):
            query_target = (
                _dotted(value.value.func, bindings or {}, handles or {})
                if isinstance(value.value, ast.Call)
                else None
            )
            if query_target == "bifrost.files._solution_query":
                # The shipped helper appends only an optional query string.
                # The route before it is the bounded permission selector.
                parts.append("")
            elif isinstance(value.value, ast.Name):
                constant = constants.get(value.value.id)
                parts.append(constant if constant is not None else "{}")
            else:
                parts.append("{}")
        else:
            return None
    return "".join(parts).partition("?")[0]


def _bounded_api_route_template(
    node: ast.AST,
    constants: dict[str, str],
    bindings: dict[str, dict[str, Any]] | None = None,
    handles: dict[str, str] | None = None,
) -> str | None:
    """Return one retained generic API route template, never request data."""
    path = _api_path_template(node, constants, bindings, handles)
    if (
        path is None
        or not path.startswith("/api/")
        or len(path) > MAX_SELECTOR_VALUE_CHARS
    ):
        return None
    return path


def _generic_http_selector_shape(
    call: str,
) -> tuple[str, str | None, str | None] | None:
    """Describe only public generic SDK transport selectors."""
    shortcut_methods = frozenset(
        {"get", "post", "put", "patch", "delete", "get_sync", "post_sync"}
    )
    method = call.rpartition(".")[2]
    if call.startswith("bifrost.api.") and method in shortcut_methods:
        return "path", None, None
    if not call.startswith("bifrost.client."):
        return None
    if method in shortcut_methods:
        return "path", None, None
    if method in {"engine_request", "engine_request_sync", "engine_stream", "request", "stream"}:
        return "path", None, "method"
    return None


class _ScopeSummary(ast.NodeVisitor):
    """Summarize executable statements in a module or one function body."""

    def __init__(
        self,
        bindings: dict[str, dict[str, Any]],
        local_functions: set[str],
        local_classes: set[str] | None = None,
        value_kinds: dict[str, str] | None = None,
        parameter_names: set[str] | None = None,
    ) -> None:
        self.bindings = {
            name: dict(binding)
            for name, binding in bindings.items()
            if "conditional_targets" not in binding
        }
        self.local_functions = local_functions
        self.local_classes = set(local_classes or ())
        self.handles: dict[str, str] = {}
        self.local_aliases: dict[str, str] = {}
        self.local_return_values: dict[str, dict[str, Any]] = {}
        self.local_instance_return_values: dict[str, dict[str, Any]] = {}
        self.conditional_local_returns: dict[str, tuple[str, ...]] = {}
        self.conditional_callables = {
            name: tuple(binding["conditional_targets"])
            for name, binding in bindings.items()
            if "conditional_targets" in binding
        }
        self.value_kinds = dict(value_kinds or {})
        self.parameter_names = set(parameter_names or ())
        self.selector_constants: dict[str, str] = {}
        self.data_assignments: set[str] = set()
        self.imports: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.truncated = False

    def _argument_kind(self, node: ast.AST) -> str:
        if route := _bounded_api_route_template(
            node, self.selector_constants, self.bindings, self.handles
        ):
            return f"api_path:{route}"
        if _literal_kind(node, self.value_kinds) is not None:
            return "json"
        if isinstance(node, ast.Name):
            if node.id in self.value_kinds:
                return "json"
            if node.id in self.parameter_names:
                return f"parameter:{node.id}"
            if node.id in self.handles:
                return f"sdk:{self.handles[node.id]}"
        target = _dotted(node, self.bindings, self.handles)
        if _is_call_result(node):
            call = _sdk_call(target)
            contract = sdk_call_contract(call) if call else None
            if contract is not None:
                if contract.sdk_type:
                    return f"sdk:{contract.sdk_type}"
                if contract.return_kind in {"json_object", "json_list", "scalar", "none"}:
                    return "json"
            return "unknown"
        return f"sdk:{target}" if target and target.startswith("bifrost.") else "unknown"

    def _callable_context(self, node: ast.AST) -> dict[str, Any] | None:
        return _callable_target(
            node, self.bindings, self.handles, self.local_functions | set(self.local_aliases)
        )

    def _constructor_context(self, node: ast.Call) -> dict[str, Any]:
        """Keep only bounded constructor values needed for later member traversal."""
        context = {
            keyword.arg: callback
            for keyword in node.keywords
            if keyword.arg is not None
            and (callback := self._callable_context(keyword.value)) is not None
        }
        if node.args:
            context["__positional__"] = [
                self._argument_kind(argument) for argument in node.args
            ]
        return context

    def _emit(self, event: dict[str, Any]) -> None:
        if len(self.events) < MAX_EVENTS_PER_SCOPE:
            self.events.append(event)
        else:
            self.truncated = True

    def _branch_local_return_names(
        self, statements: list[ast.stmt], name: str
    ) -> tuple[str, ...]:
        names: list[str] = []
        for statement in statements:
            if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
                continue
            targets = (
                statement.targets if isinstance(statement, ast.Assign) else [statement.target]
            )
            if not any(isinstance(target, ast.Name) and target.id == name for target in targets):
                continue
            value = statement.value
            if isinstance(value, ast.Await):
                value = value.value
            if (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id in (self.local_functions | set(self.local_aliases))
            ):
                resolved = self.local_aliases.get(value.func.id, value.func.id)
                if resolved not in names:
                    names.append(resolved)
        return tuple(names)

    def _selector_value(self, node: ast.AST) -> tuple[str | None, str | None]:
        value: str | None = None
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
        elif isinstance(node, ast.Name):
            value = self.selector_constants.get(node.id)
        if value is None:
            return None, "nonliteral selector"
        if len(value) > MAX_SELECTOR_VALUE_CHARS:
            return None, f"selector exceeds maximum length ({MAX_SELECTOR_VALUE_CHARS})"
        return value, None

    def _bind_sdk_result(self, name: str, call: str) -> None:
        contract = sdk_call_contract(call)
        if contract is None:
            return
        if contract.return_kind in {"sdk_object", "sdk_credential"} and contract.sdk_type:
            self.handles[name] = contract.sdk_type
        elif contract.return_kind in {"json_object", "scalar"}:
            self.value_kinds[name] = "json"
        elif (
            contract.return_kind == "json_list"
            and contract.element_kind not in {"sdk_object", "sdk_credential", "unknown", None}
        ):
            self.value_kinds[name] = "json"

    def _bind_sdk_field(self, name: str, target: str | None) -> bool:
        field = _sdk_object_field_contract(target)
        if field is None:
            return False
        if field.kind in {"sdk_object", "sdk_credential"} and field.sdk_type:
            self.handles[name] = field.sdk_type
        elif field.kind in {"json_object", "scalar"}:
            self.value_kinds[name] = "json"
        elif (
            field.kind == "json_list"
            and field.element_kind not in {"sdk_object", "sdk_credential", "unknown", None}
        ):
            self.value_kinds[name] = "json"
        return True

    def _generic_file_body_selectors(self, node: ast.Call) -> dict[str, Any]:
        """Keep only file route selectors from a literal generic-client body."""
        result: dict[str, Any] = {
            "selectors": {},
            "dynamic_selectors": [],
            "selector_reasons": {},
        }
        json_argument = next(
            (keyword.value for keyword in node.keywords if keyword.arg == "json"),
            None,
        )
        if not isinstance(json_argument, ast.Dict):
            return result
        for key, value in zip(json_argument.keys, json_argument.values):
            if not isinstance(key, ast.Constant) or key.value not in {"location", "method"}:
                continue
            selector, reason = self._selector_value(value)
            if key.value == "method" and selector not in {"GET", "PUT"}:
                selector = None
                reason = "unsupported signing method"
            if selector is None:
                result["dynamic_selectors"].append(key.value)
                result["selector_reasons"][key.value] = reason or "unknown selector"
            else:
                result["selectors"][key.value] = selector
        return result

    def _sdk_event(self, node: ast.Call, call: str) -> dict[str, Any]:
        event: dict[str, Any] = {"kind": "sdk", "line": node.lineno, "call": call}
        contract = sdk_call_contract(call)
        if contract is None:
            return event
        arguments = {
            parameter: value
            for parameter, value in zip(contract.parameters, node.args)
        }
        arguments.update(
            {
                keyword.arg: keyword.value
                for keyword in node.keywords
                if keyword.arg in contract.parameters
            }
        )
        selectors: dict[str, str] = {}
        dynamic_selectors: list[str] = []
        selector_reasons: dict[str, str] = {}
        defaults = dict(contract.resource_defaults)
        selector_parameters = {
            "scope", "solution", "solution_id", "organization_id", "org_id"
        }
        if call.startswith(("bifrost.tables.", "bifrost.files.")):
            selector_parameters |= _RESOURCE_SELECTOR_PARAMETERS
        if call == "bifrost.files.get_signed_url":
            selector_parameters = selector_parameters | {"method"}
        has_selector_parameter = any(
            parameter in selector_parameters for parameter in contract.parameters
        )
        for parameter in contract.parameters:
            if parameter not in selector_parameters:
                continue
            if parameter not in arguments:
                default = defaults.get(parameter)
                if isinstance(default, str) and (
                    parameter != "method" or default in {"GET", "PUT"}
                ):
                    if len(default) > MAX_SELECTOR_VALUE_CHARS:
                        dynamic_selectors.append(parameter)
                        selector_reasons[parameter] = (
                            f"selector exceeds maximum length ({MAX_SELECTOR_VALUE_CHARS})"
                        )
                    else:
                        selectors[parameter] = default
                elif parameter not in defaults:
                    dynamic_selectors.append(parameter)
                    selector_reasons[parameter] = "missing required selector"
                continue
            value = arguments[parameter]
            if (
                parameter in {"scope", "solution", "solution_id"}
                and isinstance(value, ast.Constant)
                and value.value is None
            ):
                continue
            selector, reason = self._selector_value(value)
            if parameter == "method" and selector not in {"GET", "PUT"}:
                selector = None
                reason = "unsupported signing method"
            if selector is None:
                dynamic_selectors.append(parameter)
                selector_reasons[parameter] = reason or "unknown selector"
            else:
                selectors[parameter] = selector
        if has_selector_parameter:
            event["resource_call"] = {
                "selectors": selectors,
                "dynamic_selectors": dynamic_selectors,
                "selector_reasons": selector_reasons,
            }
        generic_shape = _generic_http_selector_shape(call)
        if generic_shape is not None:
            path_parameter, _shortcut_method, method_parameter = generic_shape
            path_node = arguments.get(path_parameter)
            path = (
                _api_path_template(
                    path_node, self.selector_constants, self.bindings, self.handles
                )
                if path_node is not None
                else None
            )
            transport_selectors: dict[str, str] = {}
            transport_dynamic_selectors: list[str] = []
            transport_selector_reasons: dict[str, str] = {}
            if method_parameter is not None:
                method_node = arguments.get(method_parameter)
                method, reason = (
                    self._selector_value(method_node)
                    if method_node is not None
                    else (None, "missing required selector")
                )
                if method is None:
                    transport_dynamic_selectors.append("http_method")
                    transport_selector_reasons["http_method"] = (
                        reason or "unknown selector"
                    )
                elif method.upper() not in {
                    "DELETE",
                    "GET",
                    "HEAD",
                    "OPTIONS",
                    "PATCH",
                    "POST",
                    "PUT",
                }:
                    transport_dynamic_selectors.append("http_method")
                    transport_selector_reasons["http_method"] = "unsupported HTTP method"
                else:
                    transport_selectors["http_method"] = method.upper()
            file_body_selectors = self._generic_file_body_selectors(node)
            if path is not None and len(path) <= MAX_SELECTOR_VALUE_CHARS:
                selectors = {"path": path, **transport_selectors}
                dynamic_selectors = list(transport_dynamic_selectors)
                selector_reasons = dict(transport_selector_reasons)
                file_routes = {
                    "/api/files/read",
                    "/api/files/write",
                    "/api/files/delete",
                    "/api/files/complete-upload",
                    "/api/files/exists",
                    "/api/files/stat",
                    "/api/files/list",
                    "/api/files/signed-url",
                }
                if path in file_routes:
                    allowed = {"location"}
                    if path == "/api/files/signed-url":
                        allowed.add("method")
                    for selector in allowed:
                        if selector in file_body_selectors["selectors"]:
                            selectors[selector] = file_body_selectors["selectors"][selector]
                        elif selector in file_body_selectors["dynamic_selectors"]:
                            dynamic_selectors.append(selector)
                            selector_reasons[selector] = file_body_selectors[
                                "selector_reasons"
                            ][selector]
                event["resource_call"] = {
                    "selectors": selectors,
                    "dynamic_selectors": dynamic_selectors,
                    "selector_reasons": selector_reasons,
                }
            else:
                reason = (
                    f"selector exceeds maximum length ({MAX_SELECTOR_VALUE_CHARS})"
                    if path is not None
                    else "nonliteral selector"
                )
                event["resource_call"] = {
                    "selectors": transport_selectors,
                    "dynamic_selectors": [*transport_dynamic_selectors, "path"],
                    "selector_reasons": {
                        **transport_selector_reasons,
                        "path": reason,
                    },
                }
            if (
                isinstance(path_node, ast.Name)
                and path_node.id in self.parameter_names
            ):
                event["parameter_selectors"] = {"path": path_node.id}
                event["file_body_selectors"] = file_body_selectors
        return event

    def _conditional_targets(
        self, statements: list[ast.stmt], name: str
    ) -> tuple[dict[str, Any], ...]:
        """Describe locally resolvable callable values assigned on one path."""
        targets: list[dict[str, Any]] = []
        for statement in statements:
            if isinstance(statement, ast.ImportFrom):
                for alias in statement.names:
                    if alias.name != "*" and (alias.asname or alias.name) == name:
                        module = statement.module or ""
                        target = f"{module}.{alias.name}" if module else alias.name
                        sdk_call = _sdk_callable(target)
                        targets.append(
                            {"kind": "sdk_callable", "call": sdk_call}
                            if sdk_call
                            else {
                                "kind": "imported",
                                "module": module,
                                "level": statement.level,
                                "symbol": alias.name,
                            }
                        )
            elif isinstance(statement, (ast.Assign, ast.AnnAssign)):
                assignments = (
                    statement.targets
                    if isinstance(statement, ast.Assign)
                    else [statement.target]
                )
                if not any(isinstance(target, ast.Name) and target.id == name for target in assignments):
                    continue
                value = statement.value
                if value is None:
                    continue
                target = _dotted(value, self.bindings, self.handles)
                sdk_call = _sdk_callable(target)
                if sdk_call and not _is_call_result(value):
                    targets.append({"kind": "sdk_callable", "call": sdk_call})
                elif target and target.startswith("bifrost.") and not _is_call_result(value):
                    targets.append({"kind": "sdk_namespace", "target": target})
                elif isinstance(value, ast.Name) and value.id in self.local_functions:
                    targets.append({"kind": "local", "name": value.id})
                else:
                    binding = _binding_for_expression(value, self.bindings, self.handles)
                    if binding:
                        if _is_sdk_module(binding["module"]):
                            symbol = binding.get("symbol")
                            target = (
                                f"{binding['module']}.{symbol}"
                                if symbol
                                else binding["module"]
                            )
                            sdk_call = _sdk_callable(target)
                            targets.append(
                                {"kind": "sdk_callable", "call": sdk_call}
                                if sdk_call
                                else {"kind": "sdk_namespace", "target": target}
                            )
                        else:
                            targets.append({"kind": "imported", **binding})
        return _unique_bindings(targets)

    def _current_callable_target(self, name: str) -> dict[str, Any] | None:
        if name in self.handles:
            sdk_call = _sdk_callable(self.handles[name])
            return (
                {"kind": "sdk_callable", "call": sdk_call}
                if sdk_call
                else {"kind": "sdk_namespace", "target": self.handles[name]}
            )
        if name in self.local_functions | set(self.local_aliases):
            return {"kind": "local", "name": self.local_aliases.get(name, name)}
        binding = self.bindings.get(name)
        if binding and binding.get("module") and _is_sdk_module(binding["module"]):
            symbol = binding.get("symbol")
            target = (
                f"{binding['module']}.{symbol}" if symbol else binding["module"]
            )
            sdk_call = _sdk_callable(target)
            return {
                "kind": "sdk_callable",
                "call": sdk_call,
            } if sdk_call else {
                "kind": "sdk_namespace",
                "target": target,
            }
        return {"kind": "imported", **binding} if binding and "module" in binding else None

    def _emit_callback(self, callback: ast.AST, line: int) -> None:
        if isinstance(callback, ast.Lambda):
            self.visit(callback.body)
            return
        target = _dotted(callback, self.bindings, self.handles)
        sdk = _sdk_call(target)
        if sdk and sdk_call_contract(sdk) is not None:
            self._emit({"kind": "sdk", "line": line, "call": sdk})
            return
        if isinstance(callback, ast.Name):
            if callback.id in self.local_functions | set(self.local_aliases):
                self._emit(
                    {
                        "kind": "local",
                        "line": line,
                        "name": self.local_aliases.get(callback.id, callback.id),
                        "arguments": [],
                    }
                )
                return
            for candidate in self.conditional_callables.get(callback.id, ()):
                if candidate["kind"] == "sdk_callable":
                    self._emit({"kind": "sdk", "line": line, "call": candidate["call"]})
                elif candidate["kind"] == "local":
                    self._emit(
                        {
                            "kind": "local",
                            "line": line,
                            "name": candidate["name"],
                            "arguments": [],
                        }
                    )
                elif candidate["kind"] == "imported":
                    self._emit(
                        {
                            "kind": "imported",
                            "line": line,
                            "module": candidate["module"],
                            "level": candidate["level"],
                            "symbol": candidate.get("symbol"),
                            "member": candidate.get("member"),
                            "optional": candidate.get("optional", False),
                            "arguments": [],
                        }
                    )
            if callback.id in self.conditional_callables:
                return
            binding = self.bindings.get(callback.id)
            if binding:
                self._emit({"kind": "imported", "line": line, **binding, "arguments": []})

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        for alias in node.names:
            local = alias.asname or alias.name.split(".")[0]
            binding = {"module": alias.name, "level": 0, "symbol": None}
            self.bindings[local] = binding
            self.imports.append(binding)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        module = node.module or ""
        for alias in node.names:
            if alias.name == "*":
                self._emit(
                    {"kind": "unknown", "line": node.lineno, "reason": "star import"}
                )
                continue
            binding = {
                "module": module,
                "level": node.level,
                "symbol": alias.name,
            }
            self.bindings[alias.asname or alias.name] = binding
            self.imports.append(binding)

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        self.visit(node.value)
        target = _dotted(node.value, self.bindings, self.handles)
        static_getattr = _static_getattr_target(node.value, self.bindings, self.handles)
        sdk = _sdk_call(target)
        sdk_field = _sdk_object_field_contract(target)
        binding = _binding_for_expression(node.value, self.bindings, self.handles)
        local_name = node.value.id if isinstance(node.value, ast.Name) else None
        local_return = (
            self.local_aliases.get(node.value.func.id, node.value.func.id)
            if isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id in (self.local_functions | set(self.local_aliases))
            else None
        )
        local_instance_return = (
            {
                "class": self.bindings[node.value.func.value.id]["local_instance"],
                "method": node.value.func.attr,
                "callable_context": self.bindings[node.value.func.value.id].get(
                    "callable_context", {}
                ),
            }
            if isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and isinstance(node.value.func.value, ast.Name)
            and self.bindings.get(node.value.func.value.id, {}).get("local_instance")
            else None
        )
        for assignment in node.targets:
            if not isinstance(assignment, ast.Name):
                continue
            self.handles.pop(assignment.id, None)
            self.local_aliases.pop(assignment.id, None)
            self.local_return_values.pop(assignment.id, None)
            self.local_instance_return_values.pop(assignment.id, None)
            self.conditional_local_returns.pop(assignment.id, None)
            self.conditional_callables.pop(assignment.id, None)
            self.bindings.pop(assignment.id, None)
            self.value_kinds.pop(assignment.id, None)
            self.selector_constants.pop(assignment.id, None)
            self.data_assignments.discard(assignment.id)
            if static_getattr and _is_known_sdk_call(static_getattr):
                self.handles[assignment.id] = static_getattr
            elif sdk_field is not None:
                self._bind_sdk_field(assignment.id, target)
            elif sdk and not _is_call_result(node.value):
                self.handles[assignment.id] = sdk
            elif sdk and _is_call_result(node.value):
                self._bind_sdk_result(assignment.id, sdk)
            elif binding:
                imported_binding = dict(binding)
                if isinstance(node.value, ast.Call):
                    imported_binding["constructor_context"] = self._constructor_context(
                        node.value
                    )
                self.bindings[assignment.id] = imported_binding
            elif local_name and local_name in (
                self.local_functions | set(self.local_aliases)
            ):
                self.local_aliases[assignment.id] = self.local_aliases.get(
                    local_name, local_name
                )
                self.bindings[assignment.id] = {
                    "local_function": self.local_aliases[assignment.id]
                }
            elif (
                isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id in self.local_classes
            ):
                self.bindings[assignment.id] = {
                    "local_instance": node.value.func.id,
                    "callable_context": {
                        keyword.arg: callback
                        for keyword in node.value.keywords
                        if keyword.arg is not None
                        and (callback := self._callable_context(keyword.value)) is not None
                    },
                }
            elif kind := _literal_kind(node.value, self.value_kinds):
                self.value_kinds[assignment.id] = kind
            if _assignment_signature(node.value)[0] == "data_assignment":
                self.data_assignments.add(assignment.id)
            if route := _bounded_api_route_template(
                node.value, self.selector_constants, self.bindings, self.handles
            ):
                self.selector_constants[assignment.id] = route
            elif isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                self.selector_constants[assignment.id] = node.value.value
            if local_return is not None:
                self.local_return_values[assignment.id] = {
                    "name": local_return,
                    "arguments": [self._argument_kind(arg) for arg in node.value.args],
                    "keyword_arguments": {
                        keyword.arg: self._argument_kind(keyword.value)
                        for keyword in node.value.keywords
                        if keyword.arg is not None
                    },
                }
            elif local_instance_return is not None:
                self.local_instance_return_values[assignment.id] = local_instance_return

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802
        if node.value is None:
            return
        self.visit(node.value)
        target = _dotted(node.value, self.bindings, self.handles)
        sdk = _sdk_call(target)
        sdk_field = _sdk_object_field_contract(target)
        binding = _binding_for_expression(node.value, self.bindings, self.handles)
        local_name = node.value.id if isinstance(node.value, ast.Name) else None
        if isinstance(node.target, ast.Name):
            self.handles.pop(node.target.id, None)
            self.local_aliases.pop(node.target.id, None)
            self.conditional_local_returns.pop(node.target.id, None)
            self.conditional_callables.pop(node.target.id, None)
            self.bindings.pop(node.target.id, None)
            self.value_kinds.pop(node.target.id, None)
            self.selector_constants.pop(node.target.id, None)
            self.data_assignments.discard(node.target.id)
            if sdk_field is not None:
                self._bind_sdk_field(node.target.id, target)
            elif sdk and not _is_call_result(node.value):
                self.handles[node.target.id] = sdk
            elif sdk and _is_call_result(node.value):
                self._bind_sdk_result(node.target.id, sdk)
            elif binding:
                imported_binding = dict(binding)
                if isinstance(node.value, ast.Call):
                    imported_binding["constructor_context"] = self._constructor_context(
                        node.value
                    )
                self.bindings[node.target.id] = imported_binding
            elif local_name and local_name in (
                self.local_functions | set(self.local_aliases)
            ):
                self.local_aliases[node.target.id] = self.local_aliases.get(
                    local_name, local_name
                )
                self.bindings[node.target.id] = {
                    "local_function": self.local_aliases[node.target.id]
                }
            elif (
                isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id in self.local_classes
            ):
                self.bindings[node.target.id] = {
                    "local_instance": node.value.func.id,
                    "callable_context": {
                        keyword.arg: callback
                        for keyword in node.value.keywords
                        if keyword.arg is not None
                        and (callback := self._callable_context(keyword.value)) is not None
                    },
                }
            elif kind := _literal_kind(node.value, self.value_kinds):
                self.value_kinds[node.target.id] = kind
            if _assignment_signature(node.value)[0] == "data_assignment":
                self.data_assignments.add(node.target.id)
            if route := _bounded_api_route_template(
                node.value, self.selector_constants, self.bindings, self.handles
            ):
                self.selector_constants[node.target.id] = route
            elif isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                self.selector_constants[node.target.id] = node.value.value

    def visit_AugAssign(self, node: ast.AugAssign) -> None:  # noqa: N802
        self.visit(node.value)
        if isinstance(node.target, ast.Name):
            self.handles.pop(node.target.id, None)
            self.local_aliases.pop(node.target.id, None)
            self.conditional_local_returns.pop(node.target.id, None)
            self.conditional_callables.pop(node.target.id, None)
            self.bindings.pop(node.target.id, None)
            self.value_kinds.pop(node.target.id, None)
            self.selector_constants.pop(node.target.id, None)
            self.data_assignments.discard(node.target.id)

    def visit_If(self, node: ast.If) -> None:  # noqa: N802
        self.visit(node.test)
        body_updates = _branch_binding_updates(node.body)
        else_updates = _branch_binding_updates(node.orelse)
        updated_names = set(body_updates) | set(else_updates)
        prior = {
            name: (
                _binding_signature(self, name)
                or (("data_assignment", name) if name in self.data_assignments else None)
            )
            for name in updated_names
        }
        prior_value_kinds = {
            name: self.value_kinds.get(name) for name in updated_names
        }
        prior_callable_targets = {
            name: self._current_callable_target(name) for name in updated_names
        }
        for statement in node.body:
            self.visit(statement)
        for statement in node.orelse:
            self.visit(statement)
        for name in updated_names:
            body_update = body_updates.get(name, prior[name])
            else_update = else_updates.get(name, prior[name])
            if body_update == else_update:
                continue
            if (
                all(
                    update is not None and update[0] == "data_assignment"
                    for update in (body_update, else_update)
                )
                and (prior[name] is None or prior[name][0] == "data_assignment")
            ):
                body_kind = (
                    _branch_value_kind(node.body, name, prior_value_kinds)
                    if name in body_updates
                    else prior_value_kinds[name]
                )
                else_kind = (
                    _branch_value_kind(node.orelse, name, prior_value_kinds)
                    if name in else_updates
                    else prior_value_kinds[name]
                )
                kinds = (
                    (body_kind, else_kind)
                    if prior[name] is None
                    else (prior_value_kinds[name], body_kind, else_kind)
                )
                if all(kind is not None for kind in kinds):
                    self.value_kinds[name] = (
                        kinds[0] if len(set(kinds)) == 1 else "json"
                    )
                else:
                    self.value_kinds.pop(name, None)
                self.handles.pop(name, None)
                self.local_aliases.pop(name, None)
                self.conditional_local_returns.pop(name, None)
                self.bindings.pop(name, None)
                self.selector_constants.pop(name, None)
                self.data_assignments.add(name)
                continue
            self.handles.pop(name, None)
            self.local_aliases.pop(name, None)
            self.value_kinds.pop(name, None)
            return_names = (
                self._branch_local_return_names(node.body, name)
                + self._branch_local_return_names(node.orelse, name)
            )
            if return_names:
                self.conditional_local_returns[name] = tuple(dict.fromkeys(return_names))
            else:
                self.conditional_local_returns.pop(name, None)
            candidates = list(self._conditional_targets(node.body, name)) + list(
                self._conditional_targets(node.orelse, name)
            )
            prior_target = prior_callable_targets[name]
            if prior_target is not None:
                if name not in body_updates:
                    candidates.append(prior_target)
                if name not in else_updates:
                    candidates.append(prior_target)
            if candidates:
                unique = _unique_bindings(candidates)
                self.conditional_callables[name] = unique
            else:
                self.conditional_callables.pop(name, None)
            self.bindings.pop(name, None)
            if candidates:
                self.bindings[name] = {"conditional_targets": list(unique)}
            self.selector_constants.pop(name, None)
            self.data_assignments.discard(name)

    def visit_Try(self, node: ast.Try | ast.TryStar) -> None:  # noqa: N802
        alternatives = [node.body, node.orelse, node.finalbody, *(handler.body for handler in node.handlers)]
        names = set().union(*(_branch_binding_updates(statements) for statements in alternatives))
        self.generic_visit(node)
        catches_import_error = any(
            handler.type is None
            or isinstance(handler.type, ast.Name) and handler.type.id == "ImportError"
            or isinstance(handler.type, ast.Attribute) and handler.type.attr == "ImportError"
            for handler in node.handlers
        )
        if catches_import_error:
            optional_modules = {
                alias.name
                for statement in node.body
                if isinstance(statement, ast.Import)
                for alias in statement.names
            } | {
                statement.module or ""
                for statement in node.body
                if isinstance(statement, ast.ImportFrom)
            }
            for binding in self.imports:
                if binding["module"] in optional_modules:
                    binding["optional"] = True
            for binding in self.bindings.values():
                if binding.get("module") in optional_modules:
                    binding["optional"] = True
        for name in names:
            candidates = tuple(
                candidate
                for statements in alternatives
                for candidate in self._conditional_targets(statements, name)
            )
            if not candidates:
                continue
            unique = _unique_bindings(candidates)
            self.conditional_callables[name] = unique
            self.bindings.pop(name, None)
            self.bindings[name] = {"conditional_targets": list(unique)}

    visit_TryStar = visit_Try

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._visit_definition_expressions(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._visit_definition_expressions(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
        for default in [*node.args.defaults, *node.args.kw_defaults]:
            if default is not None:
                self.visit(default)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        for decorator in node.decorator_list:
            self.visit(decorator)
        for expression in [*node.bases, *(keyword.value for keyword in node.keywords)]:
            self.visit(expression)
        for child in node.body:
            _visit_import_time_statement(child, self)

    def _visit_definition_expressions(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in [*node.args.defaults, *node.args.kw_defaults]:
            if default is not None:
                self.visit(default)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        target = _dotted(node.func, self.bindings, self.handles)
        static_getattr = _static_getattr_target(
            node.func, self.bindings, self.handles
        )
        binding = _binding_for_expression(node.func, self.bindings, self.handles)
        class_return_chain = _local_class_return_chain(node.func, self.local_classes)
        callback = _higher_order_callback(node, target)
        if callback is not None:
            self._emit_callback(callback[1], node.lineno)
        if (
            isinstance(node.func, ast.Call)
            and isinstance(node.func.func, ast.Name)
            and node.func.func.id == "getattr"
            and static_getattr is None
        ):
            subject = _dotted(node.func.args[0], self.bindings, self.handles)
            if subject and subject.startswith("bifrost."):
                self._emit(
                    {
                        "kind": "unknown",
                        "line": node.lineno,
                        "reason": "dynamic SDK dispatch",
                    }
                )
            elif (
                isinstance(node.func.args[0], ast.Call)
                and isinstance(node.func.args[0].func, ast.Name)
                and node.func.args[0].func.id
                in self.local_functions | set(self.local_aliases)
            ):
                self._emit(
                    {
                        "kind": "local_dynamic_dispatch",
                        "line": node.lineno,
                        "name": self.local_aliases.get(
                            node.func.args[0].func.id, node.func.args[0].func.id
                        ),
                    }
                )
        call_target = static_getattr or target
        sdk_data_field_method = _sdk_data_field_method(call_target)
        result_method, result_data_method = (
            _sdk_result_method(node.func, self.bindings, self.handles)
            if isinstance(node.func, ast.Attribute)
            else (None, False)
        )
        direct_object_method = (
            call_target
            if call_target and sdk_object_contract(call_target.rpartition(".")[0])
            else None
        )
        sdk_object_method = (
            _sdk_field_method_call(call_target)
            or result_method
            or direct_object_method
        )
        sdk = (
            None
            if sdk_data_field_method or result_data_method
            else _sdk_call(sdk_object_method or call_target)
        )
        if target == "bifrost.files._solution_query":
            # This shipped helper contributes only an optional query suffix to
            # a generic file route.  It is not a transport operation itself.
            pass
        elif sdk:
            if sdk_call_contract(sdk) is None:
                if sdk_object_method and sdk_object_is_data(
                    sdk_object_method.rpartition(".")[0]
                ):
                    self._emit(
                        {
                            "kind": "warning",
                            "line": node.lineno,
                            "reason": "unsupported SDK data-object method",
                        }
                    )
                else:
                    self._emit(
                        {
                            "kind": "unknown",
                            "line": node.lineno,
                            "reason": "unrecognized SDK call",
                        }
                    )
            else:
                self._emit(self._sdk_event(node, sdk))
        elif target in _SAFE_BIFROST_DECORATORS:
            pass
        elif target in {"eval", "exec"}:
            self._emit({"kind": "unknown", "line": node.lineno, "reason": target})
        elif sdk_data_field_method or result_data_method or _is_safe_data_method(
            node.func, self.value_kinds
        ):
            pass
        elif target == "getattr":
            static_target = _static_getattr_target(node, self.bindings, self.handles)
            subject = (
                _dotted(node.args[0], self.bindings, self.handles)
                if node.args
                else None
            )
            if (
                _is_context_data_field(static_target)
                or _is_known_sdk_call(static_target)
                or subject == "bifrost.context"
            ):
                pass
            elif static_target and static_target.startswith("bifrost.context."):
                self._emit(
                    {
                        "kind": "unknown",
                        "line": node.lineno,
                        "reason": "unrecognized SDK call",
                    }
                )
            elif static_target and static_target.startswith("bifrost."):
                pass
            elif subject and subject.startswith("bifrost."):
                self._emit(
                    {
                        "kind": "unknown",
                        "line": node.lineno,
                        "reason": "dynamic SDK dispatch",
                    }
                )
        elif target in {"importlib.import_module", "__import__"} or (
            binding
            and binding["module"] == "importlib"
            and binding.get("symbol") == "import_module"
        ):
            value = node.args[0] if node.args else None
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                level = len(value.value) - len(value.value.lstrip("."))
                self._emit(
                    {
                        "kind": "dynamic_import",
                        "line": node.lineno,
                        "module": value.value[level:],
                        "level": level,
                    }
                )
        else:
            if binding:
                module = binding["module"]
                if _is_sdk_module(module):
                    # ``_sdk_call`` above handles public SDK call shapes.  This
                    # is intentionally conservative for wrappers we do not know.
                    self._emit(
                        {
                            "kind": "unknown",
                            "line": node.lineno,
                            "reason": "unrecognized SDK call",
                        }
                    )
                else:
                    constructor = (
                        node.func.value
                        if isinstance(node.func, ast.Attribute)
                        and isinstance(node.func.value, ast.Call)
                        else None
                    )
                    self._emit(
                        {
                            "kind": "imported",
                            "line": node.lineno,
                            "module": module,
                            "level": binding["level"],
                            "symbol": binding.get("symbol"),
                            "member": binding.get("member"),
                            "optional": binding.get("optional", False),
                            "constructor_context": (
                                self._constructor_context(constructor)
                                if constructor is not None
                                else binding.get("constructor_context", {})
                            ),
                            "arguments": [
                                self._argument_kind(argument) for argument in node.args
                            ],
                            "keyword_arguments": {
                                keyword.arg: self._argument_kind(keyword.value)
                                for keyword in node.keywords
                                if keyword.arg is not None
                            },
                        }
                    )
            elif isinstance(node.func, ast.Name) and node.func.id in (
                self.local_functions | set(self.local_aliases)
            ):
                self._emit(
                    {
                        "kind": "local",
                        "line": node.lineno,
                        "name": self.local_aliases.get(node.func.id, node.func.id),
                        "arguments": [self._argument_kind(arg) for arg in node.args],
                        "keyword_arguments": {
                            keyword.arg: self._argument_kind(keyword.value)
                            for keyword in node.keywords
                            if keyword.arg is not None
                        },
                    }
                )
            elif isinstance(node.func, ast.Name) and node.func.id in self.local_classes:
                self._emit(
                    {
                        "kind": "local_class",
                        "line": node.lineno,
                        "name": node.func.id,
                        "callable_context": {
                            keyword.arg: callback
                            for keyword in node.keywords
                            if keyword.arg is not None
                            and (callback := self._callable_context(keyword.value)) is not None
                        },
                    }
                )
            elif (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in self.local_return_values
            ):
                returned = self.local_return_values[node.func.value.id]
                self._emit(
                    {
                        "kind": "local_return_method",
                        "line": node.lineno,
                        "name": returned["name"],
                        "method": node.func.attr,
                        "arguments": returned["arguments"],
                        "keyword_arguments": returned["keyword_arguments"],
                    }
                )
            elif (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in self.local_instance_return_values
            ):
                returned = self.local_instance_return_values[node.func.value.id]
                self._emit(
                    {
                        "kind": "local_instance_return_method",
                        "line": node.lineno,
                        "class": returned["class"],
                        "returned_method": returned["method"],
                        "method": node.func.attr,
                        "callable_context": returned["callable_context"],
                    }
                )
            elif class_return_chain is not None:
                class_name, returned_method, continued_method = class_return_chain
                self._emit(
                    {
                        "kind": "local_class_return_method",
                        "line": node.lineno,
                        "class": class_name,
                        "returned_method": returned_method,
                        "method": continued_method,
                    }
                )
            elif (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and self.bindings.get(node.func.value.id, {}).get("local_instance")
            ):
                self._emit(
                    {
                        "kind": "local_instance_method",
                        "line": node.lineno,
                        "class": self.bindings[node.func.value.id]["local_instance"],
                        "method": node.func.attr,
                        "callable_context": self.bindings[node.func.value.id].get(
                            "callable_context", {}
                        ),
                    }
                )
            elif self_path := _self_member_path(node.func):
                self._emit(
                    {
                        "kind": "self_field_method" if len(self_path) > 1 else "self_method",
                        "line": node.lineno,
                        "name": ".".join(self_path),
                    }
                )
            elif isinstance(node.func, ast.Attribute) and isinstance(
                node.func.value, ast.Name
            ):
                candidates = self.conditional_callables.get(node.func.value.id, ())
                for candidate in candidates:
                    if candidate["kind"] != "sdk_namespace":
                        continue
                    call = _sdk_call(f"{candidate['target']}.{node.func.attr}")
                    if call and sdk_call_contract(call) is not None:
                        self._emit(self._sdk_event(node, call))
                for name in self.conditional_local_returns.get(node.func.value.id, ()):
                    self._emit(
                        {
                            "kind": "local_return_method",
                            "line": node.lineno,
                            "name": name,
                            "method": node.func.attr,
                            "arguments": [],
                        }
                    )
                bound_method: dict[str, Any] = {
                    "kind": "bound_method",
                    "line": node.lineno,
                    "receiver": node.func.value.id,
                    "method": node.func.attr,
                }
                resource_events = {
                    call: event
                    for call in (
                        f"bifrost.api.{node.func.attr}",
                        f"bifrost.files.{node.func.attr}",
                        f"bifrost.client.{node.func.attr}",
                        f"bifrost.client.BifrostClient.{node.func.attr}",
                    )
                    if sdk_call_contract(call) is not None
                    for event in (self._sdk_event(node, call),)
                    if "resource_call" in event
                }
                if resource_events:
                    bound_method["resource_calls"] = {
                        call: event["resource_call"]
                        for call, event in resource_events.items()
                    }
                    bound_method["resource_parameter_selectors"] = {
                        call: event["parameter_selectors"]
                        for call, event in resource_events.items()
                        if "parameter_selectors" in event
                    }
                    bound_method["resource_file_body_selectors"] = {
                        call: event["file_body_selectors"]
                        for call, event in resource_events.items()
                        if "file_body_selectors" in event
                    }
                self._emit(bound_method)
            elif isinstance(node.func, ast.Name) and node.func.id in self.conditional_callables:
                for candidate in self.conditional_callables[node.func.id]:
                    if candidate["kind"] == "sdk_namespace":
                        call = _sdk_call(
                            f"{candidate['target']}.{node.func.id}"
                        )
                        if call and sdk_call_contract(call) is not None:
                            self._emit(self._sdk_event(node, call))
                    elif candidate["kind"] == "sdk_callable":
                        self._emit(self._sdk_event(node, candidate["call"]))
                    elif candidate["kind"] == "local":
                        self._emit(
                            {
                                "kind": "local",
                                "line": node.lineno,
                                "name": candidate["name"],
                                "arguments": [
                                    self._argument_kind(argument) for argument in node.args
                                ],
                                "keyword_arguments": {
                                    keyword.arg: self._argument_kind(keyword.value)
                                    for keyword in node.keywords
                                    if keyword.arg is not None
                                },
                            }
                        )
                    else:
                        self._emit(
                            {
                                "kind": "imported",
                                "line": node.lineno,
                                "module": candidate["module"],
                                "level": candidate["level"],
                                "symbol": candidate.get("symbol"),
                                "member": candidate.get("member"),
                                "optional": candidate.get("optional", False),
                                "arguments": [
                                    self._argument_kind(argument) for argument in node.args
                                ],
                                "keyword_arguments": {
                                    keyword.arg: self._argument_kind(keyword.value)
                                    for keyword in node.keywords
                                    if keyword.arg is not None
                                },
                            }
                        )
            elif (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Call)
                and isinstance(node.func.value.func, ast.Name)
                and node.func.value.func.id in (
                    self.local_functions | set(self.local_aliases)
                )
            ):
                self._emit(
                    {
                        "kind": "local_return_method",
                        "line": node.lineno,
                        "name": self.local_aliases.get(
                            node.func.value.func.id, node.func.value.func.id
                        ),
                        "method": node.func.attr,
                        "arguments": [
                            self._argument_kind(argument)
                            for argument in node.func.value.args
                        ],
                    }
                )
        self.generic_visit(node)


class _FunctionScopeBindings(ast.NodeVisitor):
    """Collect names the compiler treats as local to one function scope."""

    def __init__(self) -> None:
        self.names: set[str] = set()
        self.globals: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        if isinstance(node.ctx, ast.Store):
            self.names.add(node.id)

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        self.names.update(
            alias.asname or alias.name.split(".")[0] for alias in node.names
        )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        self.names.update(
            alias.asname or alias.name for alias in node.names if alias.name != "*"
        )

    def visit_Global(self, node: ast.Global) -> None:  # noqa: N802
        self.globals.update(node.names)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self.names.add(node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self.names.add(node.name)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        self.names.add(node.name)

    def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
        # A lambda creates its own local scope.
        return


def _function_scope_bindings(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Return names that hide module imports throughout a function body."""
    collector = _FunctionScopeBindings()
    for statement in node.body:
        collector.visit(statement)
    arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    collector.names.update(argument.arg for argument in arguments)
    if node.args.vararg is not None:
        collector.names.add(node.args.vararg.arg)
    if node.args.kwarg is not None:
        collector.names.add(node.args.kwarg.arg)
    return collector.names - collector.globals


def _function_parameters(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[str, ...]:
    """Return positional and keyword-only parameter names in call order."""
    return tuple(
        argument.arg
        for argument in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    )


def _is_dataclass_decorator(node: ast.AST) -> bool:
    return (isinstance(node, ast.Name) and node.id == "dataclass") or (
        isinstance(node, ast.Attribute) and node.attr == "dataclass"
    )


def _dataclass_field_option(field: ast.AnnAssign, name: str) -> bool:
    """Read one literal ``dataclasses.field`` option without evaluating it."""
    if not isinstance(field.value, ast.Call):
        return False
    target = field.value.func
    if not (
        isinstance(target, ast.Name)
        and target.id == "field"
        or isinstance(target, ast.Attribute)
        and target.attr == "field"
    ):
        return False
    return any(
        keyword.arg == name
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value is (False if name == "init" else True)
        for keyword in field.value.keywords
    )


def _constructor_fields(
    class_node: ast.ClassDef,
    methods: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
) -> tuple[str, ...]:
    """Return the positional constructor fields for explicit or dataclass initializers."""
    initializer = methods.get("__init__")
    if initializer is not None:
        parameters = [*initializer.args.posonlyargs, *initializer.args.args]
        if parameters and parameters[0].arg in {"self", "cls"}:
            parameters = parameters[1:]
        return tuple(parameter.arg for parameter in parameters)

    dataclass_decorator = next(
        (
            item
            for item in class_node.decorator_list
            if _is_dataclass_decorator(item.func if isinstance(item, ast.Call) else item)
        ),
        None,
    )
    if dataclass_decorator is None:
        return ()
    dataclass_call = (
        dataclass_decorator if isinstance(dataclass_decorator, ast.Call) else None
    )
    if any(
        keyword.arg == "kw_only"
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value is True
        for keyword in (dataclass_call.keywords if dataclass_call is not None else [])
    ):
        return ()

    fields: list[str] = []
    for field in class_node.body:
        if not isinstance(field, ast.AnnAssign) or not isinstance(field.target, ast.Name):
            continue
        annotation = (
            field.annotation.value
            if isinstance(field.annotation, ast.Subscript)
            else field.annotation
        )
        if (
            isinstance(annotation, ast.Name) and annotation.id == "ClassVar"
        ) or (
            isinstance(annotation, ast.Attribute) and annotation.attr == "ClassVar"
        ):
            continue
        if _dataclass_field_option(field, "init"):
            continue
        if _dataclass_field_option(field, "kw_only"):
            continue
        fields.append(field.target.id)
    return tuple(fields)


def _annotation_mentions_execution_context(annotation: ast.AST) -> bool:
    return any(
        (
            isinstance(part, ast.Name) and part.id == "ExecutionContext"
        )
        or (
            isinstance(part, ast.Attribute) and part.attr == "ExecutionContext"
        )
        or (
            isinstance(part, ast.Constant)
            and isinstance(part.value, str)
            and "ExecutionContext" in part.value
        )
        for part in ast.walk(annotation)
    )


def _entry_context_parameters(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    bindings: dict[str, dict[str, Any]],
) -> tuple[str | None, str | None]:
    """Return the proven and uncertain engine-injected first parameters."""
    parameters = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    if not parameters:
        return None, None
    parameter = parameters[0]
    if parameter.arg == "context":
        return parameter.arg, None
    annotation = parameter.annotation
    if annotation is None:
        return None, None
    if (
        any(
            binding.get("module") == "__future__"
            and binding.get("symbol") == "annotations"
            for binding in bindings.values()
        )
        and _annotation_mentions_execution_context(annotation)
    ):
        return parameter.arg, None
    annotation_target = _dotted(annotation, bindings, {})
    if annotation_target == "bifrost.ExecutionContext":
        return parameter.arg, None
    if isinstance(annotation, ast.Name) and annotation.id == "ExecutionContext":
        return parameter.arg, None
    if isinstance(annotation, ast.Attribute) and annotation.attr == "ExecutionContext":
        return parameter.arg, None
    if (
        isinstance(annotation, ast.Constant)
        and isinstance(annotation.value, str)
        and "ExecutionContext" in annotation.value
    ):
        return parameter.arg, None
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        return None, None
    if isinstance(annotation, ast.Name) and annotation.id in {
        "Any",
        "bool",
        "bytes",
        "dict",
        "float",
        "int",
        "list",
        "object",
        "set",
        "str",
        "tuple",
    }:
        return None, None
    if isinstance(annotation, ast.Subscript) and isinstance(annotation.value, ast.Name):
        if annotation.value.id in {"dict", "list", "set", "tuple"}:
            return None, None
    return None, parameter.arg


class _DirectReturns(ast.NodeVisitor):
    """Collect returns without entering nested callable scopes."""

    def __init__(self) -> None:
        self.values: list[ast.AST | None] = []
        self.assignments: list[ast.Assign | ast.AnnAssign] = []

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        self.assignments.append(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802
        self.assignments.append(node)

    def visit_Return(self, node: ast.Return) -> None:  # noqa: N802
        self.values.append(node.value)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        return


def _return_shape(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    bindings: dict[str, dict[str, Any]],
    class_names: set[str],
    function_shapes: dict[str, str] | None = None,
    owner_class: str | None = None,
) -> str:
    """Summarize a direct return value without retaining user data."""
    collector = _DirectReturns()
    for statement in node.body:
        collector.visit(statement)
    def constructor_shape(value: ast.AST | None) -> str:
        value = value.value if isinstance(value, ast.Await) else value
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
            if value.func.id in class_names:
                return f"local_instance:{value.func.id}"
            if value.func.id == "cls" and owner_class is not None:
                return f"local_instance:{owner_class}"
            return (function_shapes or {}).get(value.func.id, "unknown")
        return "unknown"

    assigned_shapes: dict[str, set[str]] = {}
    for assignment in collector.assignments:
        value = assignment.value
        shape = constructor_shape(value)
        for target in assignment.targets if isinstance(assignment, ast.Assign) else [assignment.target]:
            assigned_shapes.setdefault(
                _dotted(target, {}, {}) or ast.dump(target), set()
            ).add(shape)
    shapes: set[str] = set()
    for value in collector.values:
        if value is None:
            return "unknown"
        if _literal_kind(value, {}) is not None:
            shapes.add("json")
            continue
        constructed = constructor_shape(value)
        if constructed != "unknown":
            shapes.add(constructed)
            continue
        assigned = assigned_shapes.get(_dotted(value, {}, {}) or ast.dump(value), set())
        if len(assigned) == 1 and "unknown" not in assigned:
            shapes.update(assigned)
            continue
        target = _dotted(value, bindings, {})
        returned = value.value if isinstance(value, ast.Await) else value
        if (
            isinstance(returned, ast.Call)
            and target in {"json.load", "json.loads"}
        ):
            shapes.add("json")
            continue
        if target and target.startswith("bifrost."):
            call = _sdk_call(target) if isinstance(returned, ast.Call) else None
            contract = sdk_call_contract(call) if call else None
            if contract is not None:
                if contract.return_kind in {"json_object", "json_list", "scalar", "none"}:
                    shapes.add("json")
                    continue
                if contract.sdk_type:
                    shapes.add(f"sdk:{contract.sdk_type}")
                    continue
            shapes.add(f"sdk:{target}")
            continue
        return "unknown"
    return next(iter(shapes)) if len(shapes) == 1 else "unknown"


def _scope_summary(
    statements: list[ast.stmt],
    bindings: dict[str, dict[str, Any]],
    local_functions: set[str],
    local_classes: set[str] | None = None,
    shadowed_names: set[str] | None = None,
    value_kinds: dict[str, str] | None = None,
    parameter_names: set[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    visitor = _ScopeSummary(
        bindings, local_functions, local_classes, value_kinds, parameter_names
    )
    for name in shadowed_names or ():
        visitor.bindings.pop(name, None)
    for statement in statements:
        _visit_import_time_statement(statement, visitor)
    if visitor.truncated:
        visitor.events.append(
            {
                "kind": "unknown",
                "line": 0,
                "reason": f"summary event limit reached ({MAX_EVENTS_PER_SCOPE})",
            }
        )
    return visitor.events, visitor.bindings, visitor.imports


def _visit_import_time_statement(statement: ast.stmt, visitor: _ScopeSummary) -> None:
    """Visit expressions Python evaluates while defining modules and classes.

    A function's body is deferred, while decorators and default expressions are
    evaluated immediately.  Class bodies are also immediate, recursively.
    """
    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
        visitor._visit_definition_expressions(statement)
        return
    if isinstance(statement, ast.ClassDef):
        visitor.visit_ClassDef(statement)
        return
    visitor.visit(statement)


def _literal_module_maps(tree: ast.Module) -> dict[str, dict[str, str]]:
    """Keep bounded, finite Python name-to-module mappings for imported facades."""
    maps: dict[str, dict[str, str]] = {}
    for statement in tree.body:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        value = statement.value
        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
        if not isinstance(value, ast.Dict) or len(value.keys) > MAX_EVENTS_PER_SCOPE:
            continue
        entries: dict[str, str] = {}
        for key, item in zip(value.keys, value.values):
            if not (
                isinstance(key, ast.Constant)
                and isinstance(key.value, str)
                and key.value.isidentifier()
                and isinstance(item, ast.Constant)
                and isinstance(item.value, str)
                and len(item.value) <= MAX_SELECTOR_VALUE_CHARS
                and all(part.isidentifier() for part in item.value.split("."))
            ):
                entries = {}
                break
            entries[key.value] = item.value
        if entries:
            for target in targets:
                if isinstance(target, ast.Name):
                    maps[target.id] = entries
    return maps


def _facade_router(tree: ast.Module, bindings: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Recognize a finite module facade and its forwarding/client wrapper."""
    maps = _literal_module_maps(tree)
    getattr_node = next((
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "__getattr__"
    ), None)
    if getattr_node is None or not getattr_node.args.args:
        return {}
    requested_name = getattr_node.args.args[0].arg
    selected_maps = {
        node.func.value.id
        for node in ast.walk(getattr_node)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and isinstance(node.func.value, ast.Name)
        and (
            node.func.value.id in maps
            or "module" in bindings.get(node.func.value.id, {})
            and bindings[node.func.value.id].get("symbol")
        )
        and node.args
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == requested_name
    }
    if len(selected_maps) != 1:
        return {}
    selected_map = next(iter(selected_maps))
    assignments = {
        node.targets[0].id: node.value
        for node in ast.walk(getattr_node)
        if isinstance(node, ast.Assign) and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    }
    selected_children = {
        name for name, value in assignments.items()
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute)
        and isinstance(value.func.value, ast.Name) and value.func.value.id == selected_map
        and value.func.attr == "get" and value.args
        and isinstance(value.args[0], ast.Name) and value.args[0].id == requested_name
    }
    functions = {
        node.name: node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    module_bindings: dict[str, tuple[str, int]] = {}
    for name, value in assignments.items():
        if not (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
                and value.args and isinstance(value.args[0], ast.Name)
                and value.args[0].id in selected_children):
            continue
        loader = functions.get(value.func.id)
        if loader is None or not loader.args.args:
            continue
        parameter = loader.args.args[0].arg
        for node in ast.walk(loader):
            if not (isinstance(node, ast.Call)
                    and _dotted(node.func, bindings, {}) == "importlib.import_module"
                    and node.args and isinstance(node.args[0], ast.JoinedStr)):
                continue
            parts = node.args[0].values
            if (len(parts) == 2 and isinstance(parts[0], ast.Constant)
                    and isinstance(parts[0].value, str) and parts[0].value.endswith(".")
                    and isinstance(parts[1], ast.FormattedValue)
                    and isinstance(parts[1].value, ast.Name) and parts[1].value.id == parameter):
                module_bindings[name] = (parts[0].value, 0)
            elif (len(parts) == 3 and isinstance(parts[0], ast.FormattedValue)
                    and isinstance(parts[0].value, ast.Name) and parts[0].value.id == "__name__"
                    and isinstance(parts[1], ast.Constant) and parts[1].value == "."
                    and isinstance(parts[2], ast.FormattedValue)
                    and isinstance(parts[2].value, ast.Name) and parts[2].value.id == parameter):
                module_bindings[name] = ("", 1)
    function_names = {
        name for name, value in assignments.items()
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
        and value.func.id == "getattr" and len(value.args) >= 2
        and isinstance(value.args[0], ast.Name) and value.args[0].id in module_bindings
        and isinstance(value.args[1], ast.Name) and value.args[1].id == requested_name
    }
    if len(function_names) != 1:
        return {}
    wrapped_name = next(iter(function_names))
    wrappers = {
        node.name: node
        for node in ast.walk(getattr_node)
        if node is not getattr_node and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for wrapper_name, wrapper in wrappers.items():
        returned = any(
            isinstance(node, ast.Return)
            and isinstance(node.value, ast.Name)
            and node.value.id == wrapper_name
            for node in ast.walk(getattr_node)
        )
        factory_targets = {
            target.id
            for node in ast.walk(wrapper)
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            for target in (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )
            if isinstance(target, ast.Name)
            and isinstance(node.value, ast.Await)
            and isinstance(node.value.value, ast.Call)
        }
        invokes_child = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == wrapped_name
            and node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id in factory_targets
            for node in ast.walk(wrapper)
        )
        if returned and factory_targets and invokes_child:
            retrieval = assignments[wrapped_name]
            assert isinstance(retrieval, ast.Call) and isinstance(retrieval.args[0], ast.Name)
            prefix, level = module_bindings[retrieval.args[0].id]
            return {
                "exports": maps.get(selected_map, {}),
                "map_binding": bindings.get(selected_map),
                "prefix": prefix, "level": level,
                "wrapper": f"__getattr__.{wrapper_name}",
            }
    return {}


def _lazy_facade_router(tree: ast.Module) -> dict[str, str]:
    """Recognize a local-instance module facade with a concrete client method."""
    classes = {
        node.name: node for node in tree.body if isinstance(node, ast.ClassDef)
    }
    instances = {
        target.id: statement.value.func.id
        for statement in tree.body
        if isinstance(statement, (ast.Assign, ast.AnnAssign))
        and isinstance(statement.value, ast.Call)
        and isinstance(statement.value.func, ast.Name)
        and statement.value.func.id in classes
        for target in (
            statement.targets if isinstance(statement, ast.Assign) else [statement.target]
        )
        if isinstance(target, ast.Name)
    }
    getattr_node = next(
        (
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "__getattr__"
            and node.args.args
        ),
        None,
    )
    if getattr_node is None:
        return {}
    requested_name = getattr_node.args.args[0].arg
    forwarded_instances = {
        node.value.args[0].id
        for node in ast.walk(getattr_node)
        if isinstance(node, ast.Return)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "getattr"
        and len(node.value.args) >= 2
        and isinstance(node.value.args[0], ast.Name)
        and isinstance(node.value.args[1], ast.Name)
        and node.value.args[1].id == requested_name
        and node.value.args[0].id in instances
    }
    if len(forwarded_instances) != 1:
        return {}
    instance = next(iter(forwarded_instances))
    lazy_class = classes[instances[instance]]
    lazy_getattr = next(
        (
            node
            for node in lazy_class.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "__getattr__"
            and node.args.args
        ),
        None,
    )
    if lazy_getattr is None:
        return {}
    lazy_name = lazy_getattr.args.args[-1].arg
    wrappers = {
        node.name: node
        for node in lazy_getattr.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for wrapper_name, wrapper in wrappers.items():
        returned = any(
            isinstance(node, ast.Return)
            and isinstance(node.value, ast.Name)
            and node.value.id == wrapper_name
            for node in ast.walk(lazy_getattr)
        )
        factories = {
            node.value.value.func.attr
            for node in ast.walk(wrapper)
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            and isinstance(node.value, ast.Await)
            and isinstance(node.value.value, ast.Call)
            and isinstance(node.value.value.func, ast.Attribute)
            and isinstance(node.value.value.func.value, ast.Name)
            and node.value.value.func.value.id == "self"
        }
        forwards_name = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Name)
            and node.args[1].id == lazy_name
            for node in ast.walk(wrapper)
        )
        if returned and len(factories) == 1 and forwards_name:
            forwarded_receivers = {
                node.args[0].id
                for node in ast.walk(wrapper)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "getattr" and len(node.args) >= 2
                and isinstance(node.args[0], ast.Name)
                and isinstance(node.args[1], ast.Name) and node.args[1].id == lazy_name
            }
            client_factories = {
                node.value.func.id
                for node in ast.walk(wrapper)
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and any(isinstance(target, ast.Name) and target.id in forwarded_receivers
                        for target in node.targets)
            }
            return {
                "class": lazy_class.name,
                "factory": next(iter(factories)),
                "wrapper": f"__getattr__.{wrapper_name}",
                "client_factory": next(iter(client_factories)) if len(client_factories) == 1 else "",
            }
    return {}


def summarize_source(source: str) -> dict[str, Any]:
    """Return a bounded, JSON-serializable static summary without retaining source or AST."""
    if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        return {
            "version": ANALYZER_VERSION,
            "functions": {},
            "classes": {},
            "bindings": {},
            "top_level": [],
            "parse_error": f"source exceeds maximum size ({MAX_SOURCE_BYTES} bytes)",
        }
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        return {
            "version": ANALYZER_VERSION,
            "functions": {},
            "classes": {},
            "bindings": {},
            "top_level": [],
            "parse_error": f"{error.msg} at line {error.lineno}",
        }

    if sum(1 for _ in ast.walk(tree)) > MAX_AST_NODES:
        return {
            "version": ANALYZER_VERSION,
            "functions": {},
            "classes": {},
            "bindings": {},
            "top_level": [],
            "parse_error": f"source exceeds maximum AST nodes ({MAX_AST_NODES})",
        }

    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    class_nodes = {
        node.name: node for node in tree.body if isinstance(node, ast.ClassDef)
    }
    names = set(functions)
    top_level, bindings, _ = _scope_summary(tree.body, {}, names, set(class_nodes))
    data_exports = _exported_data_values(tree)
    function_summaries: dict[str, dict[str, Any]] = {}
    for name, node in functions.items():
        nested_functions = {
            nested.name: nested
            for nested in node.body
            if isinstance(nested, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        scope_functions = names | set(nested_functions)
        events, function_bindings, function_imports = _scope_summary(
            node.body,
            bindings,
            scope_functions,
            set(class_nodes),
            _function_scope_bindings(node),
            None,
            set(_function_parameters(node)),
        )
        context_parameter, uncertain_context_parameter = _entry_context_parameters(
            node, bindings
        )
        function_summaries[name] = {
            "events": events,
            "bindings": function_bindings,
            "imports": function_imports,
            "parameters": list(_function_parameters(node)),
            "context_parameter": context_parameter,
            "uncertain_context_parameter": uncertain_context_parameter,
            "return_shape": _return_shape(node, function_bindings, set(class_nodes)),
            "return_callable_fields": _return_callable_fields(
                node, function_bindings, set(class_nodes)
            ),
            "return_imported_instance": _return_imported_instance(
                node, function_bindings
            ),
        }
        for nested_name, nested in nested_functions.items():
            nested_events, nested_bindings, nested_imports = _scope_summary(
                nested.body,
                function_bindings,
                scope_functions,
                set(class_nodes),
                _function_scope_bindings(nested),
                None,
                set(_function_parameters(nested)),
            )
            function_summaries[nested_name] = {
                "events": nested_events,
                "bindings": nested_bindings,
                "imports": nested_imports,
                "parameters": list(_function_parameters(nested)),
                "context_parameter": None,
                "uncertain_context_parameter": None,
                "return_shape": _return_shape(
                    nested, nested_bindings, set(class_nodes)
                ),
                "return_imported_instance": _return_imported_instance(
                    nested, nested_bindings
                ),
            }

    facade_router = _facade_router(tree, bindings)
    if facade_router:
        wrapper_name = facade_router["wrapper"]
        wrapper = next(
            node for node in ast.walk(functions["__getattr__"])
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == wrapper_name.split(".", 1)[1]
        )
        wrapper_events, _, wrapper_imports = _scope_summary(
            wrapper.body, bindings, names, set(class_nodes),
            _function_scope_bindings(wrapper), None, set(_function_parameters(wrapper)),
        )
        function_summaries[wrapper_name] = {
            "events": wrapper_events, "imports": wrapper_imports,
            "parameters": list(_function_parameters(wrapper)),
        }

    class_summaries: dict[str, dict[str, Any]] = {}
    for class_node in class_nodes.values():
        methods = {
            method.name: method
            for method in class_node.body
            if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        method_summaries: dict[str, dict[str, Any]] = {}
        for name, method in methods.items():
            events, method_bindings, method_imports = _scope_summary(
                method.body,
                bindings,
                names | set(methods),
                set(class_nodes),
                _function_scope_bindings(method),
                None,
                set(_function_parameters(method)),
            )
            method_summaries[name] = {
                "events": events,
                "bindings": method_bindings,
                "imports": method_imports,
                "parameters": list(_function_parameters(method)),
                "return_shape": _return_shape(
                    method, method_bindings, set(class_nodes),
                    {name: function.get("return_shape", "unknown")
                     for name, function in function_summaries.items()},
                    class_node.name,
                ),
            }
            for nested in method.body:
                if not isinstance(nested, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                nested_events, nested_bindings, nested_imports = _scope_summary(
                    nested.body,
                    method_bindings,
                    names | set(methods) | {nested.name},
                    set(class_nodes),
                    _function_scope_bindings(nested),
                    None,
                    set(_function_parameters(nested)),
                )
                method_summaries[f"{name}.{nested.name}"] = {
                    "events": nested_events,
                    "bindings": nested_bindings,
                    "imports": nested_imports,
                    "parameters": list(_function_parameters(nested)),
                    "return_shape": _return_shape(
                        nested, nested_bindings, set(class_nodes)
                    ),
                }
        bases: list[dict[str, Any]] = []
        data_methods: set[str] = set()
        data_fields: dict[str, str] = {}
        field_shapes: dict[str, str] = {}
        field_imports: dict[str, dict[str, Any]] = {}
        callable_fields: set[str] = set()
        for base in class_node.bases:
            binding = _binding_for_expression(base, bindings, {})
            if binding:
                bases.append({"kind": "import", **binding})
            elif isinstance(base, ast.Name) and (
                base.id in class_nodes
                or not isinstance(getattr(builtins, base.id, None), type)
            ):
                bases.append({"kind": "local", "name": base.id})
            if isinstance(base, ast.Name):
                data_methods.update(_DATA_METHODS.get(base.id, ()))
        for field in class_node.body:
            if not isinstance(field, ast.AnnAssign) or not isinstance(field.target, ast.Name):
                continue
            kind = _annotation_data_kind(field.annotation)
            if kind is not None:
                data_fields[field.target.id] = kind
            elif local_class := _annotation_local_class(
                field.annotation, set(class_nodes)
            ):
                field_shapes[field.target.id] = local_class
            elif imported_class := _annotation_imported_class(field.annotation, bindings):
                field_imports[field.target.id] = imported_class
            if _is_callable_annotation(field.annotation):
                callable_fields.add(field.target.id)
        literal_callback_maps: dict[str, list[dict[str, Any]]] = {}
        for field in class_node.body:
            if not (
                isinstance(field, (ast.Assign, ast.AnnAssign))
                and isinstance(field.value, ast.Dict)
            ):
                continue
            targets = (
                field.targets if isinstance(field, ast.Assign) else [field.target]
            )
            if len(targets) != 1 or not isinstance(targets[0], ast.Name):
                continue
            candidates = [
                _callable_target(value, bindings, {}, names)
                for value in field.value.values
            ]
            if candidates and all(candidate is not None for candidate in candidates):
                literal_callback_maps[targets[0].id] = [
                    candidate for candidate in candidates if candidate is not None
                ]
        field_constructors: dict[str, dict[str, Any]] = {}
        field_callback_targets: dict[str, list[dict[str, Any]]] = {}
        for method in methods.values():
            for statement in _nested_assignments(method.body):
                if statement.value is None:
                    continue
                targets = (
                    statement.targets
                    if isinstance(statement, ast.Assign)
                    else [statement.target]
                )
                for target in targets:
                    for field_name, value in _self_field_assignments(
                        target, statement.value
                    ):
                        if (
                            isinstance(value, ast.Subscript)
                            and isinstance(value.value, ast.Attribute)
                            and isinstance(value.value.value, ast.Name)
                            and value.value.value.id == "self"
                            and value.value.attr in literal_callback_maps
                        ):
                            field_callback_targets[field_name] = literal_callback_maps[
                                value.value.attr
                            ]
        init_node = methods.get("__init__")
        init_bindings = method_summaries.get("__init__", {}).get("bindings", {})
        init_parameters = set(_function_parameters(init_node)) if init_node else set()
        if init_node is not None:
            for statement in init_node.body:
                if not (
                    isinstance(statement, (ast.Assign, ast.AnnAssign))
                    and statement.value is not None
                ):
                    continue
                targets = (
                    statement.targets
                    if isinstance(statement, ast.Assign)
                    else [statement.target]
                )
                for target in targets:
                    for field_name, value in _self_field_assignments(
                        target, statement.value
                    ):
                        if isinstance(value, ast.Call):
                            binding = _binding_for_expression(
                                value.func, init_bindings, {}
                            )
                            if binding and "module" in binding:
                                field_constructors[field_name] = dict(binding)
                        elif isinstance(value, ast.Name) and value.id in init_parameters:
                            field_callback_targets[field_name] = [
                                {"kind": "parameter", "name": value.id}
                            ]
                        elif (
                            isinstance(value, ast.BoolOp)
                            and isinstance(value.op, ast.Or)
                            and any(
                                isinstance(candidate, ast.Lambda)
                                for candidate in value.values
                            )
                            and isinstance(value.values[0], ast.Name)
                            and value.values[0].id in init_parameters
                        ):
                            fallback = next(
                                (
                                    _callable_target(
                                        candidate.body,
                                        init_bindings,
                                        {},
                                        names,
                                    )
                                    for candidate in value.values
                                    if isinstance(candidate, ast.Lambda)
                                ),
                                None,
                            )
                            field_callback_targets[field_name] = [
                                {
                                    "kind": "parameter_or",
                                    "name": value.values[0].id,
                                    "fallback": fallback,
                                }
                            ]
                        elif (
                            isinstance(value, ast.Subscript)
                            and isinstance(value.value, ast.Attribute)
                            and isinstance(value.value.value, ast.Name)
                            and value.value.value.id == "self"
                            and value.value.attr in literal_callback_maps
                        ):
                            field_callback_targets[field_name] = literal_callback_maps[
                                value.value.attr
                            ]
        class_summaries[class_node.name] = {
            "methods": method_summaries,
            "bases": bases,
            "constructor_fields": list(_constructor_fields(class_node, methods)),
            "data_fields": data_fields,
            "field_shapes": field_shapes,
            "field_imports": field_imports,
            "field_constructors": field_constructors,
            "field_callback_targets": field_callback_targets,
            "data_methods": sorted(data_methods),
            "callable_fields": sorted(callable_fields),
        }

    return {
        "version": ANALYZER_VERSION,
        "functions": function_summaries,
        "classes": class_summaries,
        "bindings": bindings,
        "data_exports": data_exports,
        "facade_router": facade_router,
        "lazy_facade_router": _lazy_facade_router(tree),
        "literal_module_maps": _literal_module_maps(tree),
        "top_level": top_level,
    }


ResolveImport = Callable[[SourceFile, str, int], Awaitable[SourceFile | None]]
Summarize = Callable[[SourceFile], Awaitable[dict[str, Any]]]


def _label(source_file: SourceFile, function: str) -> str:
    return f"{source_file.path}::{function}"


def _is_stdlib(module: str) -> bool:
    return module.split(".")[0] in sys.stdlib_module_names


async def analyze_workflow(
    entry: SourceFile,
    function_name: str,
    resolve_import: ResolveImport,
    summarize: Summarize,
    *,
    external_module_roots: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Analyze one workflow entry point without evaluating any user source."""
    summaries: dict[str, dict[str, Any]] = {}
    source_descriptors: dict[str, SourceFile] = {}
    dependencies: dict[str, str] = {}
    initialized: set[str] = set()
    active_functions: set[tuple[str, str, tuple[Any, ...] | str]] = set()
    completed_functions: set[tuple[str, str, tuple[Any, ...] | str]] = set()
    sdk_calls: set[str] = set()
    evidence: list[dict[str, Any]] = []
    resource_calls: list[dict[str, Any]] = []
    unknowns: set[str] = set()
    warnings: set[str] = set()
    graph_steps = 0
    graph_limited = False
    evidence_limited = False
    sdk_calls_limited = False
    resource_calls_limited = False

    async def load(source_file: SourceFile) -> dict[str, Any]:
        if source_file.key not in dependencies:
            if len(dependencies) >= MAX_DEPENDENCIES:
                unknowns.add(
                    f"{source_file.path}: dependency limit reached ({MAX_DEPENDENCIES})"
                )
                return {"parse_error": "dependency limit reached"}
            dependencies[source_file.key] = source_file.content_hash
            source_descriptors[source_file.key] = SourceFile(
                key=source_file.key, path=source_file.path, source="",
                content_hash=source_file.content_hash, kind=source_file.kind,
            )
        if source_file.key not in summaries:
            summaries[source_file.key] = await summarize(source_file)
        return summaries[source_file.key]

    def bind_callback_origins(
        source_file: SourceFile, context: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            name: {**callback, "source_key": callback.get("source_key", source_file.key)}
            if isinstance(callback, dict) and callback.get("kind") in {"local", "imported"}
            else callback
            for name, callback in context.items()
        }

    def consume_graph_step(source_file: SourceFile) -> bool:
        nonlocal graph_steps, graph_limited
        if graph_limited:
            return False
        graph_steps += 1
        if graph_steps > MAX_GRAPH_STEPS:
            graph_limited = True
            unknowns.add(
                f"{source_file.path}: graph traversal limit reached ({MAX_GRAPH_STEPS})"
            )
            return False
        return True

    def add_unknown(source_file: SourceFile, event: dict[str, Any]) -> None:
        unknowns.add(f"{source_file.path}:{event['line']}: {event['reason']}")

    def add_sdk_call(
        source_file: SourceFile, event: dict[str, Any], via: list[str]
    ) -> None:
        nonlocal evidence_limited, sdk_calls_limited
        call = event["call"]
        if call in sdk_calls or len(sdk_calls) < MAX_SDK_CALLS:
            sdk_calls.add(call)
        elif not sdk_calls_limited:
            sdk_calls_limited = True
            unknowns.add(
                f"{source_file.path}: SDK call limit reached ({MAX_SDK_CALLS})"
            )
        if len(evidence) < MAX_EVIDENCE:
            evidence.append(
                {
                    "path": source_file.path,
                    "line": event["line"],
                    "call": call,
                    "via": list(via),
                }
            )
        elif not evidence_limited:
            evidence_limited = True
            unknowns.add(
                f"{source_file.path}: evidence limit reached ({MAX_EVIDENCE})"
            )

    def add_resource_call(source_file: SourceFile, event: dict[str, Any]) -> None:
        nonlocal resource_calls_limited
        resource_call = event.get("resource_call")
        if resource_call is None:
            return
        if len(resource_calls) < MAX_EVIDENCE:
            resource_calls.append(
                {
                    "call": event["call"],
                    "source_key": source_file.key,
                    "path": source_file.path,
                    "line": event["line"],
                    "selectors": resource_call["selectors"],
                    "dynamic_selectors": resource_call["dynamic_selectors"],
                    "selector_reasons": resource_call["selector_reasons"],
                }
            )
        elif not resource_calls_limited:
            resource_calls_limited = True
            unknowns.add(
                f"{source_file.path}: resource call evidence limit reached "
                f"({MAX_EVIDENCE})"
            )

    def bind_parameter_selectors(
        event: dict[str, Any], parameter_context: dict[str, str]
    ) -> dict[str, Any]:
        """Materialize bounded route templates at one helper invocation."""
        parameter_selectors = event.get("parameter_selectors", {})
        resource_call = event.get("resource_call")
        if not parameter_selectors or resource_call is None:
            return event
        selectors = dict(resource_call["selectors"])
        dynamic_selectors = list(resource_call["dynamic_selectors"])
        selector_reasons = dict(resource_call["selector_reasons"])
        changed = False
        for selector, parameter in parameter_selectors.items():
            value = parameter_context.get(parameter, "unknown")
            if not value.startswith("api_path:"):
                continue
            path = value.removeprefix("api_path:")
            if (
                not path.startswith("/api/")
                or len(path) > MAX_SELECTOR_VALUE_CHARS
            ):
                continue
            selectors[selector] = path
            dynamic_selectors = [
                candidate for candidate in dynamic_selectors if candidate != selector
            ]
            selector_reasons.pop(selector, None)
            changed = True
        if not changed:
            return event
        path = selectors.get("path")
        file_body_selectors = event.get("file_body_selectors", {})
        file_routes = {
            "/api/files/read",
            "/api/files/write",
            "/api/files/delete",
            "/api/files/complete-upload",
            "/api/files/exists",
            "/api/files/stat",
            "/api/files/list",
            "/api/files/signed-url",
        }
        if path in file_routes:
            allowed = {"location"}
            if path == "/api/files/signed-url":
                allowed.add("method")
            for selector in allowed:
                if selector in file_body_selectors.get("selectors", {}):
                    selectors[selector] = file_body_selectors["selectors"][selector]
                elif selector in file_body_selectors.get("dynamic_selectors", ()):
                    if selector not in dynamic_selectors:
                        dynamic_selectors.append(selector)
                    selector_reasons[selector] = file_body_selectors[
                        "selector_reasons"
                    ][selector]
        return {
            **event,
            "resource_call": {
                "selectors": selectors,
                "dynamic_selectors": dynamic_selectors,
                "selector_reasons": selector_reasons,
            },
        }

    async def walk_return_method(
        definition_source: SourceFile,
        return_shape: str,
        method: str,
        source_file: SourceFile,
        line: int,
        via: list[str],
        function_name: str,
        callable_context: dict[str, str] | None = None,
        return_imported_instance: dict[str, Any] | None = None,
    ) -> None:
        if return_shape == "json":
            return
        if return_shape.startswith("local_instance:"):
            class_name = return_shape.removeprefix("local_instance:")
            next_method, separator, continuation = method.partition(".")
            await walk_class_method(
                definition_source,
                class_name,
                "__init__",
                via + [_label(definition_source, f"{class_name}.__init__")],
                callable_context,
            )
            await walk_class_method(
                definition_source,
                class_name,
                next_method,
                via + [_label(definition_source, f"{class_name}.{next_method}")],
                callable_context,
            )
            if separator:
                summary = await load(definition_source)
                property_method = summary.get("classes", {}).get(class_name, {}).get(
                    "methods", {}
                ).get(next_method)
                if property_method is not None:
                    await walk_return_method(
                        definition_source,
                        property_method.get("return_shape", "unknown"),
                        continuation,
                        source_file,
                        line,
                        via,
                        f"{class_name}.{next_method}",
                    )
            return
        if return_imported_instance is not None:
            await resolve_binding(
                definition_source,
                return_imported_instance["module"],
                return_imported_instance["level"],
                return_imported_instance["symbol"],
                method,
                via,
                line,
                constructor_context=return_imported_instance.get(
                    "constructor_context", {}
                ),
            )
            return
        if return_shape.startswith("sdk:"):
            add_sdk_call(
                source_file,
                {
                    "line": line,
                    "call": f"{return_shape.removeprefix('sdk:')}.{method}",
                },
                via,
            )
            return
        # The helper body has already been scanned. An ordinary Python return
        # value is not an additional SDK operation unless it carries a known
        # SDK or local-instance binding.

    async def resolve_binding(
        source_file: SourceFile,
        module: str,
        level: int,
        symbol: str | None,
        member: str | None,
        via: list[str],
        line: int,
        argument_kinds: tuple[str, ...] = (),
        optional: bool = False,
        keyword_kinds: tuple[tuple[str, str], ...] = (),
        constructor_context: dict[str, Any] | None = None,
    ) -> None:
        constructor_context = constructor_context or {
            name: value
            for name, value in keyword_kinds
            if value.startswith("sdk:")
        }
        constructor_context = bind_callback_origins(source_file, constructor_context)
        if not consume_graph_step(source_file):
            return
        if _is_sdk_module(module):
            if symbol and member:
                call = _sdk_callable(f"{module}.{symbol}.{member}")
                if call:
                    add_sdk_call(source_file, {"line": line, "call": call}, via)
            return
        # Python initializes every concrete parent package before importing a
        # child, even when the parent is never called explicitly.
        parts = module.split(".")
        for end in range(1, len(parts)):
            if not consume_graph_step(source_file):
                return
            package = await resolve_import(source_file, ".".join(parts[:end]), level)
            if package is not None and package.path.endswith("/__init__.py"):
                await walk_module_init(package, via + [_label(package, _MODULE_INIT)])

        # A relative import executes its package initializer before the target
        # module.  Resolvers that do not model package files simply return None.
        if level:
            package = await resolve_import(source_file, "", level)
            if package is not None and package.key != source_file.key:
                await walk_module_init(package, via + [_label(package, _MODULE_INIT)])

        resolved = await resolve_import(source_file, module, level)
        if resolved is None:
            if optional or level == 0 and (
                _is_stdlib(module)
                or _is_installed_third_party(module)
                or module.split(".")[0] in external_module_roots
            ):
                return
            prefix = "." * level
            unknowns.add(
                f"{source_file.path}:{line}: unresolved user module: {prefix}{module}"
            )
            return
        await walk_module_init(resolved, via + [_label(resolved, _MODULE_INIT)])

        async def walk_imported_member(
            definition_source: SourceFile, imported_member: str
        ) -> None:
            function_name, separator, return_member = imported_member.partition(".")
            await walk_function(
                definition_source,
                function_name,
                via + [_label(definition_source, function_name)],
                argument_kinds,
                keyword_kinds,
            )
            if not separator:
                return
            summary = await load(definition_source)
            function = summary.get("functions", {}).get(function_name)
            if function is None:
                return
            await walk_return_method(
                definition_source,
                function.get("return_shape", "unknown"),
                return_member,
                source_file,
                line,
                via,
                function_name,
                function.get("return_callable_fields", {}),
                function.get("return_imported_instance"),
            )

        async def walk_child_member(
            child: SourceFile, imported_member: str
        ) -> None:
            child_summary = await load(child)
            child_symbol, separator, child_member = imported_member.partition(".")
            if child_symbol not in child_summary.get("classes", {}):
                await walk_imported_member(child, imported_member)
                return
            await walk_class_method(
                child,
                child_symbol,
                "__init__",
                via + [_label(child, f"{child_symbol}.__init__")],
                constructor_context,
            )
            if separator:
                await walk_class_method(
                    child,
                    child_symbol,
                    child_member,
                    via + [_label(child, f"{child_symbol}.{child_member}")],
                    constructor_context,
                )

        if symbol is None and member:
            await walk_imported_member(resolved, member)
            return
        if symbol:
            if resolved.kind == "namespace":
                child = await resolve_import(resolved, symbol, 1)
                if child is None:
                    unknowns.add(
                        f"{resolved.path}:{line}: unresolved user module: {symbol}"
                    )
                    return
                await walk_module_init(child, via + [_label(child, _MODULE_INIT)])
                if member:
                    await walk_child_member(child, member)
                return
            summary = await load(resolved)
            if symbol in summary.get("classes", {}):
                class_summary = summary["classes"][symbol]
                positional_context = constructor_context.pop("__positional__", ())
                if isinstance(positional_context, list):
                    for field_name, value in zip(
                        class_summary.get("constructor_fields", ()), positional_context
                    ):
                        constructor_context.setdefault(field_name, value)
                await walk_class_method(
                    resolved,
                    symbol,
                    "__init__",
                    via + [_label(resolved, f"{symbol}.__init__")],
                    constructor_context,
                )
                if member:
                    await walk_class_method(
                        resolved,
                        symbol,
                        member,
                        via + [_label(resolved, f"{symbol}.{member}")],
                        constructor_context,
                    )
                return
            export_binding = summary.get("bindings", {}).get(symbol, {})
            if (
                resolved.path.endswith("/__init__.py")
                and symbol not in summary.get("functions", {})
                and symbol not in summary.get("classes", {})
                and (
                    not export_binding
                    or export_binding.get("module") == ""
                    and export_binding.get("level") == 1
                    and export_binding.get("symbol") == symbol
                )
                and symbol not in summary.get("facade_router", {}).get("exports", {})
            ):
                # Python's from-package import also resolves a concrete child
                # module when the package has no explicit export of that name.
                child = await resolve_import(resolved, symbol, 1)
                if child is not None and child.key != resolved.key:
                    await walk_module_init(child, via + [_label(child, _MODULE_INIT)])
                    if member:
                        await walk_child_member(child, member)
                    return
            if member:
                function = summary.get("functions", {}).get(symbol)
                if function is not None:
                    await walk_function(
                        resolved,
                        symbol,
                        via + [_label(resolved, symbol)],
                        argument_kinds,
                        keyword_kinds,
                    )
                    await walk_return_method(
                        resolved,
                        function.get("return_shape", "unknown"),
                        member,
                        source_file,
                        line,
                        via,
                        symbol,
                        function.get("return_callable_fields", {}),
                        function.get("return_imported_instance"),
                    )
                    return
                if symbol in summary.get("data_exports", {}):
                    # The module initializer has already been traversed above.
                    # A literal export is data, never a local class dispatcher.
                    return
                exported = summary.get("bindings", {}).get(symbol)
                if exported:
                    if exported.get("local_instance"):
                        class_name = exported["local_instance"]
                        await walk_class_method(
                            resolved,
                            class_name,
                            "__init__",
                            via + [_label(resolved, f"{class_name}.__init__")],
                            exported.get("callable_context", {}),
                        )
                        await walk_class_method(
                            resolved,
                            class_name,
                            member,
                            via + [_label(resolved, f"{class_name}.{member}")],
                            exported.get("callable_context", {}),
                        )
                        return
                    if exported.get("local_function"):
                        await walk_function(
                            resolved,
                            exported["local_function"],
                            via + [_label(resolved, exported["local_function"])],
                            argument_kinds,
                            keyword_kinds,
                        )
                        return
                    await resolve_binding(
                        resolved,
                        exported["module"],
                        exported["level"],
                        exported.get("symbol"),
                        member,
                        via,
                        line,
                        argument_kinds,
                        optional,
                        keyword_kinds,
                    )
                    return
                await walk_class_method(
                    resolved,
                    symbol,
                    member,
                    via + [_label(resolved, f"{symbol}.{member}")],
                )
            else:
                await walk_function(
                    resolved,
                    symbol,
                    via + [_label(resolved, symbol)],
                    argument_kinds,
                    keyword_kinds,
                )

    async def entrypoint_exists(
        source_file: SourceFile, name: str, seen: set[tuple[str, str]]
    ) -> bool:
        marker = (source_file.key, name)
        if marker in seen:
            return False
        seen.add(marker)
        summary = await load(source_file)
        if name in summary.get("functions", {}):
            return True
        binding = summary.get("bindings", {}).get(name)
        if not binding or "module" not in binding or not binding.get("symbol"):
            return False
        resolved = await resolve_import(
            source_file, binding["module"], binding["level"]
        )
        if resolved is None:
            return False
        return await entrypoint_exists(resolved, binding["symbol"], seen)

    async def process_events(
        source_file: SourceFile,
        events: list[dict[str, Any]],
        via: list[str],
        parameter_context: dict[str, str] | None = None,
    ) -> None:
        parameter_context = parameter_context or {}
        for event in events:
            kind = event["kind"]
            if kind == "sdk":
                bound_event = bind_parameter_selectors(event, parameter_context)
                add_sdk_call(source_file, bound_event, via)
                add_resource_call(source_file, bound_event)
            elif kind == "local":
                argument_kinds = tuple(
                    parameter_context.get(argument.removeprefix("parameter:"), argument)
                    if argument.startswith("parameter:")
                    else argument
                    for argument in event.get("arguments", [])
                )
                keyword_kinds = tuple(
                    (
                        name,
                        parameter_context.get(value.removeprefix("parameter:"), value)
                        if value.startswith("parameter:")
                        else value,
                    )
                    for name, value in event.get("keyword_arguments", {}).items()
                )
                await walk_function(
                    source_file,
                    event["name"],
                    via + [_label(source_file, event["name"])],
                    argument_kinds,
                    keyword_kinds,
                )
            elif kind == "local_return_method":
                argument_kinds = tuple(
                    parameter_context.get(argument.removeprefix("parameter:"), argument)
                    if argument.startswith("parameter:")
                    else argument
                    for argument in event.get("arguments", [])
                )
                await walk_function(
                    source_file,
                    event["name"],
                    via + [_label(source_file, event["name"])],
                    argument_kinds,
                    tuple(
                        (
                            name,
                            parameter_context.get(value.removeprefix("parameter:"), value)
                            if value.startswith("parameter:")
                            else value,
                        )
                        for name, value in event.get("keyword_arguments", {}).items()
                    ),
                )
                summary = await load(source_file)
                function = summary.get("functions", {}).get(event["name"])
                if function is None:
                    add_unknown(
                        source_file,
                        {
                            "line": event["line"],
                            "reason": "unresolved helper return method",
                        },
                    )
                    continue
                await walk_return_method(
                    source_file,
                    function.get("return_shape", "unknown"),
                    event["method"],
                    source_file,
                    event["line"],
                    via,
                    event["name"],
                    function.get("return_callable_fields", {}),
                    function.get("return_imported_instance"),
                )
            elif kind == "local_instance_return_method":
                await walk_class_method(
                    source_file,
                    event["class"],
                    event["returned_method"],
                    via + [
                        _label(
                            source_file,
                            f"{event['class']}.{event['returned_method']}",
                        )
                    ],
                    event.get("callable_context", {}),
                )
                summary = await load(source_file)
                method = summary.get("classes", {}).get(event["class"], {}).get(
                    "methods", {}
                ).get(event["returned_method"])
                if method is not None:
                    await walk_return_method(
                        source_file,
                        method.get("return_shape", "unknown"),
                        event["method"],
                        source_file,
                        event["line"],
                        via,
                        f"{event['class']}.{event['returned_method']}",
                    )
            elif kind == "local_class_return_method":
                await walk_class_method(
                    source_file,
                    event["class"],
                    event["returned_method"],
                    via + [
                        _label(
                            source_file,
                            f"{event['class']}.{event['returned_method']}",
                        )
                    ],
                )
                summary = await load(source_file)
                method = summary.get("classes", {}).get(event["class"], {}).get(
                    "methods", {}
                ).get(event["returned_method"])
                if method is not None:
                    await walk_return_method(
                        source_file,
                        method.get("return_shape", "unknown"),
                        event["method"],
                        source_file,
                        event["line"],
                        via,
                        f"{event['class']}.{event['returned_method']}",
                    )
            elif kind == "local_dynamic_dispatch":
                await walk_function(
                    source_file,
                    event["name"],
                    via + [_label(source_file, event["name"])],
                )
                summary = await load(source_file)
                function = summary.get("functions", {}).get(event["name"])
                if function and function.get("return_shape", "unknown").startswith(
                    ("sdk:", "local_instance:")
                ):
                    add_unknown(
                        source_file,
                        {
                            "line": event["line"],
                            "reason": "dynamic SDK dispatch",
                        },
                    )
            elif kind == "local_class":
                await walk_class_method(
                    source_file,
                    event["name"],
                    "__init__",
                    via + [_label(source_file, f"{event['name']}.__init__")],
                    event.get("callable_context", {}),
                )
            elif kind == "local_instance_method":
                await walk_class_method(
                    source_file,
                    event["class"],
                    "__init__",
                    via + [_label(source_file, f"{event['class']}.__init__")],
                    event.get("callable_context", {}),
                )
                await walk_class_method(
                    source_file,
                    event["class"],
                    event["method"],
                    via + [_label(source_file, f"{event['class']}.{event['method']}")],
                    event.get("callable_context", {}),
                )
            elif kind == "bound_method":
                receiver_kind = parameter_context.get(event["receiver"], "unknown")
                if receiver_kind == "json":
                    continue
                if receiver_kind.startswith("sdk:"):
                    receiver = receiver_kind.removeprefix("sdk:")
                    call = f"{receiver}.{event['method']}"
                    field = _sdk_object_field_contract(receiver)
                    if field and field.kind in {"json_object", "json_list", "scalar"}:
                        continue
                    if sdk_object_is_data(receiver) and sdk_call_contract(call) is None:
                        warnings.add(
                            f"{source_file.path}:{event['line']}: unsupported SDK data-object method"
                        )
                        continue
                    add_sdk_call(
                        source_file,
                        {
                            "line": event["line"],
                            "call": call,
                        },
                        via,
                    )
                    resource_call = event.get("resource_calls", {}).get(call)
                    if resource_call is not None:
                        resource_event = bind_parameter_selectors(
                            {
                                "resource_call": resource_call,
                                "parameter_selectors": event.get(
                                    "resource_parameter_selectors", {}
                                ).get(call, {}),
                                "file_body_selectors": event.get(
                                    "resource_file_body_selectors", {}
                                ).get(call, {}),
                            },
                            parameter_context,
                        )
                        add_resource_call(
                            source_file,
                            {
                                "line": event["line"],
                                "call": call,
                                "resource_call": resource_event["resource_call"],
                            },
                        )
                    continue
            elif kind == "imported":
                await resolve_binding(
                    source_file,
                    event["module"],
                    event["level"],
                    event.get("symbol"),
                    event.get("member"),
                    via,
                    event["line"],
                    tuple(
                        parameter_context.get(
                            argument.removeprefix("parameter:"), argument
                        )
                        if argument.startswith("parameter:")
                        else argument
                        for argument in event.get("arguments", [])
                    ),
                    event.get("optional", False),
                    tuple(
                        (
                            name,
                            parameter_context.get(value.removeprefix("parameter:"), value)
                            if value.startswith("parameter:")
                            else value,
                        )
                        for name, value in event.get("keyword_arguments", {}).items()
                    ),
                    event.get("constructor_context", {}),
                )
            elif kind == "dynamic_import":
                await resolve_binding(
                    source_file,
                    event["module"],
                    event.get("level", 0),
                    None,
                    None,
                    via,
                    event["line"],
                )
            elif kind == "unknown":
                add_unknown(source_file, event)
            elif kind == "warning":
                warnings.add(f"{source_file.path}:{event['line']}: {event['reason']}")

    async def walk_module_init(source_file: SourceFile, via: list[str]) -> None:
        if source_file.key in initialized:
            return
        if not consume_graph_step(source_file):
            return
        initialized.add(source_file.key)
        summary = await load(source_file)
        parse_error = summary.get("parse_error")
        if parse_error:
            unknowns.add(f"{source_file.path}: {parse_error}")
            return
        await process_events(source_file, summary.get("top_level", []), via)
        # Imports do not appear as call events, but execute import-time code.
        seen_imports: set[tuple[str, int]] = set()
        for binding in summary.get("bindings", {}).values():
            if "module" not in binding:
                continue
            module = binding["module"]
            level = binding["level"]
            if (module, level) not in seen_imports:
                seen_imports.add((module, level))
                await resolve_binding(
                    source_file,
                    module,
                    level,
                    None,
                    None,
                    via,
                    1,
                    optional=binding.get("optional", False),
                )

    async def walk_lazy_facade(
        source_file: SourceFile,
        name: str,
        via: list[str],
        argument_kinds: tuple[str, ...],
        keyword_kinds: tuple[tuple[str, str], ...],
    ) -> bool:
        summary = await load(source_file)
        router = summary.get("lazy_facade_router", {})
        if not router:
            return False
        lazy_class = router["class"]
        factory = router["factory"]
        factory_summary = summary.get("classes", {}).get(lazy_class, {}).get(
            "methods", {}
        ).get(factory)
        if factory_summary is None:
            return False
        return_shape = factory_summary.get("return_shape", "unknown")
        if not return_shape.startswith("local_instance:") and router.get("client_factory"):
            client_factory = router["client_factory"]
            return_shape = (
                f"local_instance:{client_factory}"
                if client_factory in summary.get("classes", {})
                else summary.get("functions", {}).get(client_factory, {}).get(
                    "return_shape", "unknown"
                )
            )
        if not return_shape.startswith("local_instance:"):
            return False
        client_class = return_shape.removeprefix("local_instance:")
        client_methods = summary.get("classes", {}).get(client_class, {}).get(
            "methods", {}
        )
        if name not in client_methods:
            unknowns.add(
                f"{source_file.path}:1: unresolved user method: {client_class}.{name}"
            )
            return True
        await walk_class_method(
            source_file,
            lazy_class,
            router["wrapper"],
            via + [_label(source_file, f"{lazy_class}.{router['wrapper']}")],
        )
        await walk_class_method(
            source_file,
            lazy_class,
            factory,
            via + [_label(source_file, f"{lazy_class}.{factory}")],
        )
        await walk_class_method(
            source_file,
            client_class,
            name,
            via + [_label(source_file, f"{client_class}.{name}")],
        )
        return True

    async def walk_function(
        source_file: SourceFile,
        name: str,
        via: list[str],
        argument_kinds: tuple[str, ...] = (),
        keyword_kinds: tuple[tuple[str, str], ...] = (),
    ) -> None:
        marker = (source_file.key, name, argument_kinds + keyword_kinds)
        if marker in completed_functions or marker in active_functions:
            return
        if not consume_graph_step(source_file):
            return
        summary = await load(source_file)
        if summary.get("parse_error"):
            return
        function = summary.get("functions", {}).get(name)
        if function is None:
            if await walk_lazy_facade(
                source_file, name, via, argument_kinds, keyword_kinds
            ):
                completed_functions.add(marker)
                return
            router = summary.get("facade_router", {})
            exports = router.get("exports", {})
            map_binding = router.get("map_binding")
            if router and not exports and map_binding:
                metadata_source = await resolve_import(
                    source_file, map_binding["module"], map_binding["level"]
                )
                if metadata_source is not None:
                    await walk_module_init(metadata_source, via + [_label(metadata_source, _MODULE_INIT)])
                    metadata = await load(metadata_source)
                    exports = metadata.get("literal_module_maps", {}).get(map_binding["symbol"], {})
            child_module = exports.get(name)
            if child_module is not None:
                module_path = router["prefix"] + child_module
                if len(module_path) > MAX_SELECTOR_VALUE_CHARS:
                    unknowns.add(f"{source_file.path}:1: facade module path exceeds analysis bound")
                    return
                child = await resolve_import(source_file, module_path, router["level"])
                if child is None:
                    unknowns.add(f"{source_file.path}:1: unresolved user module: {module_path}")
                    return
                active_functions.add(marker)
                try:
                    await walk_module_init(child, via + [_label(child, _MODULE_INIT)])
                    wrapper = router["wrapper"]
                    await walk_function(source_file, wrapper, via + [_label(source_file, wrapper)])
                    await walk_function(child, name, via + [_label(child, name)], argument_kinds, keyword_kinds)
                finally:
                    active_functions.remove(marker)
                completed_functions.add(marker)
                return
            if name in summary.get("classes", {}):
                await walk_class_method(
                    source_file,
                    name,
                    "__init__",
                    via + [_label(source_file, f"{name}.__init__")],
                )
                completed_functions.add(marker)
                return
            exported = summary.get("bindings", {}).get(name)
            if exported:
                if exported.get("local_function"):
                    await walk_function(
                        source_file,
                        exported["local_function"],
                        via + [_label(source_file, exported["local_function"])],
                        argument_kinds,
                        keyword_kinds,
                    )
                    completed_functions.add(marker)
                    return
                conditional_targets = exported.get("conditional_targets", ())
                if conditional_targets:
                    for candidate in conditional_targets:
                        if candidate["kind"] == "local":
                            await walk_function(
                                source_file,
                                candidate["name"],
                                via + [_label(source_file, candidate["name"])],
                                argument_kinds,
                                keyword_kinds,
                            )
                        elif candidate["kind"] == "imported":
                            await resolve_binding(
                                source_file,
                                candidate["module"],
                                candidate["level"],
                                candidate.get("symbol"),
                                None,
                                via,
                                1,
                                argument_kinds,
                                candidate.get("optional", False),
                                keyword_kinds,
                            )
                    completed_functions.add(marker)
                    return
                if "module" not in exported:
                    unknowns.add(
                        f"{source_file.path}:1: unresolved user function: {name}"
                    )
                    completed_functions.add(marker)
                    return
                await resolve_binding(
                    source_file,
                    exported["module"],
                    exported["level"],
                    exported.get("symbol"),
                    None,
                    via,
                    1,
                    argument_kinds,
                    exported.get("optional", False),
                    keyword_kinds,
                )
                completed_functions.add(marker)
                return
            unknowns.add(f"{source_file.path}:1: unresolved user function: {name}")
            completed_functions.add(marker)
            return
        active_functions.add(marker)
        try:
            for binding in function.get("imports", []):
                await resolve_binding(
                    source_file,
                    binding["module"],
                    binding["level"],
                    None,
                    None,
                    via,
                    1,
                    optional=binding.get("optional", False),
                )
            parameters = function.get("parameters", ())
            parameter_context = {
                parameter: dict(keyword_kinds).get(
                    parameter,
                    argument_kinds[index] if index < len(argument_kinds) else "unknown",
                )
                for index, parameter in enumerate(parameters)
            }
            await process_events(
                source_file,
                function.get("events", []),
                via,
                parameter_context,
            )
        finally:
            active_functions.remove(marker)
        completed_functions.add(marker)

    async def walk_class_callback(
        source_file: SourceFile, callback: Any, line: int, via: list[str]
    ) -> None:
        if isinstance(callback, str):
            if callback.startswith("sdk:"):
                add_sdk_call(
                    source_file,
                    {"line": line, "call": callback.removeprefix("sdk:")},
                    via,
                )
            return
        if not isinstance(callback, dict):
            return
        origin = callback.get("source_key")
        callback_source = source_file if origin is None else source_descriptors[origin]
        if callback["kind"] == "sdk":
            add_sdk_call(
                source_file, {"line": line, "call": callback["call"]}, via
            )
        elif callback["kind"] == "local":
            await walk_function(
                callback_source,
                callback["name"],
                via + [_label(callback_source, callback["name"])],
            )
        elif callback["kind"] == "imported":
            await resolve_binding(
                callback_source,
                callback["module"],
                callback["level"],
                callback.get("symbol"),
                callback.get("member"),
                via,
                line,
                optional=callback.get("optional", False),
            )

    def field_callback_target(
        callback: Any, callable_context: dict[str, Any]
    ) -> Any:
        if not isinstance(callback, dict):
            return callback
        if callback.get("kind") == "parameter":
            return callable_context.get(callback["name"])
        if callback.get("kind") == "parameter_or":
            return callable_context.get(callback["name"], callback.get("fallback"))
        return callback

    async def walk_class_field(
        source_file: SourceFile,
        class_summary: dict[str, Any],
        field_path: str,
        via: list[str],
        line: int,
        callable_context: dict[str, Any],
    ) -> bool:
        field_name, separator, continuation = field_path.partition(".")
        field_context = callable_context.get(field_name)
        sdk_target = (
            field_context.removeprefix("sdk:")
            if isinstance(field_context, str) and field_context.startswith("sdk:")
            else field_context.get("target")
            if isinstance(field_context, dict)
            and field_context.get("kind") == "sdk_namespace"
            else None
        )
        if sdk_target is not None:
            if not separator:
                return True
            target = f"{sdk_target}.{continuation}"
            call = (
                target
                if sdk_call_contract(target) is not None
                else _sdk_callable(target)
            )
            if call is not None:
                add_sdk_call(source_file, {"line": line, "call": call}, via)
            else:
                add_unknown(
                    source_file,
                    {"line": line, "reason": "unrecognized SDK call"},
                )
            return True
        callbacks = class_summary.get("field_callback_targets", {}).get(
            field_name, []
        )
        if callbacks and not separator:
            for callback in callbacks:
                await walk_class_callback(
                    source_file,
                    field_callback_target(callback, callable_context),
                    line,
                    via,
                )
            return True
        if field_name in class_summary.get("callable_fields", ()) and not separator:
            callback = callable_context.get(field_name)
            if callback is not None:
                await walk_class_callback(source_file, callback, line, via)
                return True
        field_kind = class_summary.get("data_fields", {}).get(field_name)
        if field_kind is not None:
            return not separator or continuation in _DATA_METHODS.get(field_kind, ())
        if field_name in class_summary.get("field_shapes", {}):
            if separator:
                await walk_class_method(
                    source_file,
                    class_summary["field_shapes"][field_name],
                    continuation,
                    via + [_label(source_file, field_path)],
                    callable_context,
                )
            return True
        if field_name in class_summary.get("field_constructors", {}):
            if separator:
                field = class_summary["field_constructors"][field_name]
                await resolve_binding(
                    source_file,
                    field["module"],
                    field["level"],
                    field.get("symbol"),
                    _continued_member(field, continuation),
                    via,
                    line,
                    optional=field.get("optional", False),
                )
            return True
        if field_name in class_summary.get("field_imports", {}):
            if separator:
                field = class_summary["field_imports"][field_name]
                await resolve_binding(
                    source_file,
                    field["module"],
                    field["level"],
                    field.get("symbol"),
                    _continued_member(field, continuation),
                    via,
                    line,
                    optional=field.get("optional", False),
                )
            return True
        return False

    async def walk_class_method(
        source_file: SourceFile,
        class_name: str,
        method_name: str,
        via: list[str],
        callable_context: dict[str, Any] | None = None,
    ) -> None:
        callable_context = bind_callback_origins(source_file, callable_context or {})
        marker = (
            source_file.key,
            f"{class_name}.{method_name}",
            json.dumps(callable_context, sort_keys=True),
        )
        if marker in completed_functions or marker in active_functions:
            return
        if not consume_graph_step(source_file):
            return
        summary = await load(source_file)
        class_summary = summary.get("classes", {}).get(class_name)
        if class_summary is None:
            unknowns.add(
                f"{source_file.path}:1: unresolved user class: {class_name}"
            )
            completed_functions.add(marker)
            return
        direct_method, separator, continuation = method_name.partition(".")
        if separator:
            continued_method = class_summary.get("methods", {}).get(direct_method)
            if continued_method is not None:
                await walk_class_method(
                    source_file,
                    class_name,
                    direct_method,
                    via + [_label(source_file, f"{class_name}.{direct_method}")],
                    callable_context,
                )
                await walk_return_method(
                    source_file,
                    continued_method.get("return_shape", "unknown"),
                    continuation,
                    source_file,
                    1,
                    via,
                    f"{class_name}.{direct_method}",
                )
                completed_functions.add(marker)
                return
        method = class_summary.get("methods", {}).get(method_name)
        if method is not None:
            active_functions.add(marker)
            try:
                for binding in method.get("imports", []):
                    await resolve_binding(
                        source_file,
                        binding["module"],
                        binding["level"],
                        None,
                        None,
                        via,
                        1,
                        optional=binding.get("optional", False),
                    )
                for event in method.get("events", []):
                    if event["kind"] == "self_method":
                        callbacks = class_summary.get("field_callback_targets", {}).get(
                            event["name"], []
                        )
                        if not callbacks and event["name"] in class_summary.get(
                            "callable_fields", ()
                        ):
                            callback = callable_context.get(event["name"])
                            callbacks = [callback] if callback is not None else []
                        if callbacks:
                            for callback in callbacks:
                                await walk_class_callback(
                                    source_file,
                                    field_callback_target(
                                        callback, callable_context
                                    ),
                                    event["line"],
                                    via,
                                )
                        else:
                            await walk_class_method(
                                source_file,
                                class_name,
                                event["name"],
                                via + [_label(source_file, f"{class_name}.{event['name']}")],
                                callable_context,
                            )
                    elif event["kind"] == "self_field_method":
                        await walk_class_field(
                            source_file,
                            class_summary,
                            event["name"],
                            via,
                            event["line"],
                            callable_context,
                        )
                await process_events(source_file, method.get("events", []), via)
            finally:
                active_functions.remove(marker)
            completed_functions.add(marker)
            return
        if await walk_class_field(
            source_file,
            class_summary,
            method_name,
            via,
            1,
            callable_context,
        ):
            completed_functions.add(marker)
            return
        field_name, separator, field_method = method_name.partition(".")
        field_kind = class_summary.get("data_fields", {}).get(field_name)
        if (
            separator
            and field_kind is not None
            and field_method in _DATA_METHODS.get(field_kind, ())
        ):
            completed_functions.add(marker)
            return
        if method_name in class_summary.get("data_methods", ()):
            completed_functions.add(marker)
            return
        for base in class_summary.get("bases", []):
            if base["kind"] == "local":
                await walk_class_method(
                    source_file, base["name"], method_name, via, callable_context
                )
            else:
                await resolve_binding(
                    source_file,
                    base["module"],
                    base["level"],
                    base.get("symbol"),
                    _continued_member(base, method_name),
                    via,
                    1,
                )
        if method_name != "__init__" and not class_summary.get("bases"):
            unknowns.add(
                f"{source_file.path}:1: unresolved user method: {class_name}.{method_name}"
            )
        completed_functions.add(marker)

    try:
        await walk_module_init(entry, [_label(entry, _MODULE_INIT)])
        entry_summary = await load(entry)
        entrypoint_found = await entrypoint_exists(entry, function_name, set())
        entry_function = entry_summary.get("functions", {}).get(function_name, {})
        context_parameter = entry_function.get("context_parameter")
        uncertain_context_parameter = entry_function.get("uncertain_context_parameter")
        await walk_function(
            entry,
            function_name,
            [_label(entry, function_name)],
            tuple(
                "sdk:bifrost.ExecutionContext"
                if parameter == context_parameter
                else "unknown"
                if parameter == uncertain_context_parameter
                else "json"
                for parameter in entry_function.get("parameters", ())
            ),
        )
    except RecursionError:
        unknowns.add(
            f"{entry.path}: Python recursion depth reached before graph traversal limit "
            f"({MAX_GRAPH_STEPS})"
        )
        entrypoint_found = False

    unique_evidence = {
        (item["path"], item["line"], item["call"], tuple(item["via"])): item
        for item in evidence
    }
    unique_resource_calls = {
        (
            item["call"],
            item["source_key"],
            item["path"],
            item["line"],
            tuple(sorted(item["selectors"].items())),
            tuple(item["dynamic_selectors"]),
            tuple(sorted(item["selector_reasons"].items())),
        ): item
        for item in resource_calls
    }
    return {
        "sdk_calls": sorted(sdk_calls),
        "evidence": [unique_evidence[key] for key in sorted(unique_evidence)],
        "resource_calls": [
            unique_resource_calls[key] for key in sorted(unique_resource_calls)
        ],
        "unknowns": sorted(unknowns),
        "warnings": sorted(warnings),
        "entrypoint_found": entrypoint_found,
        "dependencies": {key: dependencies[key] for key in sorted(dependencies)},
    }
