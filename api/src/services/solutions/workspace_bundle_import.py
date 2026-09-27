"""Apply a reviewed workspace bundle plan through the non-destructive adapter."""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from src.models.contracts.solutions import WorkspaceBundleDecision, WorkspaceBundlePreview
from src.services.repo_storage import RepoStorage
from src.services.manifest_import import ManifestResolver, PartialImportSelection
from src.services.solutions.workspace_bundle_plan import PlannedWorkspaceBundle
from src.services.sync_ops import SyncOp


class WorkspaceBundleDecisionError(ValueError):
    """The request no longer exactly represents the preview's conflicts."""


def require_workspace_config_values(
    preview: WorkspaceBundlePreview,
    decisions: Sequence[WorkspaceBundleDecision],
    values: dict[str, str],
) -> None:
    """Require values only for selected declarations with no usable value."""
    actions = {decision.item_id: decision.action for decision in decisions}
    items = {item.name: item for item in preview.items if item.kind == "config"}
    missing = []
    for schema in preview.config_schemas:
        key = str(schema["key"])
        item = items.get(key)
        if not item or not schema.get("requires_input"):
            continue
        selected = item.classification == "create" or (
            item.classification == "conflict" and actions.get(item.id) == "replace"
        )
        if selected and not values.get(key, "").strip():
            missing.append(key)
    if missing:
        raise WorkspaceBundleDecisionError(
            "Enter required configuration values: " + ", ".join(missing)
        )


class _FileIndexWriter(Protocol):
    async def write_file(self, path: str, source, *, expected_hash: str) -> str:
        raise NotImplementedError


@dataclass(frozen=True)
class WorkspaceBundleImportResult:
    imported_item_ids: frozenset[str]
    selected_item_ids: frozenset[str]
    operations: tuple[SyncOp, ...]


class WorkspaceBundleImporter:
    def __init__(
        self,
        db: AsyncSession,
        *,
        progress_fn: Callable[[str, int, int], Awaitable[None]] | None = None,
    ):
        self.db = db
        self.progress_fn = progress_fn

    async def apply(
        self,
        plan: PlannedWorkspaceBundle,
        decisions: Sequence[WorkspaceBundleDecision],
        *,
        config_values: dict[str, str] | None = None,
        updated_by: str = "workspace-import",
    ) -> WorkspaceBundleImportResult:
        by_id = {decision.item_id: decision.action for decision in decisions}
        conflicts = {
            item.id for item in plan.preview.items if item.classification == "conflict"
        }
        if len(by_id) != len(decisions) or set(by_id) != conflicts:
            raise WorkspaceBundleDecisionError(
                "every conflict requires exactly one keep or replace decision"
            )
        require_workspace_config_values(plan.preview, decisions, config_values or {})
        if plan.work_dir is None:
            raise ValueError("workspace bundle import requires an extracted package directory")

        kept_groups = {
            item.group_key for item in plan.preview.items
            if item.group_key and by_id.get(item.id) == "keep"
        }
        selected_items = {
            item.id
            for item in plan.preview.items
            if item.group_key not in kept_groups
            and (item.classification == "create" or by_id.get(item.id) == "replace")
        }
        included = {
            item.source_id and str(item.source_id)
            for item in plan.preview.items
            if item.id in selected_items and item.source_id is not None
        }
        # Kept conflicts are absent from the write selection but their target IDs
        # remain in ``plan.id_map`` for selected forms/agents/events that refer to
        # them.  This is why the planner map is not derived from ``included``.
        selection = PartialImportSelection(
            included_source_ids=frozenset(item for item in included if item),
            target_ids=plan.id_map,
        )
        # A package config declaration may update metadata, but an existing
        # workspace value belongs to this environment. Keep it unless the
        # reviewer explicitly enters a replacement below.
        from src.models.orm.config import Config
        for item in plan.preview.items:
            if item.kind != "config" or item.id not in selected_items or item.classification != "conflict":
                continue
            declaration = plan.manifest.configs[item.name]
            if declaration.config_type == "secret" or item.target_id is None:
                continue
            existing = await self.db.get(Config, item.target_id)
            if existing is not None:
                declaration.value = existing.value
        ops = await ManifestResolver(self.db).plan_partial_import(
            plan.manifest, selection=selection, work_dir=plan.work_dir,
            progress_fn=self.progress_fn, organization_id=plan.organization_id,
        )
        # Declared connections become never-clobber global integration shells
        # (empty credentials for the admin to fill in). Existing integrations
        # are left untouched, so shells need no conflict decisions.
        from src.services.solutions.integration_shells import (
            upsert_integration_shells,
        )

        await upsert_integration_shells(self.db, plan.connection_schemas or [])
        await self._merge_package_roles(plan, selection)
        await self._set_config_values(
            plan, selected_items, by_id, config_values or {}, updated_by
        )
        return WorkspaceBundleImportResult(
            imported_item_ids=selection.included_source_ids,
            selected_item_ids=frozenset(selected_items),
            operations=tuple(ops),
        )

    async def _set_config_values(
        self,
        plan: PlannedWorkspaceBundle,
        selected_items: set[str],
        decisions: dict[str, str],
        config_values: dict[str, str],
        updated_by: str,
    ) -> None:
        """Apply entered values through ConfigRepository so secrets are encrypted."""
        if not config_values:
            return
        from src.models.contracts.config import SetConfigRequest
        from src.models.enums import ConfigType
        from src.repositories.config import ConfigRepository

        declarations = plan.manifest.configs
        if set(config_values) - set(declarations):
            raise WorkspaceBundleDecisionError("config values must match declared keys")
        repo = ConfigRepository(
            self.db, org_id=plan.organization_id, is_superuser=True
        )
        for key, value in config_values.items():
            item_id = f"entity:config:{declarations[key].id}"
            if decisions.get(item_id) == "keep" or not value.strip():
                continue
            if item_id not in selected_items and not any(
                item.id == item_id and item.classification == "unchanged"
                for item in plan.preview.items
            ):
                continue
            config = declarations[key]
            await repo.set_config(
                SetConfigRequest(
                    key=key, value=value, type=ConfigType(config.config_type),
                    description=config.description, organization_id=plan.organization_id,
                ),
                updated_by=updated_by,
            )

    async def _merge_package_roles(
        self, plan: PlannedWorkspaceBundle, selection: "PartialImportSelection",
    ) -> None:
        """Merge package role bindings into selected entities (never delete).

        Role names are portable while raw role UUIDs are source-env-specific,
        so only ``role_names`` are honored; missing names become empty global
        roles, exactly like installs. Bindings the destination already has are
        preserved — merging only adds. Entities outside the write selection
        (kept conflicts, unchanged) are left alone.
        """
        from uuid import UUID

        from src.models.orm.agents import AgentRole
        from src.models.orm.app_roles import AppRole
        from src.models.orm.forms import FormRole
        from src.models.orm.workflow_roles import WorkflowRole
        from src.services.manifest_import import _resolve_role_names
        from src.services.sync_ops import MergeRoles

        included = selection.included_source_ids
        candidates: list[tuple[Any, str, UUID, list[str], dict[str, str]]] = []
        collections = (
            (plan.manifest.workflows, WorkflowRole, "workflow_id", {}),
            (plan.manifest.apps, AppRole, "app_id", {}),
            (plan.manifest.forms, FormRole, "form_id", {"assigned_by": "workspace-import"}),
            (plan.manifest.agents, AgentRole, "agent_id", {"assigned_by": "workspace-import"}),
        )
        for mapping, _junction, _fk, _extra in collections:
            for source_id, entry in mapping.items():
                if str(source_id) not in included:
                    continue
                names = [name for name in getattr(entry, "role_names", None) or []]
                if names:
                    candidates.append((_junction, _fk, entry, names, _extra))
        if not candidates:
            return
        ordered: list[str] = []
        for _, _, _, names, _ in candidates:
            for name in names:
                if name not in ordered:
                    ordered.append(name)
        resolved = await _resolve_role_names(self.db, ordered, create_missing=True)
        by_name = {name: UUID(rid) for name, rid in zip(ordered, resolved)}
        for junction, fk, entry, names, extra in candidates:
            target = selection.target_ids.get(UUID(entry.id))
            if target is None:
                continue
            await MergeRoles(
                junction_model=junction, entity_fk=fk, entity_id=target,
                role_ids={by_name[name] for name in names},
                extra_fields=dict(extra),
            ).execute(self.db)

    async def promote_selected_files(
        self,
        plan: PlannedWorkspaceBundle,
        selected_item_ids: set[str] | frozenset[str],
        *,
        file_index: _FileIndexWriter,
        expected_destination_hashes: dict[str, str],
    ) -> list[str]:
        """Promote reviewed source files through the canonical S3/index writer.

        Repeating this operation is safe after a runner loss: each write is
        content-addressed by the preview hash and FileIndexService upserts its
        row rather than creating another record.
        """
        if plan.work_dir is None:
            raise ValueError("workspace bundle file promotion requires an extracted package directory")
        promoted: list[str] = []
        destination = RepoStorage()
        for item in plan.preview.items:
            if item.kind != "file" or item.id not in selected_item_ids:
                continue
            expected = plan.file_hashes.get(item.name)
            if expected is None:
                raise WorkspaceBundleDecisionError(f"missing staged hash for {item.name}")
            actual_destination_hash = await destination.content_hash(item.name)
            expected_destination_hash = expected_destination_hashes.get(item.name)
            if actual_destination_hash != expected_destination_hash:
                raise WorkspaceBundleDecisionError(
                    f"workspace file {item.name} changed after preview; create a new preview"
                )
            source = plan.work_dir / item.name
            await file_index.write_file(item.name, source, expected_hash=expected)
            promoted.append(item.name)
        return promoted
