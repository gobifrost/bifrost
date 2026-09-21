"""Durable import of a reviewed Solution archive into the global workspace."""
from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel

from src.jobs.platform.base import (
    PlatformJobContext,
    PlatformJobDefinition,
    PlatformJobFailure,
    PlatformJobPolicy,
)
from src.models.contracts.solutions import WorkspaceBundleDecision, WorkspaceBundlePreview
from src.core.database import get_db_context
from src.core.repo_dirty import mark_repo_dirty
from src.services.file_index_service import FileIndexService
from src.services.repo_sync_writer import RepoSyncWriter
from src.services.solutions.workspace_bundle_import import WorkspaceBundleImportResult, WorkspaceBundleImporter
from src.services.solutions.workspace_bundle_plan import (
    SolutionPackageWorkspaceProjection,
    WorkspaceBundlePlanner,
)
from src.services.solutions.workspace_bundle_storage import WorkspaceBundleStorage


class WorkspaceBundleImportPayload(BaseModel):
    preview_id: UUID
    package_sha256: str
    decisions: list[WorkspaceBundleDecision]


def _require_requester(metadata: dict, context: PlatformJobContext, payload: WorkspaceBundleImportPayload) -> None:
    if metadata.get("requested_by") != context.requested_by_user_id:
        raise PlatformJobFailure("preview_not_authorized", "Workspace import preview belongs to another user.")
    if metadata.get("package_sha256") != payload.package_sha256:
        raise PlatformJobFailure("preview_archive_changed", "The staged workspace archive no longer matches the reviewed preview.")
    expires_at = datetime.fromisoformat(metadata["expires_at"])
    if expires_at <= datetime.now(timezone.utc):
        raise PlatformJobFailure("preview_expired", "Workspace import preview has expired.")


def _require_preview_is_current(
    reviewed: WorkspaceBundlePreview,
    reviewed_file_hashes: dict[str, str],
    current: WorkspaceBundlePreview,
    current_file_hashes: dict[str, str],
) -> None:
    def items(preview: WorkspaceBundlePreview) -> dict[str, tuple[object, ...]]:
        return {
            item.id: (
                item.kind, item.classification, item.match_key,
                str(item.source_id), str(item.target_id),
            )
            for item in preview.items
        }

    if items(reviewed) != items(current) or reviewed_file_hashes != current_file_hashes:
        raise PlatformJobFailure(
            "preview_stale",
            "The workspace changed since preview; create a new preview before importing.",
        )


async def run_workspace_bundle_import(
    context: PlatformJobContext, payload: WorkspaceBundleImportPayload
) -> dict:
    storage = WorkspaceBundleStorage(payload.preview_id)
    metadata = await storage.load_metadata()
    _require_requester(metadata, context, payload)
    await context.report("Restoring reviewed workspace archive", percent=5)
    with tempfile.TemporaryDirectory(prefix="bifrost-workspace-bundle-") as tmp:
            archive = Path(tmp) / "package.zip"
            await storage.copy_package_to(archive, expected_sha256=payload.package_sha256)
            from src.services.solutions.zip_install import _parse_workspace, _safe_extract_path

            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            _safe_extract_path(archive, str(workspace))
            reviewed_preview = WorkspaceBundlePreview.model_validate(metadata["preview"])
            reviewed_file_hashes = metadata["file_hashes"]
            async with get_db_context() as db:
                importer = WorkspaceBundleImporter(
                    db,
                    progress_fn=lambda phase, current, total: context.report(
                        phase, current=current, total=total
                    ),
                )
                journal = context.checkpoint
                resuming = (
                    journal is not None
                    and journal.get("db_applied") is True
                    and journal.get("preview_id") == str(payload.preview_id)
                    and journal.get("package_sha256") == payload.package_sha256
                )
                projection = SolutionPackageWorkspaceProjection.from_preview(
                    _parse_workspace(workspace), preview_id=payload.preview_id, work_dir=workspace,
                )
                plan = await WorkspaceBundlePlanner(db, preview_id=payload.preview_id).plan(projection)
                if resuming:
                    result = WorkspaceBundleImportResult(
                        imported_item_ids=frozenset(journal["imported_entity_ids"]),
                        selected_item_ids=frozenset(journal["selected_item_ids"]),
                        operations=(),
                    )
                else:
                    _require_preview_is_current(
                        reviewed_preview, reviewed_file_hashes, plan.preview, plan.file_hashes or {},
                    )
                    result = await importer.apply(plan, payload.decisions)
                # Make entity upserts durable before the idempotent S3 phase.
                await context.report("Committing workspace entity changes", percent=60)
                await db.commit()
                journal = {
                    "preview_id": str(payload.preview_id),
                    "package_sha256": payload.package_sha256,
                    "selected_item_ids": sorted(result.selected_item_ids),
                    "imported_entity_ids": sorted(result.imported_item_ids),
                    "db_applied": True,
                }
                await context.save_checkpoint(journal, phase="Database import committed; finalizing files")
                await context.report("Promoting selected workspace files", percent=70)
                try:
                    promoted = await importer.promote_selected_files(plan, result.selected_item_ids, file_index=FileIndexService(db))
                    await RepoSyncWriter(db).regenerate_manifest()
                    await db.commit()
                    await mark_repo_dirty()
                except Exception as exc:
                    raise PlatformJobFailure("workspace_bundle_finalize_failed", "Workspace import database changes were committed but file finalization failed; retrying the durable job.", retryable=True, result=journal) from exc
    await context.report("Workspace import complete", percent=100)
    result_body = {
        "imported_entity_ids": sorted(result.imported_item_ids),
        "promoted_files": promoted,
        "warning": "Workspace files and manifests are uncommitted; review references and run compatibility checks before committing.",
    }
    # Do this only on success; ordinary failure and runner loss retain the
    # immutable archive for the shared job retry policy / TTL cleanup.
    await storage.delete()
    return result_body


WORKSPACE_BUNDLE_IMPORT_DEFINITION = PlatformJobDefinition(
    job_type="workspace.bundle_import",
    payload_version=1,
    payload_model=WorkspaceBundleImportPayload,
    handler=run_workspace_bundle_import,
    encrypt_payload=True,
    policy=PlatformJobPolicy(
        timeout_seconds=60 * 60,
        max_attempts=2,
        max_concurrency=1,
        min_memory_headroom_mb=512,
        retry_on_runner_loss=True,
        retry_on_failure=True,
        allow_running_cancellation=True,
    ),
)
