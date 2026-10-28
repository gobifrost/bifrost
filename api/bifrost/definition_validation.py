"""Spike: strict definition acceptance without changing execution-time parsing.

This module is exercised by the spike tests. It is not wired into production
write, import or deployment paths yet.
"""

from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass
from typing import TypeVar

import yaml
from pydantic import BaseModel, ValidationError
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from bifrost import decorators
from bifrost.manifest import MANIFEST_FILES, MANIFEST_LEGACY_FILE, Manifest

ModelT = TypeVar("ModelT", bound=BaseModel)


@dataclass(frozen=True)
class DefinitionIssue:
    path: str
    location: tuple[str | int, ...]
    code: str
    message: str
    line: int | None = None


class DefinitionValidationError(ValueError):
    def __init__(self, issues: list[DefinitionIssue]):
        self.issues = issues
        super().__init__("; ".join(
            f"{issue.path}:{'.'.join(map(str, issue.location))}: {issue.message}"
            for issue in issues
        ))


def validate_definition(model: type[ModelT], data: object, *, path: str) -> ModelT:
    """Reuse the real schema, recursively forbidding unknown object fields."""
    try:
        return model.model_validate(data, extra="forbid")
    except ValidationError as exc:
        raise DefinitionValidationError([
            DefinitionIssue(
                path=path,
                location=tuple(error["loc"]),
                code=error["type"],
                message=error["msg"],
            )
            for error in exc.errors(include_url=False, include_input=False)
        ]) from None


def _reject_duplicate_keys(root: Node, path: str) -> None:
    # Inspect explicit keys before SafeLoader expands merges or overwrites a
    # duplicate. Alias nodes can be shared or cyclic, so visit each node once.
    pending = [root]
    visited: set[int] = set()
    while pending:
        node = pending.pop()
        if id(node) in visited:
            continue
        visited.add(id(node))
        if isinstance(node, MappingNode):
            seen: set[tuple[str, str]] = set()
            for key, value in node.value:
                if isinstance(key, ScalarNode):
                    identity = (key.tag, key.value)
                    if identity in seen:
                        raise DefinitionValidationError([DefinitionIssue(
                            path, (key.value,), "duplicate_key",
                            "Duplicate YAML key", key.start_mark.line + 1,
                        )])
                    seen.add(identity)
                pending.extend((key, value))
        elif isinstance(node, SequenceNode):
            pending.extend(node.value)


def read_yaml_mapping(content: str, *, path: str) -> dict:
    """Reject malformed document shapes and duplicate keys before projection."""
    try:
        root = yaml.compose(content, Loader=yaml.SafeLoader)
        if root is None:
            return {}
        _reject_duplicate_keys(root, path)
        data = yaml.safe_load(content)
    except yaml.MarkedYAMLError as exc:
        raise DefinitionValidationError([DefinitionIssue(
            path, (), "invalid_yaml", exc.problem or "Invalid YAML",
            exc.problem_mark.line + 1 if exc.problem_mark is not None else None,
        )]) from None
    if not isinstance(data, dict):
        raise DefinitionValidationError([DefinitionIssue(
            path, (), "mapping_required", "A definition must contain a YAML mapping",
        )])
    return data


def read_workspace_manifest(filename: str, content: str) -> Manifest:
    """Prototype the common raw acceptance boundary for full/split manifests."""
    path = f".bifrost/{filename}"
    data = read_yaml_mapping(content, path=path)
    if filename == MANIFEST_LEGACY_FILE:
        return validate_definition(Manifest, data, path=path)
    sections = {value: key for key, value in MANIFEST_FILES.items()}
    if filename not in sections:
        raise DefinitionValidationError([DefinitionIssue(
            path, (), "unknown_manifest", "Unknown workspace manifest file",
        )])
    section = sections[filename]
    if section == "files":
        data = {section: data}
    else:
        unexpected = set(data) - {section}
        if unexpected:
            raise DefinitionValidationError([
                DefinitionIssue(path, (str(key),), "unknown_section",
                                f"Expected only the {section!r} section")
                for key in sorted(unexpected, key=str)
            ])
    return validate_definition(Manifest, data, path=path)


def _bound_name(node: ast.expr, bindings: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return bindings.get(node.id)
    if isinstance(node, ast.Attribute):
        parent = _bound_name(node.value, bindings)
        if parent is not None:
            return f"{parent}.{node.attr}"
    return None


def validate_decorator_keywords(source: str, *, path: str) -> list[DefinitionIssue]:
    """Check directly imported Bifrost decorators without executing source.

    The spike handles import aliases and literal ** mappings. Production work
    must settle local rebinding, star imports and indirect decorator factories.
    """
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        return [DefinitionIssue(path, (), "syntax_error", exc.msg, exc.lineno)]
    bindings: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in {"bifrost", "bifrost.decorators"}:
                    bindings[alias.asname or "bifrost"] = (
                        alias.name if alias.asname else "bifrost"
                    )
        elif isinstance(node, ast.ImportFrom) and node.module in {
            "bifrost", "bifrost.decorators",
        }:
            for alias in node.names:
                bindings[alias.asname or alias.name] = f"{node.module}.{alias.name}"

    supported = {
        name: {
            parameter.name
            for parameter in inspect.signature(getattr(decorators, name)).parameters.values()
            if parameter.kind == inspect.Parameter.KEYWORD_ONLY
        }
        for name in ("workflow", "tool", "data_provider", "service")
    }
    issues: list[DefinitionIssue] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            bound = _bound_name(decorator.func, bindings)
            decorator_name = next((
                name for name in supported
                if bound in {f"bifrost.{name}", f"bifrost.decorators.{name}"}
            ), None)
            if decorator_name is None:
                continue
            for keyword in decorator.keywords:
                names: list[str] = []
                if keyword.arg is not None:
                    names.append(keyword.arg)
                elif isinstance(keyword.value, ast.Dict) and all(
                    isinstance(key, ast.Constant) and isinstance(key.value, str)
                    for key in keyword.value.keys
                ):
                    names.extend(key.value for key in keyword.value.keys
                                 if isinstance(key, ast.Constant))
                else:
                    issues.append(DefinitionIssue(
                        path, (node.name, decorator_name), "unverifiable_keywords",
                        "Use explicit decorator keywords or a literal string-keyed mapping",
                        keyword.value.lineno,
                    ))
                for name in names:
                    if name not in supported[decorator_name]:
                        issues.append(DefinitionIssue(
                            path, (node.name, decorator_name, name), "unknown_keyword",
                            f"Unsupported @{decorator_name} keyword {name!r}",
                            keyword.value.lineno,
                        ))
    return issues
