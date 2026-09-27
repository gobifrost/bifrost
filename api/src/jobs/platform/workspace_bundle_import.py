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
    PlatformJobDefinition,
    PlatformJobFailure,
    PlatformJobPolicy,
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
            expected_destination_hashes = metadata["destination_file_hashes"]
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
                    organization_id=(
                        UUID(metadata["organization_id"])
                        if metadata.get("organization_id") else None
                    ),
                )
                plan = await WorkspaceBundlePlanner(
                    db, preview_id=payload.preview_id,
                    organization_id=(
                        UUID(metadata["organization_id"])
                        if metadata.get("organization_id") else None
                    ),
                ).plan(projection)
                if resuming:
                    result = WorkspaceBundleImportResult(
                        imported_item_ids=frozenset(journal["imported_entity_ids"]),
                        selected_item_ids=frozenset(journal["selected_item_ids"]),
                        operations=(),
                    )
                elif (
                    journal is not None
                    and journal.get("db_apply_started") is True
                    and journal.get("preview_id") == str(payload.preview_id)
                    and journal.get("package_sha256") == payload.package_sha256
                    and _checkpoint_matches_replanned_bundle(
                        reviewed_preview, plan.preview, set(journal["selected_item_ids"]),
                        {
                            key for key, value in payload.config_values.items()
                            if value.strip() and key in plan.manifest.configs and not any(
                                decision.item_id == f"entity:config:{plan.manifest.configs[key].id}"
                                and decision.action == "keep"
                                for decision in payload.decisions
                            )
                        },
                    )
                    and reviewed_file_hashes == (plan.file_hashes or {})
                    and await _entered_configs_match(db, plan, payload)
                ):
                    # A runner may have been lost between db.commit() and the
                    # following checkpoint.  The pre-apply checkpoint plus the
                    # exact expected plan transition proves the entity changes
                    # committed, so resume only the idempotent file phase.
                    result = WorkspaceBundleImportResult(
                        imported_item_ids=frozenset(journal["imported_entity_ids"]),
                        selected_item_ids=frozenset(journal["selected_item_ids"]),
                        operations=(),
                    )
                else:
                    _require_preview_is_current(
                        reviewed_preview, reviewed_file_hashes, plan.preview, plan.file_hashes or {},
                    )
                    result = await importer.apply(
                        plan, payload.decisions, config_values=payload.config_values,
                        updated_by=context.requested_by_email,
                    )
                    await context.save_checkpoint(
                        _journal_for_db_apply(payload, result),
                        phase="Applying workspace entity changes",
                    )
                # Make entity upserts durable before the idempotent S3 phase.
                await context.report("Committing workspace entity changes", percent=60)
                await db.commit()
                from src.core.cache import invalidate_config

                selected_config_keys = {
                    item.name for item in plan.preview.items
                    if item.kind == "config" and (
                        item.id in result.selected_item_ids
                        or (item.name in payload.config_values and not any(
                            decision.item_id == item.id and decision.action == "keep"
                            for decision in payload.decisions
                        ))
                    )
                }
                for key in selected_config_keys:
                    await invalidate_config(
                        str(plan.organization_id) if plan.organization_id else None, key
                    )
                journal = _journal_for_db_apply(payload, result) | {"db_applied": True}
                await context.save_checkpoint(journal, phase="Database import committed; finalizing files")
                await context.report("Promoting selected workspace files", percent=70)
                try:
                    promoted = await importer.promote_selected_files(
                        plan,
                        result.selected_item_ids,
                        file_index=FileIndexService(db),
                        expected_destination_hashes=expected_destination_hashes,
                    )
                    await RepoSyncWriter(db).regenerate_manifest()
                    await db.commit()
                    await mark_repo_dirty()
                except WorkspaceBundleDecisionError as exc:
                    raise PlatformJobFailure(
                        "workspace_bundle_file_precondition_failed",
                        "A workspace file changed after preview; create a new preview before importing.",
                        result=journal,
                    ) from exc
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
