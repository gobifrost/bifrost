"""Normalize an install-format Solution package into a workspace-import plan.

Install packages are intentionally not manifests: notably their config entries
are declarations, not ``ManifestConfig`` values.  Keeping that conversion here
makes the lossy boundary explicit and prevents the install deployer shape from
leaking into workspace reconciliation.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bifrost.manifest import (
    Manifest,
    ManifestAgent,
    ManifestApp,
    ManifestConfig,
    ManifestCustomClaim,
    ManifestEventSource,
    ManifestFilePolicy,
    ManifestForm,
    ManifestTable,
    ManifestWorkflow,
)
from src.models.contracts.solutions import WorkspaceBundleItem, WorkspaceBundlePreview
from src.services.solutions.zip_install import PreviewResult

_ID_NAMESPACE = UUID("f4b9f7ce-b035-48b8-bfdd-d18a02e34731")
_CONFIG_WARNING = (
    "Solution config declarations import as global workspace configs; required and position are not retained."
)
_SCOPE_WARNING = (
    "Imported entities are unattached global workspace content (organization_id and solution_id are null)."
)


def _uuid(value: object, *, preview_id: UUID, stable_key: str) -> UUID:
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return uuid5(preview_id, stable_key)


def _entry(entry: dict[str, Any], *, preview_id: UUID, stable_key: str) -> dict[str, Any]:
    result = dict(entry)
    result["id"] = str(_uuid(result.get("id"), preview_id=preview_id, stable_key=stable_key))
    # A bundle describes an install's binding. Workspace import deliberately
    # strips that binding before any manifest model validates the entity.
    result["organization_id"] = None
    result.pop("solution_id", None)
    result["roles"] = []
    result.pop("role_names", None)
    return result


@dataclass(frozen=True)
class SolutionPackageWorkspaceProjection:
    """The explicitly lossy projection from package/install to workspace scope."""

    manifest: Manifest
    package_name: str
    warnings: list[str]

    @classmethod
    def from_preview(
        cls, package: PreviewResult, *, preview_id: UUID
    ) -> "SolutionPackageWorkspaceProjection":
        workflows: dict[str, ManifestWorkflow] = {}
        for row in package.workflows:
            data = _entry(row, preview_id=preview_id, stable_key=f"workflow:{row.get('path')}:{row.get('function_name')}")
            data.setdefault("name", data["id"])
            workflows[data["id"]] = ManifestWorkflow.model_validate(data)

        apps: dict[str, ManifestApp] = {}
        for row in package.apps:
            data = _entry(row, preview_id=preview_id, stable_key=f"app:{row.get('slug') or row.get('path')}")
            apps[data["id"]] = ManifestApp.model_validate(data)

        tables: dict[str, ManifestTable] = {}
        for row in package.tables:
            data = _entry(row, preview_id=preview_id, stable_key=f"table:{row.get('name')}")
            data.setdefault("name", data["id"])
            tables[data["id"]] = ManifestTable.model_validate(data)

        forms: dict[str, ManifestForm] = {}
        for row in package.forms:
            data = _entry(row, preview_id=preview_id, stable_key=f"form:{row.get('name') or row.get('id')}")
            forms[data["id"]] = ManifestForm.model_validate(data)

        agents: dict[str, ManifestAgent] = {}
        for row in package.agents:
            data = _entry(row, preview_id=preview_id, stable_key=f"agent:{row.get('name') or row.get('id')}")
            agents[data["id"]] = ManifestAgent.model_validate(data)

        claims: dict[str, ManifestCustomClaim] = {}
        for row in package.claims:
            data = _entry(row, preview_id=preview_id, stable_key=f"claim:{row.get('name')}")
            data.setdefault("name", data["id"])
            claims[data["id"]] = ManifestCustomClaim.model_validate(data)

        events: dict[str, ManifestEventSource] = {}
        for row in package.events:
            data = _entry(row, preview_id=preview_id, stable_key=f"event:{row.get('name')}")
            events[data["id"]] = ManifestEventSource.model_validate(data)

        file_policies: dict[str, ManifestFilePolicy] = {}
        for row in package.file_policies:
            data = _entry(row, preview_id=preview_id, stable_key=f"file-policy:{row.get('location')}:{row.get('path')}")
            file_policies[data["id"]] = ManifestFilePolicy.model_validate(data)

        # This is intentionally *not* ``ManifestConfig.model_validate(row)``.
        # A Solution schema has type/default while a workspace config has
        # config_type/value; required and position have no workspace analogue.
        configs: dict[str, ManifestConfig] = {}
        for row in package.config_schemas:
            key = str(row.get("key") or row.get("id"))
            config_id = _uuid(row.get("id"), preview_id=preview_id, stable_key=f"config:{key}")
            configs[key] = ManifestConfig(
                id=str(config_id), key=key, config_type=str(row.get("type", "string")),
                description=row.get("description"), value=row.get("default"),
                organization_id=None, integration_id=None,
            )

        warnings = [_SCOPE_WARNING]
        if package.config_schemas:
            warnings.append(_CONFIG_WARNING)
        if package.connection_schemas or package.file_locations:
            warnings.append(
                "Solution connection schemas and file-location declarations are package-only and are not imported into the workspace."
            )
        return cls(
            manifest=Manifest(workflows=workflows, apps=apps, tables=tables, forms=forms,
                              agents=agents, claims=claims, configs=configs, events=events,
                              file_policies=file_policies),
            package_name=package.name or package.slug or "Solution package",
            warnings=warnings,
        )


@dataclass(frozen=True)
class PlannedWorkspaceBundle:
    preview: WorkspaceBundlePreview
    manifest: Manifest
    id_map: dict[UUID, UUID]
    work_dir: Path | None = None
    file_hashes: dict[str, str] | None = None


class WorkspaceBundlePlanner:
    def __init__(self, db: AsyncSession | None, *, preview_id: UUID):
        self.db = db
        self.preview_id = preview_id

    @staticmethod
    def reference_map(items: list[WorkspaceBundleItem]) -> dict[UUID, UUID]:
        """Map every selected source id, including a kept conflict, to its target."""
        return {
            item.source_id: item.target_id
            for item in items
            if item.source_id is not None and item.target_id is not None
        }

    def plan_sync(self, projection: SolutionPackageWorkspaceProjection) -> PlannedWorkspaceBundle:
        """Plan package creation without a DB; useful for deterministic staging tests."""
        return self._build_plan(projection, {})

    async def plan(self, projection: SolutionPackageWorkspaceProjection) -> PlannedWorkspaceBundle:
        return self._build_plan(projection, await self._prefetch_existing())

    def _build_plan(self, projection: SolutionPackageWorkspaceProjection, existing: dict[tuple[str, tuple], tuple[UUID, dict[str, Any]]]) -> PlannedWorkspaceBundle:
        items: list[WorkspaceBundleItem] = []
        for kind, entries, key_fn in self._collections(projection.manifest):
            for entity in entries.values():
                source = UUID(entity.id)
                natural_key = key_fn(entity)
                match = existing.get((kind, natural_key))
                target = match[0] if match else uuid5(self.preview_id, f"{kind}:{natural_key}")
                incoming = entity.model_dump(mode="json", exclude={"id"})
                classification = "create" if match is None else (
                    "unchanged" if incoming == match[1] else "conflict"
                )
                items.append(WorkspaceBundleItem(
                    id=f"entity:{kind}:{source}", kind=kind, name=str(getattr(entity, "name", None) or getattr(entity, "key", source)),
                    classification=classification, match_key=str(natural_key), source_id=source, target_id=target,
                ))
        return PlannedWorkspaceBundle(
            preview=WorkspaceBundlePreview(preview_token=str(self.preview_id), package_name=projection.package_name,
                                           package_sha256="", items=items, warnings=projection.warnings),
            manifest=projection.manifest, id_map=self.reference_map(items), file_hashes={},
        )

    @staticmethod
    def _collections(manifest: Manifest):
        return (
            ("workflow", manifest.workflows, lambda x: (x.path, x.function_name)),
            ("app", manifest.apps, lambda x: (x.slug or x.path,)),
            ("table", manifest.tables, lambda x: (x.name, None)),
            ("config", manifest.configs, lambda x: (x.key, None, None)),
            ("claim", manifest.claims, lambda x: (x.name, None)),
            ("event", manifest.events, lambda x: (x.name,)),
            ("form", manifest.forms, lambda x: (x.name,)),
            ("agent", manifest.agents, lambda x: (x.name,)),
            ("file_policy", manifest.file_policies, lambda x: (x.location, x.path, None)),
        )

    async def _prefetch_existing(self) -> dict[tuple[str, tuple], tuple[UUID, dict[str, Any]]]:
        if self.db is None:
            return {}
        # Natural keys are intentionally limited to unattached workspace rows.
        # Solution-owned rows are install content and never candidates here.
        from src.models.orm.agents import Agent
        from src.models.orm.applications import Application
        from src.models.orm.config import Config
        from src.models.orm.custom_claims import CustomClaim
        from src.models.orm.forms import Form
        from src.models.orm.tables import Table
        from src.models.orm.workflows import Workflow
        result: dict[tuple[str, tuple], tuple[UUID, dict[str, Any]]] = {}
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
            if "solution_id" in model.__table__.columns:
                query = query.where(model.solution_id.is_(None))
            rows = (await self.db.execute(query)).scalars().all()
            for row in rows:
                key = tuple(getattr(row, column.key) for column in columns)
                result[(kind, key)] = (row.id, serializers[kind](row))
        # Forms and agents have no package-portable natural key more reliable
        # than their name.  Keep them global-only too, instead of accidentally
        # matching an identically named installed entity.
        for kind, model in (("form", Form), ("agent", Agent)):
            query = select(model).where(model.organization_id.is_(None))
            if "solution_id" in model.__table__.columns:
                query = query.where(model.solution_id.is_(None))
            for row in (await self.db.execute(query)).scalars().all():
                result[(kind, (row.name,))] = (row.id, {})
        return result
