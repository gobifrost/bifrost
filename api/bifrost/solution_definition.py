"""Strict, typed reader for Solution workspace definitions.

Solutions use the same entity models as workspace manifests.  Their config
declarations and package-only app transport fields are the small, explicit
exceptions.  Keeping those differences here prevents each ZIP, CLI, and
connected-Git adapter from parsing YAML and curating its own field list.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from bifrost.definition_validation import (
    DefinitionIssue,
    DefinitionValidationError,
    read_definition_section,
    read_manifest_document,
    read_workspace_manifest,
    validate_definition,
)
from bifrost.manifest import (
    MANIFEST_FILES,
    Manifest,
    ManifestSolutionConfigSchema,
)
from bifrost.manifest_codec import Destination
from bifrost.solution_descriptor import DESCRIPTOR_FILENAME, SolutionDescriptor, load_descriptor


class SolutionAppTransport(BaseModel):
    """Package-only app build data, separate from common app definition fields."""

    model_config = ConfigDict(extra="forbid")

    # Install packages carry this in place of the workspace manifest's ``path``.
    repo_path: str | None = None
    logo_b64: str | None = None
    logo_content_type: str | None = None
    src_files: dict[str, str] | None = None
    bin_files: dict[str, str] | None = None
    dist_files: dict[str, str] | None = None
    bin_dist_files: dict[str, str] | None = None
    prebuilt_sdk_metadata: dict[str, Any] | None = None


class SolutionFormTransport(BaseModel):
    """Legacy package fields normalized into the shared inline form schema."""

    model_config = ConfigDict(extra="forbid")

    workflow_path: str | None = None
    workflow_function_name: str | None = None


class SolutionAgentTransport(BaseModel):
    """Deploy-only agent setting that is not portable manifest content."""

    model_config = ConfigDict(extra="forbid")

    max_run_timeout: int | None = None


class SolutionWorkflowTransport(BaseModel):
    """Bundled source is transport; workflow metadata stays in ManifestWorkflow."""

    model_config = ConfigDict(extra="forbid")

    source: str | None = None


class SolutionEventScheduleTransport(BaseModel):
    """Legacy event API schedule payload projected into manifest fields."""

    model_config = ConfigDict(extra="forbid")

    cron_expression: str = Field(
        validation_alias=AliasChoices("cron_expression", "cron")
    )
    timezone: str = "UTC"
    enabled: bool = True
    overlap_policy: str = "skip"


class SolutionConnectionSchema(BaseModel):
    """Secret-free integration declaration carried by a Solution package."""

    model_config = ConfigDict(extra="forbid")

    integration_name: str | None = None
    template: dict[str, Any] = Field(default_factory=dict)
    position: int = 0


class _ConfigSchemasDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    configs: dict[str, ManifestSolutionConfigSchema] = Field(default_factory=dict)


class _ConnectionsDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connections: dict[str, SolutionConnectionSchema] = Field(default_factory=dict)


@dataclass(frozen=True)
class SolutionDefinition:
    """Validated descriptor and manifest content for one workspace."""

    descriptor: SolutionDescriptor | None
    manifest: Manifest
    config_schemas: dict[str, ManifestSolutionConfigSchema]
    connection_schemas: dict[str, SolutionConnectionSchema]
    app_transports: dict[str, SolutionAppTransport]
    form_transports: dict[str, SolutionFormTransport]
    agent_transports: dict[str, SolutionAgentTransport]
    workflow_transports: dict[str, SolutionWorkflowTransport]

    def entries(self, section: str) -> list[dict[str, Any]]:
        return self._entries(section)

    def install_entries(self, section: str) -> list[dict[str, Any]]:
        """Return common entities through their canonical install projection."""
        return self._entries(section, destination=Destination.INSTALL)

    def _entries(
        self, section: str, *, destination: Destination | None = None
    ) -> list[dict[str, Any]]:
        values = getattr(self.manifest, section)
        if not isinstance(values, dict):
            raise ValueError(f"{section} is not an entity mapping")
        entries: list[dict[str, Any]] = []
        for value in values.values():
            source = value.model_dump(mode="json", by_alias=True, exclude_unset=True)
            if destination is None:
                entries.append(source)
                continue
            # The codec decides which entity fields cross the install boundary.
            # Use the source dump for those selected fields to retain an input's
            # fields_set shape inside nested open/content objects; the codec
            # supplies documented defaults that old collectors materialized.
            projected = value.view(destination)
            entries.append({
                key: source.get(key, default)
                for key, default in projected.items()
            })
        if section == "apps":
            for entry in entries:
                transport = self.app_transports.get(str(entry["id"]))
                if transport is not None:
                    entry.update(transport.model_dump(mode="json", exclude_none=True))
        if section == "forms":
            for entry in entries:
                transport = self.form_transports.get(str(entry["id"]))
                if transport is not None:
                    entry.update(transport.model_dump(mode="json", exclude_none=True))
        if section == "agents":
            for entry in entries:
                transport = self.agent_transports.get(str(entry["id"]))
                if transport is not None:
                    entry.update(transport.model_dump(mode="json", exclude_none=True))
        if section == "workflows":
            for entry in entries:
                transport = self.workflow_transports.get(str(entry["id"]))
                if transport is not None:
                    entry.update(transport.model_dump(mode="json", exclude_none=True))
        return entries

    def config_entries(self) -> list[dict[str, Any]]:
        return [
            # The previous config collector always sent these declaration
            # defaults.  Unlike common entity entries, absence here means the
            # declared default, not "leave the deployed value alone".
            value.model_dump(mode="json")
            for value in self.config_schemas.values()
        ]

    def connection_entries(self) -> list[dict[str, Any]]:
        return [
            # Connection declarations likewise always supplied template and
            # position defaults to the reconciler.
            value.model_dump(mode="json")
            for value in self.connection_schemas.values()
        ]


_DEFAULT_KEY_FIELDS: dict[str, tuple[str, ...]] = {
    "workflows": ("id", "name"),
    "tables": ("id", "name"),
    "apps": ("id", "slug", "name"),
    "forms": ("id", "name"),
    "agents": ("id", "name"),
    "claims": ("id", "name"),
    "configs": ("id", "key"),
    "file_policies": ("id",),
    "events": ("id", "name"),
}

_EMPTY_VALUE_KEY_DEFAULTS: dict[str, tuple[str, ...]] = {
    "workflows": ("name",),
    "tables": ("name",),
    "apps": ("slug", "name"),
    "claims": ("name",),
    "configs": ("key",),
}


def _path(workspace: Path, *parts: str) -> Path | None:
    root = os.path.realpath(workspace)
    target = os.path.realpath(os.path.join(root, *parts))
    if not target.startswith(root + os.sep):
        return None
    return Path(target)


def _with_mapping_defaults(data: dict[str, Any], section: str) -> dict[str, Any]:
    """Apply documented map-key identity defaults before shared model parsing."""
    raw = data.get(section, {})
    if not isinstance(raw, dict):
        return data
    normalized: dict[Any, Any] = {}
    for key, body in raw.items():
        if not isinstance(body, dict):
            normalized[key] = body
            continue
        entry = dict(body)
        for field in _DEFAULT_KEY_FIELDS.get(section, ()):
            entry.setdefault(field, key)
        for field in _EMPTY_VALUE_KEY_DEFAULTS.get(section, ()):
            entry[field] = entry.get(field) or key
        if section == "file_policies":
            entry.setdefault("organization_id", None)
            entry.setdefault("solution_id", None)
        normalized[key] = entry
    return {**data, section: normalized}


def _read_solution_section(filename: str, content: str) -> dict[str, Any]:
    """Decode a Solution manifest file before any adapter projects its fields."""
    document = read_manifest_document(filename, content)
    path = document.path
    data = document.data
    section = next((key for key, value in MANIFEST_FILES.items() if value == filename), None)
    if section == "files":
        return {"files": read_workspace_manifest(filename, content).files}
    if section not in _DEFAULT_KEY_FIELDS and section != "apps":
        # Solution consumers do not project this workspace-only section, but it
        # remains an accepted manifest file and must receive the workspace reader's
        # identical strict validation before it can be ignored here.
        read_workspace_manifest(filename, content)
        return {}
    app_transports: dict[str, SolutionAppTransport] = {}
    form_transports: dict[str, SolutionFormTransport] = {}
    agent_transports: dict[str, SolutionAgentTransport] = {}
    workflow_transports: dict[str, SolutionWorkflowTransport] = {}
    if section == "apps" and isinstance(data.get("apps"), dict):
        normalized_apps: dict[Any, Any] = {}
        for key, body in data["apps"].items():
            if not isinstance(body, dict):
                normalized_apps[key] = body
                continue
            base = dict(body)
            transport_data = {
                field: base.pop(field)
                for field in SolutionAppTransport.model_fields
                if field in base
            }
            app_transports[str(base.get("id", key))] = validate_definition(
                SolutionAppTransport, transport_data, path=path
            )
            if "path" not in base and transport_data.get("repo_path") is not None:
                base["path"] = transport_data["repo_path"]
            normalized_apps[key] = base
        data = {**data, "apps": normalized_apps}
    if section == "workflows" and isinstance(data.get("workflows"), dict):
        normalized_workflows: dict[Any, Any] = {}
        for key, body in data["workflows"].items():
            if not isinstance(body, dict):
                normalized_workflows[key] = body
                continue
            base = dict(body)
            if "content" in base:
                if "source" in base:
                    raise DefinitionValidationError([DefinitionIssue(
                        path,
                        ("workflows", str(key), "content"),
                        "conflicting_source",
                        "Specify either source or content, not both",
                    )])
                base["source"] = base.pop("content")
            transport_data = {
                field: base.pop(field)
                for field in SolutionWorkflowTransport.model_fields
                if field in base
            }
            workflow_transports[str(base.get("id", key))] = validate_definition(
                SolutionWorkflowTransport, transport_data, path=path
            )
            normalized_workflows[key] = base
        data = {**data, "workflows": normalized_workflows}
    if section == "events" and isinstance(data.get("events"), dict):
        normalized_events: dict[Any, Any] = {}
        for key, body in data["events"].items():
            if not isinstance(body, dict):
                normalized_events[key] = body
                continue
            base = dict(body)
            if "schedule" in base:
                schedule = validate_definition(
                    SolutionEventScheduleTransport,
                    base.pop("schedule"),
                    path=path,
                )
                projected = {
                    "cron_expression": schedule.cron_expression,
                    "timezone": schedule.timezone,
                    "schedule_enabled": schedule.enabled,
                    "overlap_policy": schedule.overlap_policy,
                }
                duplicates = set(base).intersection(projected)
                if duplicates:
                    field = sorted(duplicates)[0]
                    raise DefinitionValidationError([DefinitionIssue(
                        path,
                        ("events", str(key), field),
                        "conflicting_schedule",
                        "Specify either flat schedule fields or schedule, not both",
                    )])
                base.update(projected)
            normalized_events[key] = base
        data = {**data, "events": normalized_events}
    if section == "forms" and isinstance(data.get("forms"), dict):
        normalized_forms: dict[Any, Any] = {}
        for key, body in data["forms"].items():
            if not isinstance(body, dict):
                normalized_forms[key] = body
                continue
            base = dict(body)
            transport_data = {
                field: base.pop(field)
                for field in SolutionFormTransport.model_fields
                if field in base
            }
            form_transports[str(base.get("id", key))] = validate_definition(
                SolutionFormTransport, transport_data, path=path
            )
            normalized_forms[key] = base
        data = {**data, "forms": normalized_forms}
    if section == "agents" and isinstance(data.get("agents"), dict):
        normalized_agents: dict[Any, Any] = {}
        for key, body in data["agents"].items():
            if not isinstance(body, dict):
                normalized_agents[key] = body
                continue
            base = dict(body)
            transport_data = {
                field: base.pop(field)
                for field in SolutionAgentTransport.model_fields
                if field in base
            }
            agent_transports[str(base.get("id", key))] = validate_definition(
                SolutionAgentTransport, transport_data, path=path
            )
            normalized_agents[key] = base
        data = {**data, "agents": normalized_agents}
    normalized = _with_mapping_defaults(data, section)
    validated = validate_definition(Manifest, normalized, path=path)
    out: dict[str, Any] = {section: getattr(validated, section)}
    if app_transports:
        out["app_transports"] = app_transports
    if form_transports:
        out["form_transports"] = form_transports
    if agent_transports:
        out["agent_transports"] = agent_transports
    if workflow_transports:
        out["workflow_transports"] = workflow_transports
    return out


def _read_config_schemas(content: str) -> dict[str, ManifestSolutionConfigSchema]:
    document = read_definition_section(
        content, path=".bifrost/configs.yaml", section="configs"
    )
    path = document.path
    data = document.data
    normalized = _with_mapping_defaults(data, "configs")

    return validate_definition(_ConfigSchemasDocument, normalized, path=path).configs


def _read_connection_schemas(content: str) -> dict[str, SolutionConnectionSchema]:
    document = read_definition_section(
        content, path=".bifrost/connections.yaml", section="connections"
    )
    path = document.path
    data = document.data
    raw = data.get("connections", {})
    if isinstance(raw, dict):
        data = {
            "connections": {
                key: ({"integration_name": key, **body} if isinstance(body, dict) else body)
                for key, body in raw.items()
            }
        }

    return validate_definition(_ConnectionsDocument, data, path=path).connections


def read_solution_definition(workspace: Path | str) -> SolutionDefinition:
    """Read all present Solution manifest files through one strict boundary."""
    root = Path(workspace)
    descriptor_path = _path(root, DESCRIPTOR_FILENAME)
    descriptor = load_descriptor(root) if descriptor_path is not None and descriptor_path.is_file() else None
    merged: dict[str, Any] = {}
    app_transports: dict[str, SolutionAppTransport] = {}
    form_transports: dict[str, SolutionFormTransport] = {}
    agent_transports: dict[str, SolutionAgentTransport] = {}
    workflow_transports: dict[str, SolutionWorkflowTransport] = {}
    config_schemas: dict[str, ManifestSolutionConfigSchema] = {}
    for section, filename in MANIFEST_FILES.items():
        path = _path(root, ".bifrost", filename)
        if path is None or not path.is_file():
            continue
        content = path.read_text(encoding="utf-8")
        if section == "configs":
            config_schemas = _read_config_schemas(content)
            continue
        decoded = _read_solution_section(filename, content)
        if section in decoded:
            merged.update(decoded)
        app_transports.update(decoded.get("app_transports", {}))
        form_transports.update(decoded.get("form_transports", {}))
        agent_transports.update(decoded.get("agent_transports", {}))
        workflow_transports.update(decoded.get("workflow_transports", {}))
    connections_path = _path(root, ".bifrost", "connections.yaml")
    connection_schemas = (
        _read_connection_schemas(connections_path.read_text(encoding="utf-8"))
        if connections_path is not None and connections_path.is_file()
        else {}
    )
    return SolutionDefinition(
        descriptor=descriptor,
        manifest=Manifest(**merged),
        config_schemas=config_schemas,
        connection_schemas=connection_schemas,
        app_transports=app_transports,
        form_transports=form_transports,
        agent_transports=agent_transports,
        workflow_transports=workflow_transports,
    )
