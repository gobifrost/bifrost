"""Conservative static analysis for workflow least-privilege conversion.

The converter must inspect workspace source without importing it: workflow code
has import-time effects and may not be trusted.  ``summarize_source`` therefore
turns one file into a bounded JSON call graph.  ``analyze_workflow`` follows that
graph through a caller-supplied repository resolver, retaining only the source
hashes that influenced its result.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from functools import lru_cache
from importlib.metadata import packages_distributions
from typing import Any, Awaitable, Callable

from shared.workflow_conversion_sdk import sdk_call_contract, sdk_object_contract

ANALYZER_VERSION = "8"
MAX_SOURCE_BYTES = 2_000_000
MAX_AST_NODES = 256_000
MAX_EVENTS_PER_SCOPE = 2_048
MAX_DEPENDENCIES = 256
MAX_GRAPH_STEPS = 10_000
MAX_SDK_CALLS = 2_048
MAX_EVIDENCE = 4_096
MAX_SELECTOR_VALUE_CHARS = 2_000
_MODULE_INIT = "<import>"
_BUILTINS = {
    "abs",
    "all",
    "any",
    "bool",
    "bytes",
    "dict",
    "enumerate",
    "filter",
    "float",
    "frozenset",
    "int",
    "isinstance",
    "len",
    "list",
    "map",
    "max",
    "min",
    "next",
    "object",
    "open",
    "range",
    "reversed",
    "set",
    "sorted",
    "str",
    "sum",
    "super",
    "tuple",
    "type",
    "zip",
}
_THIRD_PARTY_ROOTS = {
    "aiohttp",
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
_RAW_HTTP_ROOTS = {"aiohttp", "httpx", "requests", "urllib"}
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
        {"capitalize", "casefold", "endswith", "find", "format", "index", "join", "lower", "lstrip", "replace", "rstrip", "split", "startswith", "strip", "title", "upper"}
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


_SAFE_BIFROST_DECORATORS = {"bifrost.service", "bifrost.workflow"}


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
        if binding and _is_sdk_module(binding["module"]):
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
    """Recognize a method on a declared JSON/scalar SDK object field."""
    if not target or "." not in target:
        return False
    field_target, _, _ = target.rpartition(".")
    field = _sdk_object_field_contract(field_target)
    return field is not None and field.kind in {"json_object", "json_list"}


def _binding_for_expression(
    node: ast.AST, bindings: dict[str, dict[str, Any]], handles: dict[str, str]
) -> dict[str, Any] | None:
    if isinstance(node, ast.Call):
        return _binding_for_expression(node.func, bindings, handles)
    if isinstance(node, ast.Name):
        return bindings.get(node.id)
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
                return {**binding, "member": ".".join(attributes)}
    if isinstance(current, ast.Name):
        binding = bindings.get(current.id)
        if binding and binding.get("symbol"):
            attributes.reverse()
            if attributes:
                return {**binding, "member": ".".join(attributes)}
        if binding and not binding.get("symbol"):
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
        self.bindings = {name: dict(binding) for name, binding in bindings.items()}
        self.local_functions = local_functions
        self.local_classes = set(local_classes or ())
        self.handles: dict[str, str] = {}
        self.local_aliases: dict[str, str] = {}
        self.conditional_local_returns: dict[str, tuple[str, ...]] = {}
        self.value_kinds = dict(value_kinds or {})
        self.parameter_names = set(parameter_names or ())
        self.selector_constants: dict[str, str] = {}
        self.data_assignments: set[str] = set()
        self.imports: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.truncated = False

    def _argument_kind(self, node: ast.AST) -> str:
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
        return f"sdk:{target}" if target and target.startswith("bifrost.") else "unknown"

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
        return event

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
        for assignment in node.targets:
            if not isinstance(assignment, ast.Name):
                continue
            self.handles.pop(assignment.id, None)
            self.local_aliases.pop(assignment.id, None)
            self.conditional_local_returns.pop(assignment.id, None)
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
                self.bindings[assignment.id] = dict(binding)
            elif local_name and local_name in (
                self.local_functions | set(self.local_aliases)
            ):
                self.local_aliases[assignment.id] = self.local_aliases.get(
                    local_name, local_name
                )
            elif kind := _literal_kind(node.value, self.value_kinds):
                self.value_kinds[assignment.id] = kind
            if _assignment_signature(node.value)[0] == "data_assignment":
                self.data_assignments.add(assignment.id)
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                self.selector_constants[assignment.id] = node.value.value

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
                self.bindings[node.target.id] = dict(binding)
            elif local_name and local_name in (
                self.local_functions | set(self.local_aliases)
            ):
                self.local_aliases[node.target.id] = self.local_aliases.get(
                    local_name, local_name
                )
            elif kind := _literal_kind(node.value, self.value_kinds):
                self.value_kinds[node.target.id] = kind
            if _assignment_signature(node.value)[0] == "data_assignment":
                self.data_assignments.add(node.target.id)
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                self.selector_constants[node.target.id] = node.value.value

    def visit_AugAssign(self, node: ast.AugAssign) -> None:  # noqa: N802
        self.visit(node.value)
        if isinstance(node.target, ast.Name):
            self.handles.pop(node.target.id, None)
            self.local_aliases.pop(node.target.id, None)
            self.conditional_local_returns.pop(node.target.id, None)
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
            self._emit(
                {
                    "kind": "unknown",
                    "line": node.lineno,
                    "reason": f"conditional binding for {name}",
                }
            )
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
            self.bindings.pop(name, None)
            self.selector_constants.pop(name, None)
            self.data_assignments.discard(name)

    def visit_Try(self, node: ast.Try | ast.TryStar) -> None:  # noqa: N802
        # Exception paths can leave a different callable/import binding alive.
        # Until those paths have a merged binding graph, retain uncertainty
        # instead of treating the final textual handler as the only outcome.
        if any(
            isinstance(child, (ast.Import, ast.ImportFrom))
            or isinstance(child, (ast.Assign, ast.AnnAssign))
            and child.value is not None
            and _literal_kind(child.value, self.value_kinds) is None
            and isinstance(child.value, (ast.Name, ast.Attribute, ast.IfExp))
            for child in ast.walk(node)
        ):
            self._emit(
                {
                    "kind": "unknown",
                    "line": node.lineno,
                    "reason": "conditional binding in try/except",
                }
            )
        self.generic_visit(node)

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
        callback = _higher_order_callback(node, target)
        if callback is not None:
            self._emit(
                {
                    "kind": "unknown",
                    "line": node.lineno,
                    "reason": f"higher-order callback: {callback[0]}",
                }
            )
        if (
            isinstance(node.func, ast.Call)
            and isinstance(node.func.func, ast.Name)
            and node.func.func.id == "getattr"
            and static_getattr is None
        ):
            self._emit(
                {
                    "kind": "unknown",
                    "line": node.lineno,
                    "reason": "dynamic callable target",
                }
            )
        call_target = static_getattr or target
        sdk_data_field_method = _sdk_data_field_method(call_target)
        sdk = (
            None
            if sdk_data_field_method
            else _sdk_call(_sdk_field_method_call(call_target) or call_target)
        )
        if sdk:
            if sdk_call_contract(sdk) is None:
                self._emit(
                    {
                        "kind": "unknown",
                        "line": node.lineno,
                        "reason": "unrecognized SDK wrapper",
                    }
                )
            else:
                self._emit(self._sdk_event(node, sdk))
        elif target in _SAFE_BIFROST_DECORATORS:
            pass
        elif target in {"eval", "exec"}:
            self._emit({"kind": "unknown", "line": node.lineno, "reason": target})
        elif sdk_data_field_method or _is_safe_data_method(
            node.func, self.value_kinds
        ):
            pass
        elif target == "getattr":
            static_target = _static_getattr_target(node, self.bindings, self.handles)
            if _is_context_data_field(static_target) or _is_known_sdk_call(static_target):
                pass
            elif static_target and static_target.startswith("bifrost.context."):
                self._emit(
                    {
                        "kind": "unknown",
                        "line": node.lineno,
                        "reason": "unrecognized SDK wrapper",
                    }
                )
            elif static_target and static_target.startswith("bifrost."):
                pass
            else:
                subject = (
                    _dotted(node.args[0], self.bindings, self.handles)
                    if node.args
                    else None
                )
                if subject and subject.startswith("bifrost."):
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
                self._emit(
                    {"kind": "unknown", "line": node.lineno, "reason": "dynamic import"}
                )
        else:
            if binding:
                module = binding["module"]
                if module.split(".")[0] in _RAW_HTTP_ROOTS:
                    self._emit(
                        {"kind": "unknown", "line": node.lineno, "reason": "raw HTTP"}
                    )
                elif _is_sdk_module(module):
                    # ``_sdk_call`` above handles public SDK call shapes.  This
                    # is intentionally conservative for wrappers we do not know.
                    self._emit(
                        {
                            "kind": "unknown",
                            "line": node.lineno,
                            "reason": "unrecognized SDK wrapper",
                        }
                    )
                else:
                    self._emit(
                        {
                            "kind": "imported",
                            "line": node.lineno,
                            "module": module,
                            "level": binding["level"],
                            "symbol": binding.get("symbol"),
                            "member": binding.get("member"),
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
                    }
                )
            elif isinstance(node.func, ast.Name) and node.func.id in self.local_classes:
                self._emit(
                    {"kind": "local_class", "line": node.lineno, "name": node.func.id}
                )
            elif isinstance(node.func, ast.Attribute) and isinstance(
                node.func.value, ast.Name
            ):
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
                self._emit(
                    {
                        "kind": "bound_method",
                        "line": node.lineno,
                        "receiver": node.func.value.id,
                        "method": node.func.attr,
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
            elif target and target.split(".")[0] in _RAW_HTTP_ROOTS:
                self._emit(
                    {"kind": "unknown", "line": node.lineno, "reason": "raw HTTP"}
                )
            elif target and target not in _BUILTINS:
                self._emit(
                    {
                        "kind": "unknown",
                        "line": node.lineno,
                        "reason": "unrecognized wrapper",
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
            binding["module"] == "__future__"
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
) -> str:
    """Summarize a direct return value without retaining user data."""
    collector = _DirectReturns()
    for statement in node.body:
        collector.visit(statement)
    shapes: set[str] = set()
    for value in collector.values:
        if value is None:
            return "unknown"
        if _literal_kind(value, {}) is not None:
            shapes.add("json")
            continue
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id in class_names
        ):
            shapes.add(f"local_instance:{value.func.id}")
            continue
        target = _dotted(value, bindings, {})
        if target and target.startswith("bifrost."):
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
    function_summaries: dict[str, dict[str, Any]] = {}
    for name, node in functions.items():
        events, function_bindings, function_imports = _scope_summary(
            node.body,
            bindings,
            names,
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
            "return_shape": _return_shape(node, bindings, set(class_nodes)),
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
                set(methods),
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
                "return_shape": _return_shape(method, bindings, set(class_nodes)),
            }
        bases: list[dict[str, Any]] = []
        for base in class_node.bases:
            binding = _binding_for_expression(base, bindings, {})
            if binding:
                bases.append({"kind": "import", **binding})
            elif isinstance(base, ast.Name):
                bases.append({"kind": "local", "name": base.id})
        class_summaries[class_node.name] = {
            "methods": method_summaries,
            "bases": bases,
        }

    return {
        "version": ANALYZER_VERSION,
        "functions": function_summaries,
        "classes": class_summaries,
        "bindings": bindings,
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
) -> dict[str, Any]:
    """Analyze one workflow entry point without evaluating any user source."""
    summaries: dict[str, dict[str, Any]] = {}
    dependencies: dict[str, str] = {}
    initialized: set[str] = set()
    active_functions: set[tuple[str, str, tuple[str, ...]]] = set()
    completed_functions: set[tuple[str, str, tuple[str, ...]]] = set()
    sdk_calls: set[str] = set()
    evidence: list[dict[str, Any]] = []
    resource_calls: list[dict[str, Any]] = []
    unknowns: set[str] = set()
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
        if source_file.key not in summaries:
            summaries[source_file.key] = await summarize(source_file)
        return summaries[source_file.key]

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

    async def walk_return_method(
        definition_source: SourceFile,
        return_shape: str,
        method: str,
        source_file: SourceFile,
        line: int,
        via: list[str],
        function_name: str,
    ) -> None:
        if return_shape == "json":
            return
        if return_shape.startswith("local_instance:"):
            class_name = return_shape.removeprefix("local_instance:")
            await walk_class_method(
                definition_source,
                class_name,
                "__init__",
                via + [_label(definition_source, f"{class_name}.__init__")],
            )
            await walk_class_method(
                definition_source,
                class_name,
                method,
                via + [_label(definition_source, f"{class_name}.{method}")],
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
        unknowns.add(
            f"{source_file.path}:{line}: unresolved helper return method: "
            f"{function_name}.{method}"
        )

    async def resolve_binding(
        source_file: SourceFile,
        module: str,
        level: int,
        symbol: str | None,
        member: str | None,
        via: list[str],
        line: int,
    ) -> None:
        if not consume_graph_step(source_file):
            return
        if _is_sdk_module(module):
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
            if level == 0 and _is_stdlib(module):
                return
            if level == 0 and _is_installed_third_party(module):
                unknowns.add(
                    f"{source_file.path}:{line}: unresolved external dependency: {module}"
                )
                return
            else:
                prefix = "." * level
                unknowns.add(
                    f"{source_file.path}:{line}: unresolved user module: {prefix}{module}"
                )
            return
        await walk_module_init(resolved, via + [_label(resolved, _MODULE_INIT)])
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
                    await walk_function(
                        child, member, via + [_label(child, member)]
                    )
                return
            if member:
                summary = await load(resolved)
                function = summary.get("functions", {}).get(symbol)
                if function is not None:
                    await walk_function(
                        resolved, symbol, via + [_label(resolved, symbol)]
                    )
                    await walk_return_method(
                        resolved,
                        function.get("return_shape", "unknown"),
                        member,
                        source_file,
                        line,
                        via,
                        symbol,
                    )
                    return
                exported = summary.get("bindings", {}).get(symbol)
                if exported:
                    await resolve_binding(
                        resolved,
                        exported["module"],
                        exported["level"],
                        exported.get("symbol"),
                        member,
                        via,
                        line,
                    )
                    return
                await walk_class_method(
                    resolved,
                    symbol,
                    member,
                    via + [_label(resolved, f"{symbol}.{member}")],
                )
            else:
                await walk_function(resolved, symbol, via + [_label(resolved, symbol)])

    async def process_events(
        source_file: SourceFile,
        events: list[dict[str, Any]],
        via: list[str],
        parameter_context: dict[str, str] | None = None,
    ) -> None:
        nonlocal resource_calls_limited
        parameter_context = parameter_context or {}
        for event in events:
            kind = event["kind"]
            if kind == "sdk":
                add_sdk_call(source_file, event, via)
                resource_call = event.get("resource_call")
                if resource_call is not None:
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
            elif kind == "local":
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
                )
            elif kind == "local_class":
                await walk_class_method(
                    source_file,
                    event["name"],
                    "__init__",
                    via + [_label(source_file, f"{event['name']}.__init__")],
                )
            elif kind == "bound_method":
                receiver_kind = parameter_context.get(event["receiver"], "unknown")
                if receiver_kind == "json":
                    continue
                if receiver_kind.startswith("sdk:"):
                    add_sdk_call(
                        source_file,
                        {
                            "line": event["line"],
                            "call": f"{receiver_kind.removeprefix('sdk:')}.{event['method']}",
                        },
                        via,
                    )
                    continue
                add_unknown(
                    source_file,
                    {
                        "line": event["line"],
                        "reason": "unrecognized wrapper",
                    },
                )
            elif kind == "imported":
                await resolve_binding(
                    source_file,
                    event["module"],
                    event["level"],
                    event.get("symbol"),
                    event.get("member"),
                    via,
                    event["line"],
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
            module = binding["module"]
            level = binding["level"]
            if (module, level) not in seen_imports:
                seen_imports.add((module, level))
                await resolve_binding(source_file, module, level, None, None, via, 1)

    async def walk_function(
        source_file: SourceFile,
        name: str,
        via: list[str],
        argument_kinds: tuple[str, ...] = (),
    ) -> None:
        marker = (source_file.key, name, argument_kinds)
        if marker in completed_functions or marker in active_functions:
            return
        if not consume_graph_step(source_file):
            return
        summary = await load(source_file)
        if summary.get("parse_error"):
            return
        function = summary.get("functions", {}).get(name)
        if function is None:
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
                await resolve_binding(
                    source_file,
                    exported["module"],
                    exported["level"],
                    exported.get("symbol"),
                    None,
                    via,
                    1,
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
                )
            parameters = function.get("parameters", ())
            parameter_context = {
                parameter: argument_kinds[index]
                if index < len(argument_kinds)
                else "unknown"
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

    async def walk_class_method(
        source_file: SourceFile, class_name: str, method_name: str, via: list[str]
    ) -> None:
        marker = (source_file.key, f"{class_name}.{method_name}", ())
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
                    )
                await process_events(source_file, method.get("events", []), via)
            finally:
                active_functions.remove(marker)
            completed_functions.add(marker)
            return
        for base in class_summary.get("bases", []):
            if base["kind"] == "local":
                await walk_class_method(source_file, base["name"], method_name, via)
            else:
                await resolve_binding(
                    source_file,
                    base["module"],
                    base["level"],
                    base.get("symbol"),
                    method_name,
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
        "dependencies": {key: dependencies[key] for key in sorted(dependencies)},
    }
