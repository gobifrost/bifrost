"""Normalize an install-format Solution package into a workspace-import plan.

Install packages are intentionally not manifests: notably their config entries
are declarations, not ``ManifestConfig`` values. Keeping that conversion here
prevents the install deployer shape from leaking into workspace reconciliation.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bifrost.manifest import (
    Manifest,
    ManifestAgent,
    ManifestApp,
    ManifestConfig,
    ManifestCustomClaim,
    ManifestEventSource,
    ManifestFilePolicy,
    ManifestForm,
    ManifestPolicy,
    ManifestTable,
    ManifestWorkflow,
)
from bifrost.ignore_patterns import DEFAULT_IGNORE_PATTERNS
from shared.file_policies_seed import make_seed_admin_bypass_file
from shared.policies.probe import make_seed_admin_bypass
from src.services.solutions.file_locations import normalize_file_locations
from src.models.contracts.solutions import WorkspaceBundleItem, WorkspaceBundlePreview
from src.services.git_repo_manager import hash_file, iter_repo_files
from src.services.solutions.zip_install import PreviewResult

_ID_NAMESPACE = UUID("f4b9f7ce-b035-48b8-bfdd-d18a02e34731")
_FILE_LOOKUP_BATCH_SIZE = 100
_REFERENCE_FIELDS = frozenset({
    "workflow_id", "launch_workflow_id", "agent_id", "tool_ids",
    "delegated_agent_ids", "mcp_connection_ids", "webhook_integration_id",
    "data_provider_id", "default_entity_id", "list_entities_data_provider_id",
})


def _has_config_value(value: object) -> bool:
    if isinstance(value, dict) and "value" in value:
        value = value["value"]
    return value is not None and value != {} and (not isinstance(value, str) or bool(value.strip()))


@lru_cache(maxsize=1)
def _workspace_source_ignore_spec():
    import pathspec

    return pathspec.PathSpec.from_lines("gitwildmatch", DEFAULT_IGNORE_PATTERNS)


def _is_importable_source_file(relative: str) -> bool:
    """Apply the shared Solution/CLI secret and generated-content exclusions."""
    return not _workspace_source_ignore_spec().match_file(relative)


def _uuid(value: object, *, preview_id: UUID, stable_key: str) -> UUID:
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return uuid5(preview_id, stable_key)


def _entry(
    entry: dict[str, Any], *, preview_id: UUID, stable_key: str,
    organization_id: UUID | None = None,
) -> dict[str, Any]:
    result = dict(entry)
    result["id"] = str(_uuid(result.get("id"), preview_id=preview_id, stable_key=stable_key))
    # A bundle describes an install's binding. Workspace import restamps it to
    # the chosen target scope (global null by default) before any manifest
    # model validates the entity.
    # Raw role UUIDs are source-env-specific and go; portable role_names stay
    # so the import can re-bind (auto-creating missing global roles).
    result["organization_id"] = str(organization_id) if organization_id is not None else None
    result.pop("solution_id", None)
    result.pop("roles", None)
    return result


@dataclass(frozen=True)
class SolutionPackageWorkspaceProjection:
    """Projection from package/install to workspace scope."""

    manifest: Manifest
    package_name: str
    work_dir: Path | None = None
    # Raw connection declarations become never-clobber global integration shells.
    connection_schemas: tuple[dict[str, Any], ...] = ()
    config_schemas: tuple[dict[str, Any], ...] = ()

    @classmethod
    def from_preview(
        cls, package: PreviewResult, *, preview_id: UUID, work_dir: Path | None = None,
        organization_id: UUID | None = None,
    ) -> "SolutionPackageWorkspaceProjection":
        workflows: dict[str, ManifestWorkflow] = {}
        for row in package.workflows:
            data = _entry(row, preview_id=preview_id, organization_id=organization_id, stable_key=f"workflow:{row.get('path')}:{row.get('function_name')}")
            data.setdefault("name", data["id"])
            workflows[data["id"]] = ManifestWorkflow.model_validate(data)

        apps: dict[str, ManifestApp] = {}
        for row in package.apps:
            data = _entry(row, preview_id=preview_id, organization_id=organization_id, stable_key=f"app:{row.get('slug') or row.get('path')}")
            apps[data["id"]] = ManifestApp.model_validate(data)

        tables: dict[str, ManifestTable] = {}
        for row in package.tables:
            data = _entry(row, preview_id=preview_id, organization_id=organization_id, stable_key=f"table:{row.get('name')}")
            data.setdefault("name", data["id"])
            tables[data["id"]] = ManifestTable.model_validate(data)

        forms: dict[str, ManifestForm] = {}
        for row in package.forms:
            data = _entry(row, preview_id=preview_id, organization_id=organization_id, stable_key=f"form:{row.get('name') or row.get('id')}")
            forms[data["id"]] = ManifestForm.model_validate(data)

        agents: dict[str, ManifestAgent] = {}
        for row in package.agents:
            data = _entry(row, preview_id=preview_id, organization_id=organization_id, stable_key=f"agent:{row.get('name') or row.get('id')}")
            agents[data["id"]] = ManifestAgent.model_validate(data)

        events: dict[str, ManifestEventSource] = {}
        for row in package.events:
            data = _entry(row, preview_id=preview_id, organization_id=organization_id, stable_key=f"event:{row.get('name')}")
            events[data["id"]] = ManifestEventSource.model_validate(data)

        file_policies: dict[str, ManifestFilePolicy] = {}
        for row in package.file_policies:
            data = _entry(row, preview_id=preview_id, organization_id=organization_id, stable_key=f"file-policy:{row.get('location')}:{row.get('path')}")
            file_policies[data["id"]] = ManifestFilePolicy.model_validate(data)

        # A workspace share exists as soon as it has a root policy. Give each
        # declared location the same visible, revocable admin seed policy that
        # an install receives, unless the package supplies an explicit root.
        declared_locations = normalize_file_locations(package.file_locations)
        seeded_policies = make_seed_admin_bypass_file()["policies"]
        for location in declared_locations:
            if any(policy.location == location and policy.path == "" for policy in file_policies.values()):
                continue
            policy_id = uuid5(_ID_NAMESPACE, f"file-location:{organization_id}:{location}")
            file_policies[str(policy_id)] = ManifestFilePolicy(
                id=str(policy_id), organization_id=str(organization_id) if organization_id else None,
                location=location, path="", policies=seeded_policies,
            )

        claims: dict[str, ManifestCustomClaim] = {}
        for row in package.claims:
            data = _entry(row, preview_id=preview_id, organization_id=organization_id, stable_key=f"claim:{row.get('name')}")
            data.setdefault("name", data["id"])
            claims[data["id"]] = ManifestCustomClaim.model_validate(data)

        # This is intentionally *not* ``ManifestConfig.model_validate(row)``.
        # A Solution schema has type/default while a workspace config has
        # config_type/value. Declaration metadata carries through separately.
        configs: dict[str, ManifestConfig] = {}
        for row in package.config_schemas:
            key = str(row.get("key") or row.get("id"))
            config_id = _uuid(row.get("id"), preview_id=preview_id, stable_key=f"config:{key}")
            configs[key] = ManifestConfig(
                id=str(config_id), key=key, config_type=str(row.get("type", "string")),
                description=row.get("description"),
                required=row.get("required") is True,
                position=int(row.get("position") or 0),
                value=None if row.get("type") == "secret" else row.get("default"),
                organization_id=str(organization_id) if organization_id else None,
                integration_id=None,
            )

        return cls(
            manifest=Manifest(workflows=workflows, apps=apps, tables=tables, forms=forms,
                              agents=agents, configs=configs, events=events,
                              file_policies=file_policies, claims=claims),
            package_name=package.name or package.slug or "Solution package",
            work_dir=work_dir,
            connection_schemas=tuple(
                dict(schema) for schema in package.connection_schemas
            ),
            config_schemas=tuple(
                {
                    "key": str(schema["key"]),
                    "type": str(schema.get("type") or "string"),
                    "required": schema.get("required") is True,
                    "description": schema.get("description"),
                }
                for schema in package.config_schemas if schema.get("key")
            ),
        )


@dataclass(frozen=True)
class PlannedWorkspaceBundle:
    preview: WorkspaceBundlePreview
    manifest: Manifest
    id_map: dict[UUID, UUID]
    work_dir: Path | None = None
    file_hashes: dict[str, str] | None = None
    destination_file_hashes: dict[str, str] | None = None
    connection_schemas: list[dict[str, Any]] | None = None
    organization_id: UUID | None = None


def _display_key(natural_key: tuple) -> str:
    """Render a natural key for the review table (``path :: function``).

    The stored lookup stays tuple-shaped; this is only the explainable string
    the reviewer sees. ``None`` padding and raw scope UUIDs never leak into
    it — the import scope is shown once at the review level instead.
    """
    parts: list[str] = []
    for part in natural_key:
        if part is None or part == "":
            continue
        try:
            UUID(str(part))
        except (TypeError, ValueError):
            parts.append(str(part))
    return " :: ".join(parts)


def _comparable_snapshot(kind: str, snapshot: dict[str, Any], *, incoming: bool) -> dict[str, Any]:
    """Compare effective definitions rather than package transport details."""
    data = dict(snapshot)
    if kind == "table" and incoming and data.get("policies") is None:
        data["policies"] = [
            ManifestPolicy.model_validate(policy).model_dump(mode="json")
            for policy in make_seed_admin_bypass()["policies"]
        ]
    if kind == "config":
        # Package defaults only initialize new configs. Existing workspace
        # values are preserved unless the reviewer explicitly enters one.
        data.pop("value", None)
    if kind == "event":
        if incoming and data.get("source_type") == "schedule" and data.get("cron_expression"):
            data["timezone"] = data.get("timezone") or "UTC"
            data["schedule_enabled"] = True if data.get("schedule_enabled") is None else data["schedule_enabled"]
            data["overlap_policy"] = data.get("overlap_policy") or "skip"
        data["subscriptions"] = sorted(
            ({key: value for key, value in sub.items() if key != "id"}
             for sub in data.get("subscriptions", [])),
            key=str,
        )
    return data


def _remap_references(value: Any, targets: dict[str, str], *, field: str | None = None) -> Any:
    if isinstance(value, str):
        return targets.get(value, value) if field in _REFERENCE_FIELDS else value
    if isinstance(value, list):
        return [_remap_references(entry, targets, field=field) for entry in value]
    if isinstance(value, dict):
        return {key: _remap_references(entry, targets, field=key) for key, entry in value.items()}
    return value


class WorkspaceBundlePlanner:
    def __init__(
        self, db: AsyncSession | None, *, preview_id: UUID,
        organization_id: UUID | None = None,
    ):
        self.db = db
        self.preview_id = preview_id
        # Target scope for scoped definitions (None = global). Files,
        # integrations, and roles are always global. Workflow paths and app
        # slugs are unique across workspace scopes, so those matches can move.
        self.organization_id = organization_id

    def _scope_clause(self, column):
        """Match this planner's target scope (exact org, or IS NULL global)."""
        if self.organization_id is None:
            return column.is_(None)
        return column == self.organization_id

    @staticmethod
    def reference_map(items: list[WorkspaceBundleItem]) -> dict[UUID, UUID]:
        """Map every selected source id, including a kept conflict, to its target."""
        return {
            item.source_id: item.target_id
            for item in items
            if item.source_id is not None and item.target_id is not None
        }

    def plan_sync(
        self,
        projection: SolutionPackageWorkspaceProjection,
        *,
        existing_file_hashes: dict[str, str | None] | None = None,
    ) -> PlannedWorkspaceBundle:
        """Plan package creation without a DB; useful for deterministic staging tests."""
        return self._build_plan(projection, {}, existing_file_hashes or {})

    async def plan(self, projection: SolutionPackageWorkspaceProjection) -> PlannedWorkspaceBundle:
        incoming_paths = self._incoming_file_paths(projection)
        return self._build_plan(
            projection, await self._prefetch_existing(projection.manifest),
            await self._prefetch_existing_file_hashes(incoming_paths),
            existing_integrations=await self._prefetch_existing_integrations(),
        )

    @staticmethod
    def _incoming_file_paths(projection: SolutionPackageWorkspaceProjection) -> Iterator[str]:
        if projection.work_dir is None:
            return iter(())

        def paths() -> Iterator[str]:
            for path in iter_repo_files(projection.work_dir):
                relative = path.relative_to(projection.work_dir).as_posix()
                if relative != "bifrost.solution.yaml" and _is_importable_source_file(relative):
                    yield relative

        return paths()

    def _build_plan(
        self,
        projection: SolutionPackageWorkspaceProjection,
        existing: dict[tuple[str, tuple], tuple[UUID, dict[str, Any], bool]],
        existing_file_hashes: dict[str, str | None],
        *,
        existing_integrations: dict[str, UUID] | None = None,
    ) -> PlannedWorkspaceBundle:
        items: list[WorkspaceBundleItem] = []
        # Group keys tie a definition to the files implementing it: a workflow
        # (or an app) and its source are always decided together, since one
        # without the other is never a working import.
        workflow_paths = {wf.path for wf in projection.manifest.workflows.values()}
        app_prefixes = {
            f"{app.path.rstrip('/')}/": f"app:{app.slug or app.path}"
            for app in projection.manifest.apps.values() if app.path
        }
        source_targets = {
            entity.id: str(
                existing[(kind, key_fn(entity))][0]
                if (kind, key_fn(entity)) in existing
                else uuid5(self.preview_id, f"{kind}:{key_fn(entity)}")
            )
            for kind, entries, key_fn in self._collections(projection.manifest)
            for entity in entries.values()
        }

        def _file_group(relative: str) -> str | None:
            if relative in workflow_paths:
                return f"file:{relative}"
            for prefix, group in app_prefixes.items():
                if relative.startswith(prefix):
                    return group
            return None

        for kind, entries, key_fn in self._collections(projection.manifest):
            for entity in entries.values():
                source = UUID(entity.id)
                natural_key = key_fn(entity)
                match = existing.get((kind, natural_key))
                target = match[0] if match else uuid5(self.preview_id, f"{kind}:{natural_key}")
                incoming = _remap_references(
                    entity.model_dump(mode="json", exclude={"id"}), source_targets,
                )
                if match and kind in {"form", "agent"}:
                    # Omitted portable access grants leave the destination's
                    # environment-owned settings alone during import.
                    for field in ("access_level", "role_names"):
                        if incoming.get(field) is None:
                            incoming[field] = match[1].get(field)
                scope_change = bool(
                    match and kind in {"workflow", "app"}
                    and match[1].get("organization_id") != incoming.get("organization_id")
                )
                classification = "create" if match is None else (
                    "unchanged" if _comparable_snapshot(kind, incoming, incoming=True) == _comparable_snapshot(kind, match[1], incoming=False) else "conflict"
                )
                group_key: str | None = None
                if kind == "workflow":
                    group_key = f"file:{natural_key[0]}"
                elif kind == "app":
                    group_key = f"app:{getattr(entity, 'slug', None) or getattr(entity, 'path', None)}"
                items.append(WorkspaceBundleItem(
                    id=f"entity:{kind}:{source}", kind=kind, name=str(getattr(entity, "name", None) or getattr(entity, "key", source)),
                    classification=classification, match_key=_display_key(natural_key), source_id=source, target_id=target,
                    group_key=group_key, scope_change=scope_change,
                ))
        file_hashes: dict[str, str] = {}
        if projection.work_dir is not None:
            for path in iter_repo_files(projection.work_dir):
                relative = path.relative_to(projection.work_dir).as_posix()
                if relative == "bifrost.solution.yaml" or not _is_importable_source_file(relative):
                    continue
                _size, sha256 = hash_file(path)
                file_hashes[relative] = sha256
                existing_hash = existing_file_hashes.get(relative)
                # A known destination path with no indexed SHA is a conflict,
                # never a guessed create. This makes unknown/binary content
                # fail closed until the reviewer chooses its disposition.
                items.append(WorkspaceBundleItem(
                    id=f"file:{relative}", kind="file", name=relative,
                    classification=("create" if relative not in existing_file_hashes else
                                    "unchanged" if existing_hash == sha256 else "conflict"),
                    match_key=relative, group_key=_file_group(relative),
                ))
        # Declared connections become never-clobber global integration shells:
        # an existing integration means nothing changes, a missing one is
        # created with empty credentials for the admin to fill in.
        for decl in projection.connection_schemas:
            name = str(decl.get("integration_name") or "")
            if not name:
                continue
            match = (existing_integrations or {}).get(name)
            target = match if match is not None else uuid5(self.preview_id, f"integration:{name}")
            items.append(WorkspaceBundleItem(
                id=f"integration:{name}", kind="integration", name=name,
                classification=("unchanged" if match is not None else "create"),
                match_key=name, target_id=target,
            ))
        config_schemas = []
        for schema in projection.config_schemas:
            key = schema["key"]
            declaration = projection.manifest.configs[key]
            match = existing.get(("config", (key, None, self.organization_id)))
            config_schemas.append({
                **schema,
                "exists": match is not None,
                "has_existing_value": bool(match and match[2]),
                "has_package_default": _has_config_value(declaration.value),
                "requires_input": bool(
                    schema["required"]
                    and not _has_config_value(declaration.value)
                    and not (match and match[2])
                ),
            })
        return PlannedWorkspaceBundle(
            preview=WorkspaceBundlePreview(preview_token=str(self.preview_id), package_name=projection.package_name,
                                            package_sha256="", items=items,
                                            config_schemas=config_schemas,
                                            organization_id=self.organization_id),
            manifest=projection.manifest, id_map=self.reference_map(items),
            work_dir=projection.work_dir, file_hashes=file_hashes,
            destination_file_hashes={
                path: fingerprint
                for path, fingerprint in existing_file_hashes.items()
                if fingerprint is not None
            },
            connection_schemas=[dict(schema) for schema in projection.connection_schemas],
            organization_id=self.organization_id,
        )

    @staticmethod
    def _collections(manifest: Manifest):
        return (
            ("workflow", manifest.workflows, lambda x: (x.path, x.function_name)),
            ("app", manifest.apps, lambda x: (x.slug or x.path,)),
            ("table", manifest.tables, lambda x: (x.name,)),
            ("config", manifest.configs, lambda x: (x.key, None, UUID(x.organization_id) if x.organization_id else None)),
            ("claim", manifest.claims, lambda x: (x.name, UUID(x.organization_id) if x.organization_id else None)),
            ("event", manifest.events, lambda x: (x.name,)),
            ("form", manifest.forms, lambda x: (x.name,)),
            ("agent", manifest.agents, lambda x: (x.name,)),
            ("file_policy", manifest.file_policies, lambda x: (x.location, x.path, x.organization_id)),
        )

    async def _prefetch_existing(
        self, manifest: Manifest,
    ) -> dict[tuple[str, tuple], tuple[UUID, dict[str, Any], bool]]:
        if self.db is None:
            return {}
        incoming_keys = {
            (kind, key_fn(entity))
            for kind, entries, key_fn in self._collections(manifest)
            for entity in entries.values()
        }
        # Natural keys are intentionally limited to unattached workspace rows.
        # Solution-owned rows are install content and never candidates here.
        from src.models.orm.agents import Agent, AgentDelegation, AgentRole, AgentTool
        from src.models.orm.applications import Application
        from src.models.orm.config import Config
        from src.models.orm.custom_claims import CustomClaim
        from src.models.orm.events import EventSource, EventSubscription, ScheduleSource, WebhookSource
        from src.models.orm.external_mcp import AgentMCPConnection
        from src.models.orm.forms import Form, FormField, FormRole
        from src.models.orm.users import Role
        from src.models.orm.tables import Table
        from src.models.orm.workflows import Workflow
        result: dict[tuple[str, tuple], tuple[UUID, dict[str, Any], bool]] = {}
        serializers = {
            "workflow": lambda row: ManifestWorkflow.from_row(row).model_dump(mode="json", exclude={"id"}),
            "app": lambda row: ManifestApp.from_row(row).model_dump(mode="json", exclude={"id"}),
            "table": lambda row: ManifestTable.from_row(row).model_dump(mode="json", exclude={"id"}),
            "config": lambda row: ManifestConfig.from_row(row).model_dump(mode="json", exclude={"id"}),
            "claim": lambda row: ManifestCustomClaim.from_row(row).model_dump(mode="json", exclude={"id"}),
        }
        for kind, model, columns in (
            ("workflow", Workflow, (Workflow.path, Workflow.function_name)),
            ("app", Application, (Application.slug,)),
            ("table", Table, (Table.name,)),
            ("config", Config, (Config.key, Config.integration_id, Config.organization_id)),
            ("claim", CustomClaim, (CustomClaim.name, CustomClaim.organization_id)),
        ):
            query = select(model)
            if kind not in {"workflow", "app"}:
                query = query.where(self._scope_clause(model.organization_id))
            if "solution_id" in model.__table__.columns:
                query = query.where(model.solution_id.is_(None))
            rows = (await self.db.execute(query)).scalars().all()
            for row in rows:
                key = tuple(getattr(row, column.key) for column in columns)
                if (kind, key) not in incoming_keys:
                    continue
                if kind == "app" and row.repo_path is None:
                    # Independent Apps have no workspace source. A matching slug
                    # must be reviewed as a conflict, never serialized as a manifest App.
                    snapshot = {"path": None, "slug": row.slug, "name": row.name}
                else:
                    snapshot = serializers[kind](row)
                result[(kind, key)] = (
                    row.id, snapshot,
                    _has_config_value(row.value) if kind == "config" else False,
                )
        event_rows = (await self.db.execute(
            select(EventSource).where(
                self._scope_clause(EventSource.organization_id),
                EventSource.solution_id.is_(None),
            )
        )).scalars().all()
        event_rows = [
            row for row in event_rows if ("event", (row.name,)) in incoming_keys
        ]
        if event_rows:
            event_ids = [row.id for row in event_rows]
            schedules = {row.event_source_id: row for row in (await self.db.execute(
                select(ScheduleSource).where(ScheduleSource.event_source_id.in_(event_ids))
            )).scalars()}
            webhooks = {row.event_source_id: row for row in (await self.db.execute(
                select(WebhookSource).where(WebhookSource.event_source_id.in_(event_ids))
            )).scalars()}
            subscriptions: dict[UUID, list] = {}
            for row in (await self.db.execute(
                select(EventSubscription).where(EventSubscription.event_source_id.in_(event_ids))
            )).scalars():
                subscriptions.setdefault(row.event_source_id, []).append(row)
            for row in event_rows:
                snapshot = ManifestEventSource.from_row(
                    row, schedule=schedules.get(row.id), webhook=webhooks.get(row.id),
                    subscriptions=subscriptions.get(row.id, []),
                ).model_dump(mode="json", exclude={"id"})
                result[("event", (row.name,))] = (row.id, snapshot, False)
        # Load the inline form/agent content and bindings before comparing. A
        # name match with an empty snapshot would mark every agent as Replace.
        from src.services.manifest_import import _load_file_policy_model

        FilePolicy = _load_file_policy_model()
        scoped_rows = {}
        for kind, model in (("form", Form), ("agent", Agent), ("file_policy", FilePolicy)):
            query = select(model).where(self._scope_clause(model.organization_id))
            if kind == "agent":
                query = query.options(selectinload(Agent.llm_profile))
            if "solution_id" in model.__table__.columns:
                query = query.where(model.solution_id.is_(None))
            rows = (await self.db.execute(query)).scalars().all()
            if kind == "file_policy":
                scoped_rows[kind] = [
                    row for row in rows
                    if (kind, (
                        row.location, row.path,
                        str(row.organization_id) if row.organization_id else None,
                    )) in incoming_keys
                ]
            else:
                scoped_rows[kind] = [
                    row for row in rows if (kind, (row.name,)) in incoming_keys
                ]
        form_ids = [row.id for row in scoped_rows["form"]]
        agent_ids = [row.id for row in scoped_rows["agent"]]
        form_fields: dict[UUID, list] = {}
        for row in (await self.db.execute(
            select(FormField).where(FormField.form_id.in_(form_ids)).order_by(FormField.position)
        )).scalars():
            form_fields.setdefault(row.form_id, []).append(row)
        role_names = dict((await self.db.execute(select(Role.id, Role.name))).all())
        form_roles: dict[UUID, list[str]] = {}
        for form_id, role_id in (await self.db.execute(
            select(FormRole.form_id, FormRole.role_id).where(FormRole.form_id.in_(form_ids))
        )).all():
            form_roles.setdefault(form_id, []).append(role_names[role_id])
        agent_roles: dict[UUID, list[str]] = {}
        for agent_id, role_id in (await self.db.execute(
            select(AgentRole.agent_id, AgentRole.role_id).where(AgentRole.agent_id.in_(agent_ids))
        )).all():
            agent_roles.setdefault(agent_id, []).append(role_names[role_id])
        agent_tools: dict[UUID, list[UUID]] = {}
        for agent_id, workflow_id in (await self.db.execute(
            select(AgentTool.agent_id, AgentTool.workflow_id).where(AgentTool.agent_id.in_(agent_ids))
        )).all():
            agent_tools.setdefault(agent_id, []).append(workflow_id)
        agent_delegations: dict[UUID, list[UUID]] = {}
        for parent_id, child_id in (await self.db.execute(
            select(AgentDelegation.parent_agent_id, AgentDelegation.child_agent_id).where(
                AgentDelegation.parent_agent_id.in_(agent_ids)
            )
        )).all():
            agent_delegations.setdefault(parent_id, []).append(child_id)
        agent_mcp: dict[UUID, list[UUID]] = {}
        for agent_id, connection_id in (await self.db.execute(
            select(AgentMCPConnection.agent_id, AgentMCPConnection.connection_id).where(
                AgentMCPConnection.agent_id.in_(agent_ids)
            )
        )).all():
            agent_mcp.setdefault(agent_id, []).append(connection_id)
        for kind, rows in scoped_rows.items():
            for row in rows:
                if kind == "file_policy":
                    org = row.organization_id
                    key = (row.location, row.path, str(org) if org is not None else None)
                else:
                    key = (row.name,)
                if kind == "form":
                    snapshot = ManifestForm.from_row(
                        row, fields=form_fields.get(row.id),
                    ).model_dump(mode="json", exclude={"id"})
                    if form_roles.get(row.id):
                        snapshot["role_names"] = sorted(form_roles[row.id])
                elif kind == "agent":
                    snapshot = ManifestAgent.from_row(
                        row, tool_ids=sorted(agent_tools.get(row.id, [])),
                        delegated_agent_ids=sorted(agent_delegations.get(row.id, [])),
                        mcp_connection_ids=sorted(agent_mcp.get(row.id, [])),
                    ).model_dump(mode="json", exclude={"id"})
                    if agent_roles.get(row.id):
                        snapshot["role_names"] = sorted(agent_roles[row.id])
                else:
                    snapshot = ManifestFilePolicy.from_row(row).model_dump(mode="json", exclude={"id"})
                result[(kind, key)] = (row.id, snapshot, False)
        return result

    async def _prefetch_existing_integrations(self) -> dict[str, UUID]:
        """Global integrations by unique name (shells never clobber)."""
        if self.db is None:
            return {}
        from src.models.orm.integrations import Integration

        rows = (
            await self.db.execute(select(Integration.id, Integration.name))
        ).all()
        return {str(name): row_id for row_id, name in rows}

    async def _prefetch_existing_file_hashes(self, incoming_paths: Iterator[str]) -> dict[str, str | None]:
        from src.services.repo_storage import RepoStorage
        from itertools import batched

        destination_paths: dict[str, str | None] = {}
        repo = RepoStorage()
        for paths in batched(incoming_paths, _FILE_LOOKUP_BATCH_SIZE):
            batch = list(paths)
            for path in batch:
                fingerprint = await repo.content_hash(path)
                if fingerprint is not None:
                    destination_paths[path] = fingerprint
        return destination_paths
