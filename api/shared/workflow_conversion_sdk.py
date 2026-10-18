"""Derive workflow permission candidates from shipped SDK transport metadata."""

from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

from src.models.contracts.access_list import AccessClass, AccessEntry
from src.models.contracts.permissions import parse_permission
from src.services.access_list import ACCESS_LIST


_SDK_MODULES = (
    "_context", "_execution_context", "_runtime_io",
    "agents", "ai", "api", "artifacts", "config", "events", "executions",
    "files", "forms", "integrations", "knowledge", "models", "organizations",
    "roles", "tables", "users", "webhooks", "workflows",
)
_TRANSPORTS = {"engine_request", "engine_request_sync", "engine_stream", "request"}
_GENERIC_HTTP_METHODS = {"get", "post", "put", "patch", "delete"}
_PARAMETER = re.compile(r"\{[^}:]+(?::[^}]+)?\}")
# The worker proxies these source-loader routes during imports. Their access-list
# permission protects human diagnostics; execution children instead present the
# signed workflow source scope enforced by the canonical SDK-module router.
_SOURCE_TRANSPORT_PATHS = {
    "/api/sdk/modules-resolve",
    "/api/sdk/modules/{}",
}
_RESOURCE_SELECTOR_PARAMETERS = frozenset({
    "table", "path", "location", "scope", "solution", "organization_id", "org_id",
})


@dataclass(frozen=True)
class SdkRouteCandidate:
    """One canonical platform route reached by a static SDK call."""

    call: str
    method: str
    path: str
    access_class: AccessClass
    permission: str | None
    boundary: str | None


@dataclass(frozen=True)
class SdkNamedPermissionCandidate:
    """A named service guard reached by a shipped SDK transport."""

    call: str
    permission: str
    condition: Literal["unconditional", "conditional"]


SdkEffect = Literal["none", "scope_override", "runtime_log"]
SdkValueKind = Literal[
    "none", "scalar", "json_object", "json_list", "sdk_object", "sdk_credential", "unknown",
]


@dataclass(frozen=True)
class SdkFieldContract:
    """A statically declared field on a shipped SDK object."""

    name: str
    kind: SdkValueKind
    sdk_type: str | None = None
    element_kind: SdkValueKind | None = None
    element_sdk_type: str | None = None


@dataclass(frozen=True)
class SdkObjectContract:
    """Static shape of a shipped SDK object, without runtime values."""

    sdk_type: str
    fields: tuple[SdkFieldContract, ...]


@dataclass(frozen=True)
class SdkCallContract:
    """Static SDK call behavior and return shape derived from shipped source."""

    effect: SdkEffect
    return_kind: SdkValueKind
    sdk_type: str | None = None
    element_kind: SdkValueKind | None = None
    element_sdk_type: str | None = None
    parameters: tuple[str, ...] = ()
    resource_defaults: tuple[tuple[str, str | int | float | bool | None], ...] = ()


@dataclass(frozen=True)
class _Function:
    key: str
    module: str
    class_name: str | None
    node: ast.FunctionDef | ast.AsyncFunctionDef

    @property
    def parameters(self) -> tuple[str, ...]:
        parameters = tuple(
            argument.arg
            for argument in (
                *self.node.args.posonlyargs,
                *self.node.args.args,
                *self.node.args.kwonlyargs,
            )
        )
        if parameters and parameters[0] in {"self", "cls"}:
            return parameters[1:]
        return parameters

    @property
    def resource_defaults(self) -> tuple[tuple[str, str | int | float | bool | None], ...]:
        positional = (*self.node.args.posonlyargs, *self.node.args.args)
        defaults = self.node.args.defaults
        default_nodes = {
            argument.arg: default
            for argument, default in zip(positional[-len(defaults):], defaults)
        } if defaults else {}
        default_nodes.update({
            argument.arg: default
            for argument, default in zip(self.node.args.kwonlyargs, self.node.args.kw_defaults)
            if default is not None
        })
        selector_parameters = _RESOURCE_SELECTOR_PARAMETERS
        if (
            self.module == "files"
            and self.class_name == "files"
            and self.node.name == "get_signed_url"
        ):
            selector_parameters = selector_parameters | {"method"}
        values: list[tuple[str, str | int | float | bool | None]] = []
        for name in self.parameters:
            if name not in selector_parameters:
                continue
            default = default_nodes.get(name)
            if isinstance(default, ast.Constant) and (
                isinstance(default.value, (str, int, float, bool)) or default.value is None
            ):
                values.append((name, default.value))
        return tuple(values)


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    if isinstance(node, ast.Call):
        return _dotted(node.func)
    return ""


def _normal_path(path: str) -> str:
    return _PARAMETER.sub("{}", path.split("?", 1)[0])


def _is_source_transport_route(entry: AccessEntry) -> bool:
    return entry.method == "GET" and _normal_path(entry.path or "") in _SOURCE_TRANSPORT_PATHS


def _paths(node: ast.AST, variables: dict[str, set[str]]) -> set[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {_normal_path(node.value)}
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
                if isinstance(part.value, ast.Call) and _dotted(part.value.func).endswith(
                    "_query"
                ):
                    continue
                parts.append("{}")
        return {_normal_path("".join(parts))}
    return set()


def _functions(module: str, tree: ast.Module) -> list[_Function]:
    functions: list[_Function] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(_Function(f"{module}.{node.name}", module, None, node))
        elif isinstance(node, ast.ClassDef):
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    functions.append(_Function(
                        f"{module}.{node.name}.{member.name}", module, node.name, member
                    ))
    return functions


def _class_nodes(tree: ast.Module) -> dict[str, ast.ClassDef]:
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
    }


def _is_pydantic_model(node: ast.ClassDef) -> bool:
    return any(_dotted(base).endswith("BaseModel") for base in node.bases)


def _annotation_node(annotation: ast.expr) -> ast.expr:
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        return ast.parse(annotation.value, mode="eval").body
    return annotation


def _annotation_contract(
    annotation: ast.expr | None,
    type_names: dict[str, str],
) -> tuple[SdkValueKind, str | None, SdkValueKind | None, str | None]:
    """Classify a source annotation without inspecting runtime values."""
    if annotation is None:
        return "unknown", None, None, None
    annotation = _annotation_node(annotation)
    if isinstance(annotation, ast.Constant) and annotation.value is None:
        return "none", None, None, None
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        values = [_annotation_contract(annotation.left, type_names), _annotation_contract(
            annotation.right, type_names,
        )]
        non_none = [value for value in values if value[0] != "none"]
        if len(non_none) == 1:
            return non_none[0]
        return "unknown", None, None, None
    name = _dotted(annotation)
    if name in {"None", "NoneType"}:
        return "none", None, None, None
    if name in {"Any", "typing.Any"}:
        return "unknown", None, None, None
    if name in {"str", "int", "float", "bool", "bytes", "datetime", "date", "UUID"}:
        return "scalar", None, None, None
    if name in {"dict", "Dict", "Mapping", "MutableMapping"}:
        return "json_object", None, None, None
    if name in {"list", "List", "set", "tuple", "Sequence", "Iterable"}:
        return "json_list", None, "unknown", None
    if name == "Literal":
        return "scalar", None, None, None
    if name in type_names:
        sdk_type = type_names[name]
        kind: SdkValueKind = "sdk_credential" if name == "OAuthCredentials" else "sdk_object"
        return kind, sdk_type, None, None
    if isinstance(annotation, ast.Subscript):
        container = _dotted(annotation.value)
        if container in {"Optional", "typing.Optional"}:
            return _annotation_contract(annotation.slice, type_names)
        if container in {"Union", "typing.Union"}:
            values = [
                _annotation_contract(item, type_names)
                for item in (
                    annotation.slice.elts
                    if isinstance(annotation.slice, ast.Tuple)
                    else (annotation.slice,)
                )
            ]
            non_none = [value for value in values if value[0] != "none"]
            if len(non_none) == 1:
                return non_none[0]
            return "unknown", None, None, None
        if container in {"dict", "Dict", "Mapping", "MutableMapping"}:
            return "json_object", None, None, None
        if container in {"list", "List", "set", "tuple", "Sequence", "Iterable"}:
            element = _annotation_contract(annotation.slice, type_names)
            return "json_list", None, element[0], element[1]
        if container in {"Literal", "typing.Literal"}:
            return "scalar", None, None, None
    return "unknown", None, None, None


def _field_contracts(
    node: ast.ClassDef, type_names: dict[str, str],
) -> tuple[SdkFieldContract, ...]:
    fields: list[SdkFieldContract] = []
    for member in node.body:
        if isinstance(member, ast.AnnAssign) and isinstance(member.target, ast.Name):
            if member.target.id.startswith("_"):
                continue
            kind, sdk_type, element_kind, element_sdk_type = _annotation_contract(
                member.annotation, type_names,
            )
            fields.append(SdkFieldContract(
                name=member.target.id,
                kind=kind,
                sdk_type=sdk_type,
                element_kind=element_kind,
                element_sdk_type=element_sdk_type,
            ))
        elif (
            isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
            and any(isinstance(decorator, ast.Name) and decorator.id == "property"
                    for decorator in member.decorator_list)
            and not member.name.startswith("_")
        ):
            kind, sdk_type, element_kind, element_sdk_type = _annotation_contract(
                member.returns, type_names,
            )
            fields.append(SdkFieldContract(
                name=member.name,
                kind=kind,
                sdk_type=sdk_type,
                element_kind=element_kind,
                element_sdk_type=element_sdk_type,
            ))
    return tuple(fields)


def _sdk_names(function: _Function) -> set[str]:
    if function.class_name is None:
        if function.module == "files" and function.node.name == "_solution_query":
            return {"bifrost.files._solution_query"}
        if function.module == "_runtime_io" and not function.node.name.startswith("_"):
            return {f"bifrost._runtime_io.{function.node.name}"}
        if function.module == "_context" and not function.node.name.startswith("_"):
            return {
                f"bifrost._context.{function.node.name}",
                f"bifrost.{function.node.name}",
            }
        return set()
    if function.node.name.startswith("_"):
        return set()
    names = {f"bifrost.{function.module}.{function.node.name}"}
    names.add(f"bifrost.{function.module}.{function.class_name}.{function.node.name}")
    if function.module == "models":
        names.add(f"bifrost.{function.class_name}.{function.node.name}")
    if function.module == "_execution_context" and function.class_name == "ExecutionContext":
        names.update({
            f"bifrost.ExecutionContext.{function.node.name}",
            f"bifrost.context.{function.node.name}",
        })
    return names


def _public_exports(tree: ast.Module) -> set[str]:
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets):
            continue
        if isinstance(node.value, (ast.List, ast.Tuple)):
            return {
                value.value
                for value in node.value.elts
                if isinstance(value, ast.Constant) and isinstance(value.value, str)
            }
    return set()


def _is_pure_exception_class(node: ast.ClassDef, pure_names: set[str]) -> bool:
    if not node.bases or not all(
        _dotted(base) in {"Exception", *pure_names} for base in node.bases
    ):
        return False
    return all(
        isinstance(member, ast.Pass)
        or (
            isinstance(member, ast.Expr)
            and isinstance(member.value, ast.Constant)
            and isinstance(member.value.value, str)
        )
        for member in node.body
    )


def _is_pure_pydantic_dto(node: ast.ClassDef) -> bool:
    return _is_pydantic_model(node) and all(
        isinstance(member, (ast.AnnAssign, ast.Pass))
        or (
            isinstance(member, ast.Expr)
            and isinstance(member.value, ast.Constant)
            and isinstance(member.value.value, str)
        )
        for member in node.body
    )


@lru_cache(maxsize=1)
def _pure_public_constructor_contracts() -> tuple[
    dict[str, SdkCallContract], dict[str, SdkObjectContract], str,
]:
    """Prove root-exported local constructors from trusted SDK source only."""
    api_root = Path(__file__).resolve().parents[1]
    sdk_root = api_root / "bifrost"
    source_files = (
        sdk_root / "__init__.py",
        sdk_root / "models.py",
        api_root / "src" / "sdk" / "errors.py",
    )
    raw_sources = tuple(source.read_bytes() for source in source_files)
    digest = hashlib.sha256()
    for source, raw in zip(source_files, raw_sources):
        digest.update(str(source.relative_to(api_root)).encode())
        digest.update(raw)
    root_tree, models_tree, errors_tree = (ast.parse(raw) for raw in raw_sources)
    exported = _public_exports(root_tree)

    calls: dict[str, SdkCallContract] = {}
    objects: dict[str, SdkObjectContract] = {}
    pure_errors: set[str] = set()
    for node in errors_tree.body:
        if not isinstance(node, ast.ClassDef) or node.name not in exported:
            continue
        if not _is_pure_exception_class(node, pure_errors):
            continue
        pure_errors.add(node.name)
        sdk_type = f"bifrost.{node.name}"
        objects[sdk_type] = SdkObjectContract(sdk_type, ())
        calls[sdk_type] = SdkCallContract(
            effect="none", return_kind="sdk_object", sdk_type=sdk_type,
        )

    model_classes = _class_nodes(models_tree)
    type_names = {name: f"bifrost.{name}" for name in model_classes}
    for name, node in model_classes.items():
        if name not in exported or not _is_pure_pydantic_dto(node):
            continue
        sdk_type = type_names[name]
        kind: SdkValueKind = "sdk_credential" if name == "OAuthCredentials" else "sdk_object"
        objects[sdk_type] = SdkObjectContract(sdk_type, _field_contracts(node, type_names))
        calls[sdk_type] = SdkCallContract(
            effect="none", return_kind=kind, sdk_type=sdk_type,
        )
    return calls, objects, digest.hexdigest()


@lru_cache(maxsize=1)
def _sdk_contracts() -> tuple[dict[str, SdkCallContract], dict[str, SdkObjectContract]]:
    """Read public call and object shapes from shipped SDK source annotations."""
    sdk_root = Path(__file__).resolve().parents[1] / "bifrost"
    trees = {
        module: ast.parse((sdk_root / f"{module}.py").read_bytes())
        for module in _SDK_MODULES
    }
    classes = {module: _class_nodes(tree) for module, tree in trees.items()}
    type_names = {
        name: f"bifrost.{name}"
        for name in classes["models"]
    }
    type_names.update({
        name: f"bifrost.{name}"
        for name in classes["_execution_context"]
        if name in {"ExecutionContext", "EventContext", "ROIContext"}
    })

    objects: dict[str, SdkObjectContract] = {}
    for module in ("models", "_execution_context"):
        for name, node in classes[module].items():
            sdk_type = type_names.get(name)
            if sdk_type is not None:
                objects[sdk_type] = SdkObjectContract(sdk_type, _field_contracts(node, type_names))
    objects["bifrost.context"] = SdkObjectContract(
        "bifrost.ExecutionContext", objects["bifrost.ExecutionContext"].fields,
    )

    calls: dict[str, SdkCallContract] = {}
    for module, tree in trees.items():
        for function in _functions(module, tree):
            return_kind, sdk_type, element_kind, element_sdk_type = _annotation_contract(
                function.node.returns, type_names,
            )
            effect: SdkEffect = "none"
            if function.module == "_runtime_io" and function.node.name == "write_log":
                effect = "runtime_log"
            elif (
                function.module == "_execution_context"
                and function.class_name == "ExecutionContext"
                and function.node.name == "set_scope"
            ):
                effect = "scope_override"
            contract = SdkCallContract(
                effect=effect,
                return_kind=return_kind,
                sdk_type=sdk_type,
                element_kind=element_kind,
                element_sdk_type=element_sdk_type,
                parameters=function.parameters,
                resource_defaults=function.resource_defaults,
            )
            for name in _sdk_names(function):
                calls[name] = contract

    for name, node in classes["models"].items():
        if _is_pydantic_model(node):
            sdk_type = type_names[name]
            kind: SdkValueKind = "sdk_credential" if name == "OAuthCredentials" else "sdk_object"
            calls[f"{sdk_type}.model_validate"] = SdkCallContract(
                effect="none", return_kind=kind, sdk_type=sdk_type,
            )
    constructor_calls, constructor_objects, _ = _pure_public_constructor_contracts()
    calls.update(constructor_calls)
    objects.update(constructor_objects)
    return calls, objects


def _function_target(
    target: str, current: _Function, functions: dict[str, _Function]
) -> str | None:
    if target.startswith(("self.", "cls.")) and current.class_name is not None:
        candidate = f"{current.module}.{current.class_name}.{target.rsplit('.', 1)[-1]}"
        return candidate if candidate in functions else None
    local = f"{current.module}.{target}"
    if local in functions:
        return local
    if "." not in target:
        # A bare builtin (for example ``set(value)``) is not evidence of an
        # SDK helper call.  The old suffix fallback could bind it to an
        # unrelated public method named ``set``.
        return None
    suffix = f".{target}"
    matches = [key for key in functions if key.endswith(suffix)]
    return matches[0] if len(matches) == 1 else None


def _call_arguments(
    call: ast.Call, function: _Function, variables: dict[str, set[str]]
) -> dict[str, set[str]]:
    arguments: dict[str, set[str]] = {}
    for parameter, value in zip(function.parameters, call.args):
        paths = _paths(value, variables)
        if paths:
            arguments[parameter] = paths
    for keyword in call.keywords:
        if keyword.arg in function.parameters:
            paths = _paths(keyword.value, variables)
            if paths:
                arguments[keyword.arg] = paths
    return arguments


def _import_aliases(node: ast.AST) -> dict[str, str]:
    """Resolve only source-level imports; never import a service at analysis time."""
    aliases: dict[str, str] = {}
    for child in ast.walk(node):
        if isinstance(child, ast.Import):
            for imported in child.names:
                local = imported.asname or imported.name.split(".", 1)[0]
                aliases[local] = imported.name
        elif isinstance(child, ast.ImportFrom) and child.module is not None:
            for imported in child.names:
                if imported.name == "*":
                    continue
                aliases[imported.asname or imported.name] = f"{child.module}.{imported.name}"
    return aliases


def _resolved_dotted(node: ast.AST, aliases: dict[str, str]) -> str:
    dotted = _dotted(node)
    if not dotted:
        return ""
    first, *rest = dotted.split(".")
    target = aliases.get(first, first)
    return ".".join((target, *rest)) if rest else target


def _router_prefixes(tree: ast.Module) -> dict[str, str]:
    """Read literal APIRouter prefixes without importing the router module."""
    prefixes: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        if not _dotted(node.value.func).endswith("APIRouter"):
            continue
        prefix = next((
            keyword.value
            for keyword in node.value.keywords
            if keyword.arg == "prefix"
        ), None)
        if not isinstance(prefix, ast.Constant) or not isinstance(prefix.value, str):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                prefixes[target.id] = prefix.value
    return prefixes


def _route_path(
    decorator: ast.expr,
    router_prefixes: dict[str, str],
) -> tuple[str, str] | None:
    if not isinstance(decorator, ast.Call) or not decorator.args:
        return None
    target = _dotted(decorator.func)
    router_name, separator, method_name = target.partition(".")
    if not separator or router_name not in router_prefixes:
        return None
    method = method_name.upper()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        return None
    path = decorator.args[0]
    if not isinstance(path, ast.Constant) or not isinstance(path.value, str):
        return None
    return method, _normal_path(f"{router_prefixes[router_name]}{path.value}")


def _operation_permissions(operation: str) -> set[str]:
    """Resolve a literal operation ID or METHOD/path through the complete catalog."""
    route = None
    if " " in operation:
        method, path = operation.split(" ", 1)
        route = (method.upper(), _normal_path(path))
    return {
        entry.permission
        for entry in ACCESS_LIST
        if entry.permission is not None
        and (
            entry.operation_id == operation
            or (
                route is not None
                and entry.method is not None
                and entry.path is not None
                and (entry.method, _normal_path(entry.path)) == route
            )
        )
    }


@dataclass(frozen=True)
class _StaticSymbols:
    strings: dict[str, set[str]]
    mappings: dict[str, set[str]]


def _mapping_values(
    node: ast.AST,
    values: dict[str, set[str]],
    mappings: dict[str, set[str]],
    functions: dict[str, _Function] | None = None,
    aliases: dict[str, str] | None = None,
    active_returns: set[str] | None = None,
    current_module: str | None = None,
) -> set[str]:
    if isinstance(node, ast.Dict):
        return set().union(*(
            _string_values(value, values)
            for value in node.values
            if value is not None
        ))
    if isinstance(node, ast.Name):
        return mappings.get(node.id, set())
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
        return mappings.get(node.value.id, set())
    if isinstance(node, ast.DictComp):
        return _mapping_values(
            node.value, values, mappings, functions, aliases, active_returns, current_module,
        )
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id in {"dict", "sorted"}:
            return _mapping_values(
                node.args[0], values, mappings, functions, aliases, active_returns, current_module,
            ) if node.args else set()
        target = _resolved_dotted(node.func, aliases or {})
        if functions is not None and target not in functions and current_module is not None:
            target = f"{current_module}.{target}"
        if functions is None or target not in functions:
            return set()
        active_returns = active_returns or set()
        if target in active_returns:
            return set()
        active_returns.add(target)
        try:
            function = functions[target]
            return set().union(*(
                _mapping_values(
                    returned.value,
                    {**values, **_static_symbols(function.node).strings},
                    {**mappings, **_static_symbols(function.node).mappings},
                    functions,
                    {**(aliases or {}), **_import_aliases(function.node)},
                    active_returns,
                    function.module,
                )
                for returned in ast.walk(function.node)
                if isinstance(returned, ast.Return) and returned.value is not None
            ))
        finally:
            active_returns.remove(target)
    return set()


def _static_symbols(tree: ast.AST) -> _StaticSymbols:
    """Read only literal module/local constants and literal mapping values."""
    strings: dict[str, set[str]] = {}
    mappings: dict[str, set[str]] = {}
    statements = tree.body if isinstance(tree, ast.Module) else []
    for statement in statements:
        value: ast.AST | None = None
        targets: list[ast.expr] = []
        if isinstance(statement, ast.Assign):
            value, targets = statement.value, statement.targets
        elif isinstance(statement, ast.AnnAssign):
            value, targets = statement.value, [statement.target]
        if value is None:
            continue
        names = [target.id for target in targets if isinstance(target, ast.Name)]
        if not names:
            continue
        literal_strings = _string_values(value, strings)
        literal_mapping = _mapping_values(value, strings, mappings)
        for name in names:
            if literal_strings:
                strings[name] = literal_strings
            if literal_mapping:
                mappings[name] = literal_mapping
    return _StaticSymbols(strings, mappings)


def _iter_mapping_values(
    node: ast.AST,
    values: dict[str, set[str]],
    mappings: dict[str, set[str]],
    functions: dict[str, _Function],
    aliases: dict[str, str],
    current_module: str,
) -> set[str]:
    """Return finite literal mapping values unpacked by a ``for`` loop."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "sorted":
        return _iter_mapping_values(
            node.args[0], values, mappings, functions, aliases, current_module,
        ) if node.args else set()
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "items":
        return _mapping_values(
            node.func.value, values, mappings, functions, aliases, current_module=current_module,
        )
    return set()


def _permission_call_arguments(
    call: ast.Call,
    function: _Function,
    values: dict[str, set[str]],
) -> dict[str, set[str]]:
    """Bind only statically known string arguments for trusted helper traversal."""
    arguments: dict[str, set[str]] = {}
    for parameter, value in zip(function.parameters, call.args):
        resolved = _string_values(value, values)
        if resolved:
            arguments[parameter] = resolved
    for keyword in call.keywords:
        if keyword.arg in function.parameters:
            resolved = _string_values(keyword.value, values)
            if resolved:
                arguments[keyword.arg] = resolved
    return arguments


def _string_values(node: ast.AST, values: dict[str, set[str]]) -> set[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.Name):
        return values.get(node.id, set())
    return set()


def _literal_permission(
    node: ast.Call,
    *,
    operation: bool = False,
    values: dict[str, set[str]] | None = None,
) -> tuple[set[str], bool]:
    """Read statically trusted guard values, preserving genuinely dynamic requirements."""
    if len(node.args) < 2:
        return set(), True
    resolved = _string_values(node.args[1], values or {})
    if not resolved:
        return set(), True
    if not operation:
        valid = set()
        for permission in resolved:
            try:
                parse_permission(permission)
            except ValueError:
                continue
            valid.add(permission)
        return valid, len(valid) != len(resolved)
    for keyword in node.keywords:
        if keyword.arg == "permission":
            permissions = _string_values(keyword.value, values or {})
            if permissions:
                valid = set()
                for permission in permissions:
                    try:
                        parse_permission(permission)
                    except ValueError:
                        continue
                    valid.add(permission)
                return valid, len(valid) != len(permissions)
            else:
                return set(), True
    operation_permissions = [_operation_permissions(value) for value in resolved]
    permissions = set().union(*operation_permissions)
    valid_permissions = set()
    for permission in permissions:
        try:
            parse_permission(permission)
        except ValueError:
            continue
        valid_permissions.add(permission)
    return valid_permissions, (
        not bool(valid_permissions) or any(not permissions for permissions in operation_permissions)
    )


@dataclass(frozen=True)
class _RoutePermissionMetadata:
    candidates: tuple[tuple[str, Literal["unconditional", "conditional"]], ...]
    unresolved: bool


@dataclass(frozen=True)
class _PermissionScope:
    values: dict[str, set[str]]
    conditional_names: frozenset[str] = frozenset()


def _merge_permission_scopes(scopes: list[_PermissionScope]) -> _PermissionScope:
    """Keep only bindings proven on every branch, marking divergent literals."""
    if not scopes:
        return _PermissionScope({})
    merged: dict[str, set[str]] = {}
    conditional_names: set[str] = set()
    for name in set().union(*(scope.values for scope in scopes)):
        branch_values = [scope.values.get(name) for scope in scopes]
        if any(values is None for values in branch_values):
            continue
        assert all(values is not None for values in branch_values)
        values = set().union(*(values for values in branch_values if values is not None))
        merged[name] = values
        if (
            len({tuple(sorted(value)) for value in branch_values if value is not None}) > 1
            or any(name in scope.conditional_names for scope in scopes)
        ):
            conditional_names.add(name)
    return _PermissionScope(merged, frozenset(conditional_names))


def _assign_permission_scope(scope: _PermissionScope, node: ast.AST) -> _PermissionScope:
    value: ast.AST | None = None
    targets: list[ast.expr] = []
    if isinstance(node, ast.Assign):
        value, targets = node.value, node.targets
    elif isinstance(node, ast.AnnAssign):
        value, targets = node.value, [node.target]
    if value is None:
        return scope
    names = [target.id for target in targets if isinstance(target, ast.Name)]
    if not names:
        return scope
    values = dict(scope.values)
    conditional_names = set(scope.conditional_names)
    resolved = _string_values(value, values)
    for name in names:
        if resolved:
            values[name] = resolved
            conditional_names.discard(name)
        else:
            values.pop(name, None)
            conditional_names.discard(name)
    return _PermissionScope(values, frozenset(conditional_names))


def _scope_uses_conditional_value(node: ast.Call, scope: _PermissionScope) -> bool:
    return any(
        isinstance(child, ast.Name) and child.id in scope.conditional_names
        for child in ast.walk(node)
    )


def _permission_arguments_key(
    function_key: str, argument_values: dict[str, set[str]] | None,
) -> tuple[str, tuple[tuple[str, tuple[str, ...]], ...]]:
    return (
        function_key,
        tuple(sorted(
            (name, tuple(sorted(values)))
            for name, values in (argument_values or {}).items()
        )),
    )


def _helper_permission_metadata(
    function_key: str,
    functions: dict[str, _Function],
    module_aliases: dict[str, dict[str, str]],
    module_symbols: dict[str, _StaticSymbols],
    inherited_condition: bool,
    active: set[tuple[str, tuple[tuple[str, tuple[str, ...]], ...]]],
    argument_values: dict[str, set[str]] | None = None,
) -> _RoutePermissionMetadata:
    """Traverse trusted shared SDK helpers with a cycle guard and branch facts."""
    resolution = _permission_arguments_key(function_key, argument_values)
    if resolution in active:
        # A repeated abstract call state has no additional static guard facts.
        return _RoutePermissionMetadata((), False)
    active.add(resolution)
    try:
        function = functions[function_key]
        aliases = {**module_aliases[function.module], **_import_aliases(function.node)}
        symbols = module_symbols[function.module]
        initial_scope = _PermissionScope({**symbols.strings, **(argument_values or {})})
        candidates: set[tuple[str, Literal["unconditional", "conditional"]]] = set()
        unresolved = False

        def visit_block(
            statements: list[ast.stmt],
            conditional: bool,
            scope: _PermissionScope,
            finite_selection: bool = False,
        ) -> _PermissionScope:
            for statement in statements:
                scope = visit(statement, conditional, scope, finite_selection)
            return scope

        def visit(
            node: ast.AST,
            conditional: bool,
            scope: _PermissionScope,
            finite_selection: bool = False,
        ) -> _PermissionScope:
            nonlocal unresolved
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                if node is not function.node:
                    return scope
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                # An assignment may await a trusted helper; its right-hand
                # side still contributes guards before it updates a binding.
                if node.value is not None:
                    scope = visit(node.value, conditional, scope, finite_selection)
                return _assign_permission_scope(scope, node)
            if isinstance(node, ast.If):
                visit(node.test, conditional, scope, finite_selection)
                body_scope = visit_block(node.body, True, scope, finite_selection)
                else_scope = visit_block(node.orelse, True, scope, finite_selection)
                return _merge_permission_scopes([body_scope, else_scope])
            if isinstance(node, (ast.For, ast.AsyncFor)):
                # The iterator expression is evaluated before any iteration;
                # it may itself call a trusted helper with named guards.
                iter_scope = visit(node.iter, conditional, scope, finite_selection)
                loop_scope = _PermissionScope(
                    dict(iter_scope.values), iter_scope.conditional_names,
                )
                item_values = _iter_mapping_values(
                    node.iter, iter_scope.values, symbols.mappings, functions, aliases, function.module,
                )
                if item_values:
                    target = node.target
                    if isinstance(target, (ast.Tuple, ast.List)) and len(target.elts) >= 2:
                        value_target = target.elts[1]
                        if isinstance(value_target, ast.Name):
                            loop_scope = _PermissionScope(
                                {**loop_scope.values, value_target.id: item_values},
                                loop_scope.conditional_names | frozenset({value_target.id}),
                            )
                body_scope = visit_block(
                    node.body, True, loop_scope, finite_selection or bool(item_values),
                )
                after_loop = _merge_permission_scopes([iter_scope, body_scope])
                else_scope = visit_block(node.orelse, True, after_loop, finite_selection)
                return _merge_permission_scopes([after_loop, else_scope])
            if isinstance(node, ast.While):
                # A while condition runs before the first body evaluation.
                condition_scope = visit(node.test, conditional, scope, finite_selection)
                body_scope = visit_block(node.body, True, condition_scope, finite_selection)
                after_loop = _merge_permission_scopes([condition_scope, body_scope])
                else_scope = visit_block(node.orelse, True, after_loop, finite_selection)
                return _merge_permission_scopes([after_loop, else_scope])
            if isinstance(node, ast.Try):
                body_scope = visit_block(node.body, conditional, scope, finite_selection)
                alternatives = [body_scope]
                alternatives.extend(
                    visit_block(handler.body, True, scope, finite_selection)
                    for handler in node.handlers
                )
                merged = _merge_permission_scopes(alternatives)
                merged = visit_block(node.orelse, True, merged, finite_selection)
                return visit_block(node.finalbody, True, merged, finite_selection)
            if isinstance(node, ast.Call):
                target = _resolved_dotted(node.func, aliases)
                tail = target.rsplit(".", 1)[-1]
                is_authorization_gate = False
                if target.startswith("src.services.authorization.enforce."):
                    is_conditional = conditional or tail.startswith("allows_")
                    if tail in {
                        "require_permission", "require_named_permission", "allows_permission",
                        "permitted_organizations",
                    }:
                        is_authorization_gate = True
                        permissions, unknown = _literal_permission(node, values=scope.values)
                        is_conditional = (
                            is_conditional
                            or tail == "permitted_organizations"
                            or finite_selection
                            or _scope_uses_conditional_value(node, scope)
                        )
                        candidates.update((
                            permission,
                            "conditional" if is_conditional else "unconditional",
                        ) for permission in permissions)
                        unresolved = unresolved or unknown
                    elif tail in {"require_operation", "allows_operation"}:
                        is_authorization_gate = True
                        permissions, unknown = _literal_permission(
                            node, operation=True, values=scope.values,
                        )
                        is_conditional = (
                            is_conditional
                            or finite_selection
                            or _scope_uses_conditional_value(node, scope)
                        )
                        candidates.update((
                            permission,
                            "conditional" if is_conditional else "unconditional",
                        ) for permission in permissions)
                        unresolved = unresolved or unknown
                helper_key = None if is_authorization_gate else (
                    target if target in functions else None
                )
                if helper_key is None:
                    local_target = f"{function.module}.{target}"
                    if local_target in functions:
                        helper_key = local_target
                if helper_key is not None:
                    helper = functions[helper_key]
                    child_values = _permission_call_arguments(
                        node, helper, scope.values,
                    )
                    child_condition = (
                        conditional
                        or inherited_condition
                        or finite_selection
                        or _scope_uses_conditional_value(node, scope)
                    )
                    nested = _helper_permission_metadata(
                        helper_key, functions, module_aliases, module_symbols,
                        child_condition, active, child_values,
                    )
                    candidates.update(nested.candidates)
                    unresolved = unresolved or nested.unresolved
            for child in ast.iter_child_nodes(node):
                scope = visit(child, conditional, scope, finite_selection)
            return scope

        visit_block(function.node.body, inherited_condition, initial_scope)
        return _RoutePermissionMetadata(tuple(sorted(candidates)), unresolved)
    finally:
        active.remove(resolution)


@lru_cache(maxsize=1)
def _service_permission_routes() -> tuple[
    dict[tuple[str, str], _RoutePermissionMetadata], str,
]:
    """Map canonical SDK router paths to actual named helper guards from source."""
    api_root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    functions: dict[str, _Function] = {}
    module_aliases: dict[str, dict[str, str]] = {}
    module_symbols: dict[str, _StaticSymbols] = {}
    trusted_helper_sources = (
        (api_root / "shared", "shared", "sdk_*.py"),
        (
            api_root / "src" / "services" / "authorization",
            "src.services.authorization",
            "*.py",
        ),
    )
    for root, package, pattern in trusted_helper_sources:
        for source in sorted(root.glob(pattern)):
            raw = source.read_bytes()
            module = f"{package}.{source.stem}"
            tree = ast.parse(raw)
            digest.update(module.encode())
            digest.update(raw)
            module_aliases[module] = _import_aliases(tree)
            module_symbols[module] = _static_symbols(tree)
            functions.update({function.key: function for function in _functions(module, tree)})

    route_metadata: dict[tuple[str, str], _RoutePermissionMetadata] = {}
    route_handlers: list[tuple[tuple[str, str], str]] = []
    for source in sorted((api_root / "src" / "routers").glob("*.py")):
        raw = source.read_bytes()
        module = f"src.routers.{source.stem}"
        tree = ast.parse(raw)
        digest.update(module.encode())
        digest.update(raw)
        module_aliases[module] = _import_aliases(tree)
        module_symbols[module] = _static_symbols(tree)
        router_functions = _functions(module, tree)
        functions.update({function.key: function for function in router_functions})
        prefixes = _router_prefixes(tree)
        for function in router_functions:
            if function.class_name is not None:
                continue
            for decorator in function.node.decorator_list:
                route = _route_path(decorator, prefixes)
                if route is not None:
                    route_handlers.append((route, function.key))

    for route, handler_key in route_handlers:
        metadata = _helper_permission_metadata(
            handler_key, functions, module_aliases, module_symbols, False, set(),
        )
        if not metadata.candidates and not metadata.unresolved:
            continue
        existing = route_metadata.get(route)
        if existing is None:
            route_metadata[route] = metadata
            continue
        route_metadata[route] = _RoutePermissionMetadata(
            tuple(sorted(set(existing.candidates) | set(metadata.candidates))),
            existing.unresolved or metadata.unresolved,
        )
    return route_metadata, digest.hexdigest()


@lru_cache(maxsize=1)
def _sdk_bindings() -> tuple[dict[str, tuple[tuple[str, str], ...]], frozenset[str], str]:
    digest = hashlib.sha256()
    sdk_root = Path(__file__).resolve().parents[1] / "bifrost"
    functions: dict[str, _Function] = {}
    for module in _SDK_MODULES:
        raw = (sdk_root / f"{module}.py").read_bytes()
        digest.update(module.encode())
        digest.update(raw)
        for function in _functions(module, ast.parse(raw)):
            functions[function.key] = function

    resolving: set[tuple[str, tuple[tuple[str, tuple[str, ...]], ...]]] = set()

    @lru_cache(maxsize=None)
    def routes_for(
        function_key: str, arguments: tuple[tuple[str, tuple[str, ...]], ...] = ()
    ) -> tuple[tuple[tuple[str, str], ...], bool]:
        resolution = (function_key, arguments)
        if resolution in resolving:
            # The static argument graph is cyclic. Do not recurse forever or
            # silently treat a capability-bearing call as transport-free.
            return (), True
        resolving.add(resolution)
        try:
            return _routes_for(function_key, arguments)
        finally:
            resolving.remove(resolution)

    def _routes_for(
        function_key: str, arguments: tuple[tuple[str, tuple[str, ...]], ...]
    ) -> tuple[tuple[tuple[str, str], ...], bool]:
        function = functions[function_key]
        variables = {name: set(paths) for name, paths in arguments}
        for node in ast.walk(function.node):
            if isinstance(node, ast.Assign):
                paths = _paths(node.value, variables)
                for target in node.targets:
                    if isinstance(target, ast.Name) and paths:
                        variables.setdefault(target.id, set()).update(paths)

        routes: set[tuple[str, str]] = set()
        unresolved_transport = False
        for node in ast.walk(function.node):
            if not isinstance(node, ast.Call):
                continue
            target = _dotted(node.func)
            tail = target.rsplit(".", 1)[-1]
            if tail in _TRANSPORTS and len(node.args) >= 2:
                method_node = node.args[0]
                method = (
                    method_node.value.upper()
                    if isinstance(method_node, ast.Constant) and isinstance(method_node.value, str)
                    else None
                )
                paths = _paths(node.args[1], variables)
                if method is not None and paths:
                    routes.update((method, path) for path in paths if path.startswith("/api/"))
                else:
                    unresolved_transport = True
                continue
            if (
                function.module == "api"
                and function.class_name == "api"
                and tail in _GENERIC_HTTP_METHODS
            ):
                unresolved_transport = True
                continue
            helper_key = _function_target(target, function, functions)
            if helper_key is not None:
                helper = functions[helper_key]
                helper_args = tuple(sorted(
                    (name, tuple(sorted(paths)))
                    for name, paths in _call_arguments(node, helper, variables).items()
                ))
                helper_routes, helper_unresolved = routes_for(helper_key, helper_args)
                routes.update(helper_routes)
                unresolved_transport = unresolved_transport or helper_unresolved
        return tuple(sorted(routes)), unresolved_transport

    bindings: dict[str, tuple[tuple[str, str], ...]] = {}
    unresolved_calls: set[str] = set()
    for function in functions.values():
        for name in _sdk_names(function):
            routes, unresolved_transport = routes_for(function.key)
            bindings[name] = routes
            if unresolved_transport:
                unresolved_calls.add(name)
    constructor_contracts, _, constructor_fingerprint = _pure_public_constructor_contracts()
    for name in _sdk_contracts()[0]:
        if name.endswith(".model_validate"):
            # Pydantic's inherited constructor validates supplied data locally;
            # it has no platform transport and must not be treated as a grant.
            bindings.setdefault(name, ())
    for name in constructor_contracts:
        bindings.setdefault(name, ())
    for name, contract in sorted(_sdk_contracts()[0].items()):
        digest.update(
            (
                f"{name}:{contract.effect}:{contract.return_kind}:{contract.sdk_type}:"
                f"{contract.element_kind}:{contract.element_sdk_type}:{contract.parameters}:"
                f"{contract.resource_defaults}"
            ).encode()
        )
    for entry in ACCESS_LIST:
        digest.update(
            f"{entry.key}:{entry.access_class}:{entry.permission}:{entry.boundary}".encode()
        )
    _, service_permissions_fingerprint = _service_permission_routes()
    digest.update(service_permissions_fingerprint.encode())
    digest.update(constructor_fingerprint.encode())
    return bindings, frozenset(unresolved_calls), digest.hexdigest()


def _routes_by_key() -> dict[tuple[str, str], AccessEntry]:
    return {
        (entry.method, _normal_path(entry.path)): entry
        for entry in ACCESS_LIST
        if entry.method is not None and entry.path is not None
    }


def sdk_call_contract(call: str) -> SdkCallContract | None:
    """Return static call behavior for a known shipped SDK symbol."""
    return _sdk_contracts()[0].get(call)


def sdk_object_contract(sdk_type: str) -> SdkObjectContract | None:
    """Return static public fields for a known shipped SDK object or proxy."""
    return _sdk_contracts()[1].get(sdk_type)


def sdk_permission_fingerprint() -> str:
    """Version proposals when SDK transports or authorization catalog change."""
    return _sdk_bindings()[2]


def sdk_route_candidates(calls: list[str]) -> list[SdkRouteCandidate]:
    """Return canonical authorization metadata for statically bound SDK routes."""
    bindings, _, _ = _sdk_bindings()
    routes = _routes_by_key()
    candidates = {
        SdkRouteCandidate(
            call=call,
            method=method,
            path=entry.path or path,
            access_class=entry.access_class,
            permission=entry.permission,
            boundary=entry.boundary,
        )
        for call in set(calls)
        for method, path in bindings.get(call, ())
        if (entry := routes.get((method, path))) is not None
    }
    return sorted(candidates, key=lambda candidate: (candidate.call, candidate.method, candidate.path))


def _call_named_permission_metadata(
    call: str,
    bindings: dict[str, tuple[tuple[str, str], ...]],
) -> tuple[_RoutePermissionMetadata, ...]:
    route_metadata, _ = _service_permission_routes()
    return tuple(
        route_metadata[route]
        for route in bindings.get(call, ())
        if route in route_metadata
    )


def sdk_named_permission_candidates(calls: list[str]) -> list[SdkNamedPermissionCandidate]:
    """Return source-derived named guards, retaining whether their branch is conditional."""
    bindings, _, _ = _sdk_bindings()
    candidates = {
        SdkNamedPermissionCandidate(call, permission, condition)
        for call in set(calls)
        for metadata in _call_named_permission_metadata(call, bindings)
        for permission, condition in metadata.candidates
    }
    return sorted(candidates, key=lambda candidate: (
        candidate.call, candidate.permission, candidate.condition,
    ))


def permissions_for_sdk_calls(calls: list[str]) -> tuple[list[str], list[str]]:
    """Return named declaration candidates and unresolved SDK transport mappings."""
    bindings, unresolved_calls, _ = _sdk_bindings()
    routes = _routes_by_key()
    permissions = {
        candidate.permission
        for candidate in sdk_named_permission_candidates(calls)
    }
    unknowns: set[str] = set()
    for call in sorted(set(calls)):
        bound = bindings.get(call)
        if bound is None:
            unknowns.add(f"No static platform transport mapping for SDK call {call}")
            continue
        if call in unresolved_calls:
            unknowns.add(f"No static platform transport mapping for SDK call {call}")
        metadata = _call_named_permission_metadata(call, bindings)
        for item in metadata:
            if item.unresolved:
                unknowns.add(f"No static named permission mapping for SDK call {call}")
        for method, path in bound:
            entry = routes.get((method, path))
            if entry is None:
                unknowns.add(f"No canonical access entry for {call}: {method} {path}")
            elif entry.access_class == AccessClass.PERMISSION and entry.permission:
                if not _is_source_transport_route(entry):
                    permissions.add(entry.permission)
    return sorted(permissions), sorted(unknowns)


def permissions_for_recorded_operations(operations: list[str]) -> tuple[list[str], list[str]]:
    """Map operation IDs or recorded route templates through every access class."""
    routes = _routes_by_key()
    by_operation = {
        entry.operation_id: entry
        for entry in ACCESS_LIST
        if entry.operation_id is not None
    }
    permissions: set[str] = set()
    unknowns: set[str] = set()
    route_metadata, _ = _service_permission_routes()
    for operation in sorted(set(operations)):
        entry = _recorded_access_entry(operation, by_operation, routes)
        if entry is None:
            unknowns.add(f"No canonical permission mapping for observed operation {operation}")
        elif (
            entry.access_class == AccessClass.PERMISSION
            and entry.permission
            and not _is_source_transport_route(entry)
        ):
            permissions.add(entry.permission)
        if entry is not None and entry.method is not None and entry.path is not None:
            metadata = route_metadata.get((entry.method, _normal_path(entry.path)))
            if metadata is not None:
                permissions.update(
                    permission
                    for permission, condition in metadata.candidates
                    if condition == "unconditional"
                )
                if metadata.unresolved:
                    unknowns.add(
                        f"No static named permission mapping for observed operation {operation}"
                    )
    return sorted(permissions), sorted(unknowns)


def recorded_route_candidates(operations: list[str]) -> list[SdkRouteCandidate]:
    """Return canonical metadata for recorded operation IDs or route templates."""
    routes = _routes_by_key()
    by_operation = {
        entry.operation_id: entry
        for entry in ACCESS_LIST
        if entry.operation_id is not None
    }
    candidates = {
        SdkRouteCandidate(
            call=operation,
            method=entry.method or "",
            path=entry.path or "",
            access_class=entry.access_class,
            permission=entry.permission,
            boundary=entry.boundary,
        )
        for operation in set(operations)
        if (entry := _recorded_access_entry(operation, by_operation, routes)) is not None
    }
    return sorted(candidates, key=lambda candidate: (candidate.call, candidate.method, candidate.path))


def _recorded_access_entry(
    operation: str,
    by_operation: dict[str, AccessEntry],
    routes: dict[tuple[str, str], AccessEntry],
) -> AccessEntry | None:
    entry = by_operation.get(operation)
    if entry is None and " " in operation:
        method, path = operation.split(" ", 1)
        return routes.get((method.upper(), _normal_path(path)))
    return entry
