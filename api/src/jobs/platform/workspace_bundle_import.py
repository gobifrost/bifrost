"""Durable import of a reviewed Solution archive into the global workspace."""
from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import select

from src.jobs.platform.base import (
    PlatformJobContext,
    PlatformJobFailure,
    PlatformJobCancelled,
    define_platform_job,
)
from src.models.contracts.solutions import WorkspaceBundleDecision, WorkspaceBundlePreview
from src.core.database import get_db_context
from src.core.repo_dirty import mark_repo_dirty
from src.services.file_index_service import FileIndexService
from src.services.repo_sync_writer import RepoSyncWriter
from src.services.solutions.workspace_bundle_import import (
    WorkspaceBundleDecisionError,
    WorkspaceBundleImportResult,
    WorkspaceBundleImporter,
)
from src.services.solutions.workspace_bundle_plan import (
    SolutionPackageWorkspaceProjection,
    WorkspaceBundlePlanner,
)
from src.services.solutions.workspace_bundle_storage import WorkspaceBundleStorage
from src.services.solutions.archive_compute import inspect_solution_archive


class WorkspaceBundleImportPayload(BaseModel):
    preview_id: UUID
    package_sha256: str
    decisions: list[WorkspaceBundleDecision]
    config_values: dict[str, str] = Field(default_factory=dict)


def _require_requester(metadata: dict, context: PlatformJobContext, payload: WorkspaceBundleImportPayload) -> None:
    if metadata.get("requested_by") != context.requested_by_user_id:
        raise PlatformJobFailure("preview_not_authorized", "Workspace import preview belongs to another user.")
    if metadata.get("package_sha256") != payload.package_sha256:
        raise PlatformJobFailure("preview_archive_changed", "The staged workspace archive no longer matches the reviewed preview.")
    target_scope = str(context.organization_id) if context.organization_id else None
    if metadata.get("organization_id") != target_scope:
        raise PlatformJobFailure("preview_not_authorized", "Workspace import preview scope changed.")
    bound_job = metadata.get("platform_job_id")
    if bound_job is not None and bound_job != str(context.job_id):
        raise PlatformJobFailure("preview_not_authorized", "Workspace import preview belongs to another job.")
    expires_at = datetime.fromisoformat(metadata["expires_at"])
    # Expiry controls accepting a new import request.  Once the queueing
    # request has bound this immutable preview to this particular durable job,
    # retries must still be able to finish it after the preview TTL elapses.
    if (
        metadata.get("platform_job_id") != str(context.job_id)
        and expires_at <= datetime.now(timezone.utc)
    ):
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

    if (
        items(reviewed) != items(current)
        or reviewed.config_schemas != current.config_schemas
        or reviewed_file_hashes != current_file_hashes
    ):
        raise PlatformJobFailure(
            "preview_stale",
            "The workspace changed since preview; create a new preview before importing.",
        )


def _checkpoint_matches_replanned_bundle(
    reviewed: WorkspaceBundlePreview,
    current: WorkspaceBundlePreview,
    selected_item_ids: set[str],
    entered_config_keys: set[str] | None = None,
) -> bool:
    """Recognize the only state transition made before the durable checkpoint.

    Entity create/replace actions become ``unchanged`` after their database
    transaction commits.  File actions are promoted later, so their preview
    state must not move.  This lets a runner-loss retry resume finalization
    without accepting any unrelated workspace change.
    """
    reviewed_items = {item.id: item for item in reviewed.items}
    current_items = {item.id: item for item in current.items}
    if reviewed_items.keys() != current_items.keys():
        return False

    for item_id, reviewed_item in reviewed_items.items():
        current_item = current_items[item_id]
        if (
            reviewed_item.kind != current_item.kind
            or reviewed_item.match_key != current_item.match_key
            or reviewed_item.source_id != current_item.source_id
            or reviewed_item.target_id != current_item.target_id
        ):
            return False
        expected_classification = reviewed_item.classification
        if item_id in selected_item_ids and reviewed_item.kind != "file":
            expected_classification = "unchanged"
        if reviewed_item.kind == "config" and reviewed_item.name in (entered_config_keys or set()):
            # An explicitly entered value may differ from the package default
            # even after the config row was committed successfully.
            if current_item.classification in {"unchanged", "conflict"}:
                continue
        if current_item.classification != expected_classification:
            return False
    return True


async def _entered_configs_match(db, plan, payload: WorkspaceBundleImportPayload) -> bool:
    """Prove entered values committed before resuming after runner loss."""
    from src.core.security import decrypt_secret
    from src.models.enums import ConfigType
    from src.models.orm.config import Config

    kept = {
        decision.item_id for decision in payload.decisions
        if decision.action == "keep"
    }
    for key, value in payload.config_values.items():
        if not value.strip():
            continue
        declaration = plan.manifest.configs.get(key)
        if declaration is None:
            return False
        if f"entity:config:{declaration.id}" in kept:
            continue
        row = (
            await db.execute(
                select(Config).where(
                    Config.key == key,
                    Config.organization_id == plan.organization_id,
                    Config.integration_id.is_(None),
                )
            )
        ).scalar_one_or_none()
        if (
            row is None
            or row.config_type.value != declaration.config_type
            or row.description != declaration.description
            or row.required != declaration.required
            or row.position != declaration.position
        ):
            return False
        stored = row.value.get("value") if isinstance(row.value, dict) else row.value
        if row.config_type == ConfigType.SECRET:
            if not isinstance(stored, str) or decrypt_secret(stored) != value:
                return False
        elif stored != value:
            return False
    return True


def _journal_for_db_apply(
    payload: WorkspaceBundleImportPayload,
    result: WorkspaceBundleImportResult,
) -> dict:
    return {
        "preview_id": str(payload.preview_id),
        "package_sha256": payload.package_sha256,
        "selected_item_ids": sorted(result.selected_item_ids),
        "imported_entity_ids": sorted(result.imported_item_ids),
        "db_apply_started": True,
    }


async def _require_attempt(db, context: PlatformJobContext) -> None:
    if not await context.authorize_effect(db):
        raise PlatformJobCancelled


def _import_result(imported_item_ids, promoted_files) -> dict:
    return {
        "imported_entity_ids": sorted(imported_item_ids), "promoted_files": list(promoted_files),
        "warning": "Workspace files and manifests are uncommitted; review references and run compatibility checks before committing.",
    }


async def run_workspace_bundle_import(
    context: PlatformJobContext, payload: WorkspaceBundleImportPayload
) -> dict:
    if context.file_compute is None:
        raise PlatformJobFailure("file_compute_required", "Workspace import requires API-owned archive compute.")
    storage = WorkspaceBundleStorage(payload.preview_id)
    completed = context.checkpoint
    if completed is not None and completed.get("files_finalized") is True:
        if (completed.get("preview_id") != str(payload.preview_id)
                or completed.get("package_sha256") != payload.package_sha256
                or completed.get("requested_by") != context.requested_by_user_id
                or completed.get("organization_id") != (str(context.organization_id) if context.organization_id else None)):
            raise PlatformJobFailure("workspace_checkpoint_mismatch", "Workspace completion checkpoint no longer matches its request.")
        async with get_db_context() as db:
            await _require_attempt(db, context)
            await mark_repo_dirty()
            await storage.delete()
        return _import_result(completed["imported_entity_ids"], completed["promoted_files"])
    metadata = await storage.load_metadata()
    _require_requester(metadata, context, payload)
    async with get_db_context() as db:
        await _require_attempt(db, context)
    await context.report("Restoring reviewed workspace archive", percent=5)
    with tempfile.TemporaryDirectory(prefix="bifrost-workspace-bundle-") as tmp:
        archive = Path(tmp) / "package.zip"
        await storage.copy_package_to(archive, expected_sha256=payload.package_sha256)
        # Capture and entered values stay in the API. No DB scope spans the
        # physical source-only inspector or its cancellation-safe verification.
        async with inspect_solution_archive(archive, context.file_compute) as inspected:
            _require_requester(await storage.load_metadata(), context, payload)
            reviewed_preview = WorkspaceBundlePreview.model_validate(metadata["preview"])
            reviewed_file_hashes = metadata["file_hashes"]
            expected_destination_hashes = metadata["destination_file_hashes"]
            await context.report("Applying reviewed workspace entities", percent=30)
            async with get_db_context() as db:
                await _require_attempt(db, context)
                # Resolver progress cannot open another job-row transaction
                # while this atomic entity/checkpoint transaction holds it.
                buffered_progress: list[tuple[str, int, int]] = []

                async def buffer_progress(phase: str, current: int, total: int):
                    buffered_progress[:] = [(phase, current, total)]

                importer = WorkspaceBundleImporter(db, progress_fn=buffer_progress)
                journal = context.checkpoint
                resuming = (
                    journal is not None and journal.get("db_applied") is True
                    and journal.get("preview_id") == str(payload.preview_id)
                    and journal.get("package_sha256") == payload.package_sha256
                )
                projection = SolutionPackageWorkspaceProjection.from_preview(
                    inspected.preview, preview_id=payload.preview_id, work_dir=inspected.workspace,
                    organization_id=context.organization_id,
                )
                plan = await WorkspaceBundlePlanner(
                    db, preview_id=payload.preview_id, organization_id=context.organization_id,
                ).plan(projection)
                if resuming:
                    result = WorkspaceBundleImportResult(
                        imported_item_ids=frozenset(journal["imported_entity_ids"]),
                        selected_item_ids=frozenset(journal["selected_item_ids"]), operations=(),
                    )
                elif (
                    journal is not None and journal.get("db_apply_started") is True
                    and journal.get("preview_id") == str(payload.preview_id)
                    and journal.get("package_sha256") == payload.package_sha256
                    and _checkpoint_matches_replanned_bundle(
                        reviewed_preview, plan.preview, set(journal["selected_item_ids"]),
                        {key for key, value in payload.config_values.items()
                         if value.strip() and key in plan.manifest.configs and not any(
                             decision.item_id == f"entity:config:{plan.manifest.configs[key].id}"
                             and decision.action == "keep" for decision in payload.decisions)},
                    )
                    and reviewed_file_hashes == (plan.file_hashes or {})
                    and await _entered_configs_match(db, plan, payload)
                ):
                    # Retain the exact legacy precommit-journal recovery contract.
                    result = WorkspaceBundleImportResult(
                        imported_item_ids=frozenset(journal["imported_entity_ids"]),
                        selected_item_ids=frozenset(journal["selected_item_ids"]), operations=(),
                    )
                else:
                    _require_preview_is_current(reviewed_preview, reviewed_file_hashes,
                                                plan.preview, plan.file_hashes or {})
                    result = await importer.apply(plan, payload.decisions,
                        config_values=payload.config_values, updated_by=context.requested_by_email)
                journal = _journal_for_db_apply(payload, result) | {"db_applied": True}
                await context.checkpoint_in_transaction(db, journal,
                    phase="Database import committed; finalizing files")
                await db.commit()
            if buffered_progress:
                phase, current, total = buffered_progress[0]
                await context.report(phase, current=current, total=total)
            await context.report("Database import committed; finalizing files", percent=65)
            from src.core.cache import invalidate_config

            for key in {
                item.name for item in plan.preview.items
                if item.kind == "config" and (item.id in result.selected_item_ids
                    or (item.name in payload.config_values and not any(
                        decision.item_id == item.id and decision.action == "keep" for decision in payload.decisions)))
            }:
                await invalidate_config(str(context.organization_id) if context.organization_id else None, key)
            await context.report("Promoting selected workspace files", percent=70)
            async with get_db_context() as db:
                await _require_attempt(db, context)
                try:
                    promoted = await WorkspaceBundleImporter(db).promote_selected_files(
                        plan, result.selected_item_ids, file_index=FileIndexService(db),
                        expected_destination_hashes=expected_destination_hashes,
                    )
                    await _require_attempt(db, context)
                    await RepoSyncWriter(db).regenerate_manifest()
                    await _require_attempt(db, context)
                    await mark_repo_dirty()
                    completed = journal | {
                        "files_finalized": True, "promoted_files": promoted,
                        "requested_by": context.requested_by_user_id,
                        "organization_id": str(context.organization_id) if context.organization_id else None,
                    }
                    await context.checkpoint_in_transaction(db, completed, phase="Workspace files finalized")
                    await db.commit()
                except PlatformJobCancelled:
                    raise
                except WorkspaceBundleDecisionError as exc:
                    raise PlatformJobFailure("workspace_bundle_file_precondition_failed",
                        "A workspace file changed after preview; create a new preview before importing.", result=journal) from exc
                except Exception as exc:
                    raise PlatformJobFailure("workspace_bundle_finalize_failed",
                        "Workspace import database changes were committed but file finalization failed; retrying the durable job.",
                        retryable=True, result=journal) from exc
    await context.report("Workspace import complete", percent=100)
    async with get_db_context() as db:
        await _require_attempt(db, context)
        await storage.delete()
    return _import_result(result.imported_item_ids, promoted)


WORKSPACE_BUNDLE_IMPORT_DEFINITION = define_platform_job(
    "workspace.bundle_import", WorkspaceBundleImportPayload, run_workspace_bundle_import
)
