"""Apply a reviewed workspace bundle plan through the non-destructive adapter."""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from src.models.contracts.solutions import WorkspaceBundleDecision
from src.services.manifest_import import ManifestResolver, PartialImportSelection
from src.services.solutions.workspace_bundle_plan import PlannedWorkspaceBundle
from src.services.sync_ops import SyncOp


class WorkspaceBundleDecisionError(ValueError):
    """The request no longer exactly represents the preview's conflicts."""


class _FileIndexWriter(Protocol):
    async def write_file(self, path: str, source, *, expected_hash: str) -> str: ...


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
    ) -> WorkspaceBundleImportResult:
        by_id = {decision.item_id: decision.action for decision in decisions}
        conflicts = {
            item.id for item in plan.preview.items if item.classification == "conflict"
        }
        if len(by_id) != len(decisions) or set(by_id) != conflicts:
            raise WorkspaceBundleDecisionError(
                "every conflict requires exactly one keep or replace decision"
            )
        if plan.work_dir is None:
            raise ValueError("workspace bundle import requires an extracted package directory")

        selected_items = {
            item.id
            for item in plan.preview.items
            if item.classification == "create" or by_id.get(item.id) == "replace"
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
        ops = await ManifestResolver(self.db).plan_partial_import(
            plan.manifest, selection=selection, work_dir=plan.work_dir,
            progress_fn=self.progress_fn,
        )
        return WorkspaceBundleImportResult(
            imported_item_ids=selection.included_source_ids,
            selected_item_ids=frozenset(selected_items),
            operations=tuple(ops),
        )

    async def promote_selected_files(
        self,
        plan: PlannedWorkspaceBundle,
        selected_item_ids: set[str] | frozenset[str],
        *,
        file_index: _FileIndexWriter,
    ) -> list[str]:
        """Promote reviewed source files through the canonical S3/index writer.

        Repeating this operation is safe after a runner loss: each write is
        content-addressed by the preview hash and FileIndexService upserts its
        row rather than creating another record.
        """
        if plan.work_dir is None:
            raise ValueError("workspace bundle file promotion requires an extracted package directory")
        promoted: list[str] = []
        for item in plan.preview.items:
            if item.kind != "file" or item.id not in selected_item_ids:
                continue
            expected = plan.file_hashes.get(item.name)
            if expected is None:
                raise WorkspaceBundleDecisionError(f"missing staged hash for {item.name}")
            source = plan.work_dir / item.name
            await file_index.write_file(item.name, source, expected_hash=expected)
            promoted.append(item.name)
        return promoted
