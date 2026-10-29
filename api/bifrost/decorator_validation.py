"""Static acceptance checks for Bifrost executable decorators.

The runtime decorators intentionally accept ``**kwargs`` for already-deployed
source.  New source takes this stricter, AST-only path before it is persisted.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable

from bifrost.definition_validation import DefinitionIssue, DefinitionValidationError


_DECORATORS = frozenset({"workflow", "tool", "data_provider", "service"})
_COMMON_KEYS = frozenset({"id", "name", "description", "category", "tags"})
_SUPPORTED_KEYS = {
    "workflow": _COMMON_KEYS | {"is_tool"},
    "tool": _COMMON_KEYS,
    "data_provider": _COMMON_KEYS,
    "service": _COMMON_KEYS,
}
_REMOVED_KEYS = {
    "retry_policy": "Remove it: the current workflow API has no retry-policy setting.",
}


def validate_python_decorators(source: str, *, path: str) -> None:
    """Reject unsupported Bifrost decorator settings without importing source.

    Only decorators whose bindings can be proven to originate from ``bifrost``
    are inspected.  This deliberately leaves same-named, unrelated decorators
    alone.
    """
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        raise DefinitionValidationError([
            DefinitionIssue(
                path=path,
                location=(),
                code="invalid_python",
                message=f"Syntax error: {exc.msg}",
                line=exc.lineno,
            )
        ]) from None

    validator = _DecoratorValidator(path)
    validator.visit_body(tree.body, {})
    if validator.issues:
        raise DefinitionValidationError(validator.issues)


class _DecoratorValidator:
    def __init__(self, path: str):
        self.path = path
        self.issues: list[DefinitionIssue] = []

    def visit_body(self, statements: Iterable[ast.stmt], bindings: dict[str, str | None]) -> None:
        scope = dict(bindings)
        for statement in statements:
            if isinstance(statement, ast.Import):
                for imported in statement.names:
                    bound_name = imported.asname or imported.name.split(".")[0]
                    # ``import bifrost.decorators`` binds ``bifrost``; only an
                    # explicit alias binds the full module path.
                    scope[bound_name] = imported.name if imported.asname else bound_name
                continue
            if isinstance(statement, ast.ImportFrom):
                self._bind_from_import(statement, scope)
                continue

            if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                self._bind_assignment(statement, scope)
                continue

            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._validate_decorators(statement.decorator_list, scope)
                nested = dict(scope)
                for argument in (*statement.args.posonlyargs, *statement.args.args, *statement.args.kwonlyargs):
                    nested[argument.arg] = None
                if statement.args.vararg is not None:
                    nested[statement.args.vararg.arg] = None
                if statement.args.kwarg is not None:
                    nested[statement.args.kwarg.arg] = None
                self.visit_body(statement.body, nested)
                scope[statement.name] = None
                continue

            if isinstance(statement, ast.ClassDef):
                self._validate_decorators(statement.decorator_list, scope)
                self.visit_body(statement.body, dict(scope))
                scope[statement.name] = None
                continue

            if isinstance(statement, (ast.For, ast.AsyncFor, ast.With, ast.AsyncWith, ast.Try)):
                self._visit_control_flow(statement, scope)
                self._shadow_bound_names(statement, scope)
                continue

            if isinstance(statement, (ast.If, ast.While)):
                for body in self._nested_bodies(statement):
                    self.visit_body(body, dict(scope))
                self._shadow_bound_names(statement, scope)
                continue

            self._shadow_bound_names(statement, scope)

    def _visit_control_flow(
        self, statement: ast.For | ast.AsyncFor | ast.With | ast.AsyncWith | ast.Try,
        scope: dict[str, str | None],
    ) -> None:
        """Visit block bodies with their targets shadowing imported decorators."""
        if isinstance(statement, (ast.For, ast.AsyncFor)):
            nested = dict(scope)
            self._shadow_target(statement.target, nested)
            self.visit_body(statement.body, nested)
            self.visit_body(statement.orelse, dict(nested))
            return

        if isinstance(statement, (ast.With, ast.AsyncWith)):
            nested = dict(scope)
            for item in statement.items:
                if item.optional_vars is not None:
                    self._shadow_target(item.optional_vars, nested)
            self.visit_body(statement.body, nested)
            return

        self.visit_body(statement.body, dict(scope))
        self.visit_body(statement.orelse, dict(scope))
        self.visit_body(statement.finalbody, dict(scope))
        for handler in statement.handlers:
            nested = dict(scope)
            if handler.name is not None:
                nested[handler.name] = None
            self.visit_body(handler.body, nested)

    @staticmethod
    def _nested_bodies(statement: ast.stmt) -> Iterable[list[ast.stmt]]:
        for name in ("body", "orelse", "finalbody"):
            body = getattr(statement, name, None)
            if body:
                yield body
        for handler in getattr(statement, "handlers", ()):
            yield handler.body

    @staticmethod
    def _bind_from_import(statement: ast.ImportFrom, scope: dict[str, str | None]) -> None:
        if statement.level:
            for imported in statement.names:
                if imported.name == "*":
                    scope.clear()
                else:
                    scope[imported.asname or imported.name] = None
            return
        module = statement.module or ""
        for imported in statement.names:
            if imported.name == "*":
                if module in {"bifrost", "bifrost.decorators"}:
                    prefix = module
                    for decorator in _DECORATORS:
                        scope[decorator] = f"{prefix}.{decorator}"
                else:
                    # An unknown star import may replace an imported decorator
                    # binding. Do not claim its provenance through the import.
                    scope.clear()
                continue
            name = imported.asname or imported.name
            if module == "bifrost" and imported.name in _DECORATORS:
                scope[name] = f"bifrost.{imported.name}"
            elif module == "bifrost" and imported.name == "decorators":
                scope[name] = "bifrost.decorators"
            elif module == "bifrost.decorators" and imported.name in _DECORATORS:
                scope[name] = f"bifrost.decorators.{imported.name}"
            else:
                scope[name] = None

    @staticmethod
    def _bind_assignment(
        statement: ast.Assign | ast.AnnAssign, scope: dict[str, str | None],
    ) -> None:
        """Keep direct decorator aliases without trying to evaluate expressions."""
        value = statement.value
        binding = scope.get(value.id) if isinstance(value, ast.Name) else None
        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
        for target in targets:
            if isinstance(target, ast.Name):
                scope[target.id] = binding

    @staticmethod
    def _shadow_bound_names(statement: ast.stmt, scope: dict[str, str | None]) -> None:
        for node in ast.walk(statement):
            if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                scope[node.id] = None

    @staticmethod
    def _shadow_target(target: ast.expr, scope: dict[str, str | None]) -> None:
        for node in ast.walk(target):
            if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                scope[node.id] = None

    def _validate_decorators(self, decorators: list[ast.expr], bindings: dict[str, str | None]) -> None:
        for decorator in decorators:
            call = decorator if isinstance(decorator, ast.Call) else None
            target = call.func if call is not None else decorator
            decorator_name = self._decorator_name(target, bindings)
            if decorator_name is None or call is None:
                continue
            self._validate_call(decorator_name, call)

    @staticmethod
    def _decorator_name(node: ast.expr, bindings: dict[str, str | None]) -> str | None:
        if isinstance(node, ast.Name):
            target = bindings.get(node.id)
        elif isinstance(node, ast.Attribute):
            chain = _attribute_chain(node)
            if chain is None:
                return None
            root, *attributes = chain
            bound = bindings.get(root)
            target = ".".join([bound, *attributes]) if bound else None
        else:
            return None
        if target is None:
            return None
        parts = target.split(".")
        if parts[:1] != ["bifrost"] or parts[-1] not in _DECORATORS:
            return None
        if parts in (["bifrost", parts[-1]], ["bifrost", "decorators", parts[-1]]):
            return parts[-1]
        return None

    def _validate_call(self, decorator: str, call: ast.Call) -> None:
        for keyword in call.keywords:
            if keyword.arg is not None:
                self._validate_key(decorator, keyword.arg, keyword)
                continue
            keys = _literal_mapping_keys(keyword.value)
            if keys is None:
                self.issues.append(DefinitionIssue(
                    path=self.path,
                    location=(decorator,),
                    code="dynamic_decorator_expansion",
                    message=(
                        f"@{decorator} **options must use a literal mapping with explicit "
                        "string keys so Bifrost can validate its settings."
                    ),
                    line=keyword.lineno,
                ))
                continue
            for key in keys:
                self._validate_key(decorator, key, keyword)

    def _validate_key(self, decorator: str, key: str, node: ast.keyword) -> None:
        if key in _SUPPORTED_KEYS[decorator]:
            return
        if key in _REMOVED_KEYS:
            message = f"@{decorator} no longer accepts '{key}'. {_REMOVED_KEYS[key]}"
        else:
            allowed = ", ".join(sorted(_SUPPORTED_KEYS[decorator]))
            message = (
                f"@{decorator} does not support '{key}'. Allowed settings: {allowed}. "
                "Configure runtime settings through the platform UI or API."
            )
        self.issues.append(DefinitionIssue(
            path=self.path,
            location=(decorator, key),
            code="unsupported_decorator_setting",
            message=message,
            line=node.lineno,
        ))


def _attribute_chain(node: ast.Attribute) -> list[str] | None:
    parts = [node.attr]
    value: ast.expr = node.value
    while isinstance(value, ast.Attribute):
        parts.append(value.attr)
        value = value.value
    if not isinstance(value, ast.Name):
        return None
    parts.append(value.id)
    return list(reversed(parts))


def _literal_mapping_keys(node: ast.expr) -> list[str] | None:
    if not isinstance(node, ast.Dict):
        return None
    keys: list[str] = []
    for key, value in zip(node.keys, node.values, strict=True):
        if key is None:
            # ``{**{'name': value}}`` is just as statically knowable as a
            # direct literal mapping, and needs no source execution.
            nested = _literal_mapping_keys(value)
            if nested is None:
                return None
            keys.extend(nested)
            continue
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            return None
        keys.append(key.value)
    return keys
