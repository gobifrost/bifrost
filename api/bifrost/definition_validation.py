"""Strict, safe decoding for workspace definition files.

The manifest models deliberately remain permissive when callers instantiate
them directly.  Workspace files, however, are an external acceptance boundary:
they must reject fields that no schema owns before Pydantic can discard them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeVar

import yaml
from pydantic import BaseModel, ValidationError
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode

if TYPE_CHECKING:
    from bifrost.manifest import Manifest


ModelT = TypeVar("ModelT", bound=BaseModel)


@dataclass(frozen=True)
class DefinitionIssue:
    """One source-safe diagnostic from workspace definition decoding."""

    path: str
    location: tuple[str | int, ...]
    code: str
    message: str
    line: int | None = None


class DefinitionValidationError(ValueError):
    """A collection of actionable, source-safe definition diagnostics."""

    def __init__(self, issues: list[DefinitionIssue]):
        self.issues = issues
        super().__init__("; ".join(
            "".join((
                issue.path,
                f":{'.'.join(map(str, issue.location))}" if issue.location else "",
                f":line {issue.line}" if issue.line is not None else "",
                f": {issue.message}",
            ))
            for issue in issues
        ))


@dataclass(frozen=True)
class DefinitionDocument:
    """One safely decoded definition document before model projection."""

    path: str
    data: dict


class _DuplicateYamlKeyError(Exception):
    def __init__(self, line: int):
        self.line = line


class _DuplicateKeySafeLoader(yaml.SafeLoader):
    """SafeLoader that rejects explicit duplicate keys before merge expansion."""

    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict:
        seen: set[object] = set()
        for key_node, _ in node.value:
            # YAML merge entries are intentionally exempt: explicit keys are
            # permitted to override a mapping brought in through ``<<``.
            if key_node.tag == "tag:yaml.org,2002:merge":
                continue
            key = self.construct_object(key_node, deep=False)
            try:
                if key in seen:
                    raise _DuplicateYamlKeyError(key_node.start_mark.line + 1)
                seen.add(key)
            except TypeError as exc:
                raise ConstructorError(
                    None,
                    None,
                    "found unhashable key",
                    key_node.start_mark,
                ) from exc
        return super().construct_mapping(node, deep=deep)


def validate_definition(model: type[ModelT], data: object, *, path: str) -> ModelT:
    """Validate one accepted definition with unknown object fields forbidden."""
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


def read_yaml_mapping(content: str, *, path: str) -> dict:
    """Decode one YAML mapping with SafeLoader and duplicate-key rejection."""
    loader = _DuplicateKeySafeLoader(content)
    try:
        node = loader.get_single_node()
        if node is None:
            return {}
        data = loader.construct_document(node)
    except _DuplicateYamlKeyError as exc:
        raise DefinitionValidationError([
            DefinitionIssue(
                path,
                (),
                "duplicate_key",
                "Duplicate YAML key",
                exc.line,
            )
        ]) from None
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None) or getattr(exc, "context_mark", None)
        raise DefinitionValidationError([
            DefinitionIssue(
                path,
                (),
                "invalid_yaml",
                "Invalid YAML document",
                mark.line + 1 if mark is not None else None,
            )
        ]) from None
    finally:
        loader.dispose()
    if not isinstance(data, dict):
        raise DefinitionValidationError([
            DefinitionIssue(
                path,
                (),
                "mapping_required",
                "A definition must contain a YAML mapping",
            )
        ])
    return data


def read_definition_section(
    content: str,
    *,
    path: str,
    section: str,
    direct: bool = False,
) -> DefinitionDocument:
    """Decode a blank-tolerant definition document and verify its wrapper.

    Workspace manifests and Solution-only declaration documents use this same
    boundary. ``direct`` is for documents like ``files.yaml`` whose fields are
    the payload rather than nested below a top-level section name.
    """
    if not content or not content.strip():
        return DefinitionDocument(path=path, data={})
    data = read_yaml_mapping(content, path=path)
    if direct:
        return DefinitionDocument(path=path, data=data)
    unexpected = set(data) - {section}
    if unexpected:
        raise DefinitionValidationError([
            DefinitionIssue(
                path,
                (str(key),),
                "unknown_section",
                "Split manifest file contains an unexpected section",
            )
            for key in sorted(
                unexpected,
                key=lambda value: (type(value).__name__, str(value)),
            )
        ])
    return DefinitionDocument(path=path, data=data)


def read_manifest_document(filename: str, content: str) -> DefinitionDocument:
    """Decode one legacy or split workspace manifest before entity validation."""
    from bifrost.manifest import MANIFEST_FILES, MANIFEST_LEGACY_FILE

    path = f".bifrost/{filename}"
    if filename == MANIFEST_LEGACY_FILE:
        if not content or not content.strip():
            return DefinitionDocument(path=path, data={})
        return DefinitionDocument(path=path, data=read_yaml_mapping(content, path=path))

    sections = {
        manifest_filename: section
        for section, manifest_filename in MANIFEST_FILES.items()
    }
    section = sections.get(filename)
    if section is None:
        raise DefinitionValidationError([
            DefinitionIssue(path, (), "unknown_manifest", "Unknown workspace manifest file")
        ])
    return read_definition_section(
        content,
        path=path,
        section=section,
        direct=section == "files",
    )


def read_workspace_manifest(filename: str, content: str) -> "Manifest":
    """Decode one legacy or split workspace manifest file strictly."""
    # Local imports keep this low-level decoder independent from manifest models.
    from bifrost.manifest import MANIFEST_FILES, MANIFEST_LEGACY_FILE, Manifest

    document = read_manifest_document(filename, content)
    path = document.path
    data = document.data
    if filename == MANIFEST_LEGACY_FILE:
        return validate_definition(Manifest, data, path=path)

    sections = {
        manifest_filename: section
        for section, manifest_filename in MANIFEST_FILES.items()
    }
    section = sections.get(filename)
    if section == "files":
        return validate_definition(Manifest, {section: data}, path=path)
    payload = {section: data[section]} if section in data else {}
    return validate_definition(Manifest, payload, path=path)
