"""PlatformJob handlers for one reviewed Global Builder release."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import select

from src.core.database import get_db_context
from src.jobs.platform.base import (
    PlatformJobContext,
    PlatformJobDefinition,
    PlatformJobFailure,
    PlatformJobPolicy,
)
from src.jobs.platform.builder_global_operations import (
    apply_global_operations_for_release,
    global_operations_mcp_context,
    required_capabilities_for_changes,
    rollback_global_operations_for_release,
)
from src.models.orm.solution_builder import (
    SolutionGlobalOperationChange,
    SolutionGlobalWorkspaceApply,
)
from src.services.builder.global_operation_changes import (
    operation_change_applied_fingerprint,
)
from src.services.builder.global_workspace import (
    GlobalWorkspaceError,
    apply_global_workspace,
    finalize_global_workspace_release_revision,
    preflight_global_workspace_rollback,
    rollback_global_workspace,
)
from src.services.builder.runtime_authorization import (
    AuthorizedBuilderProject,
    BuilderRuntimeForbidden,
    authorize_builder_project,
)
from src.services.solutions.access import SolutionAction


class BuilderGlobalReleaseApplyPayload(BaseModel):
    solution_id: UUID
    from_revision_id: UUID | None = None
    to_revision_id: UUID | None = None
    approved_operation_changes: dict[UUID, str] = Field(default_factory=dict)


class BuilderGlobalReleaseRollbackPayload(BaseModel):
    solution_id: UUID
    source_apply_id: UUID | None = None
    approved_operation_changes: dict[UUID, str] = Field(default_factory=dict)


def _user_id(context: PlatformJobContext) -> UUID:
    return UUID(context.requested_by_user_id)


def _failure_message(exc: BaseException) -> str:
    if isinstance(exc, PlatformJobFailure):
        return exc.message
    return str(exc) or exc.__class__.__name__


async def _authorize_release(
    context: PlatformJobContext,
    payload: BuilderGlobalReleaseApplyPayload | BuilderGlobalReleaseRollbackPayload,
    *,
    includes_source: bool,
) -> AuthorizedBuilderProject:
    required_capabilities = {"builder.execute"}
    if includes_source:
        required_capabilities.add("repository.readwrite")
    async with get_db_context() as db:
        operation_capabilities = await required_capabilities_for_changes(
            db,
            solution_id=payload.solution_id,
            approved_changes=payload.approved_operation_changes,
        )
        required_capabilities.update(operation_capabilities)
        try:
            authorized = await authorize_builder_project(
                db,
                solution_id=payload.solution_id,
                requester_user_id=_user_id(context),
                action=SolutionAction.BUILD,
                required_capabilities=tuple(sorted(required_capabilities)),
            )
        except BuilderRuntimeForbidden as exc:
            raise PlatformJobFailure(
                "builder_authorization_revoked",
                "The requester no longer has permission to run this Global Builder release.",
            ) from exc
        if authorized.project.target_kind != "global_repo":
            raise PlatformJobFailure(
                "builder_target_invalid",
                "Global release jobs can only run for the Global Workspace.",
            )
    return authorized


async def _compensate_source_after_operation_failure(
    context: PlatformJobContext,
    payload: BuilderGlobalReleaseApplyPayload,
    *,
    source_apply_id: UUID | None,
    failure: BaseException,
) -> None:
    try:
        await context.log(
            "error",
            "builder_global_release_partial_apply",
            (
                "Global source changes were applied but operation changes failed: "
                f"{_failure_message(failure)}"
            ),
        )
    except Exception as log_exc:
        log_exc.add_note(
            "Global release source compensation continued after diagnostic log failure."
        )
    if source_apply_id is None:
        raise PlatformJobFailure(
            "global_release_partial_apply_uncompensated",
            (
                "Operation apply failed after source apply, but no durable source "
                "apply row was found for compensation. Original operation failure: "
                f"{_failure_message(failure)}."
            ),
            retryable=False,
        ) from failure
    async with get_db_context() as db:
        try:
            await rollback_global_workspace(
                db,
                solution_id=payload.solution_id,
                requested_by=_user_id(context),
                requested_by_email=context.requested_by_email,
                source_apply_id=source_apply_id,
                rollback_job_id=context.job_id,
            )
        except GlobalWorkspaceError as rollback_exc:
            raise PlatformJobFailure(
                "global_release_partial_apply_uncompensated",
                (
                    "Operation apply failed after source apply, and source "
                    "compensation also failed. Original operation failure: "
                    f"{_failure_message(failure)}. Source compensation failure: "
                    f"{rollback_exc}"
                ),
                retryable=False,
            ) from failure


async def _compensate_operations_after_finalize_failure(
    context: PlatformJobContext,
    payload: BuilderGlobalReleaseApplyPayload,
    authorized: AuthorizedBuilderProject,
    failure: BaseException,
) -> None:
    async with get_db_context() as db:
        rows = (
            await db.scalars(
                select(SolutionGlobalOperationChange).where(
                    SolutionGlobalOperationChange.solution_id == payload.solution_id,
                    SolutionGlobalOperationChange.apply_job_id == context.job_id,
                    SolutionGlobalOperationChange.state == "applied",
                )
            )
        ).all()
        if not rows:
            return
        approved = {
            row.id: operation_change_applied_fingerprint(row)
            for row in rows
        }
        try:
            await rollback_global_operations_for_release(
                db,
                solution_id=payload.solution_id,
                context=global_operations_mcp_context(
                    authorized,
                    solution_id=payload.solution_id,
                ),
                requested_by=_user_id(context),
                rollback_job_id=context.job_id,
                approved_changes=approved,
            )
        except Exception as rollback_exc:  # noqa: BLE001
            raise PlatformJobFailure(
                "global_release_partial_apply_uncompensated",
                "Global release finalization failed after operation changes applied, "
                "and operation compensation failed. "
                f"Original error: {_failure_message(failure)}; "
                f"compensation error: {_failure_message(rollback_exc)}",
                retryable=False,
            ) from rollback_exc


async def run_builder_global_release_apply(
    context: PlatformJobContext,
    payload: BuilderGlobalReleaseApplyPayload,
) -> dict[str, object]:
    """Apply one frozen Global source/operation release batch."""

    changed_paths: list[str] = []
    applied_operations: list[dict[str, object]] = []
    source_applied = False
    source_apply_id: UUID | None = None
    authorized = await _authorize_release(
        context,
        payload,
        includes_source=bool(
            (payload.from_revision_id and payload.to_revision_id)
            or payload.approved_operation_changes
        ),
    )
    if payload.from_revision_id and payload.to_revision_id:
        await context.report("Applying reviewed Global source changes", percent=15)
        async with get_db_context() as db:
            try:
                result = await apply_global_workspace(
                    db,
                    solution_id=payload.solution_id,
                    requested_by=_user_id(context),
                    requested_by_email=context.requested_by_email,
                    apply_job_id=context.job_id,
                    expected_from_revision_id=payload.from_revision_id,
                    expected_to_revision_id=payload.to_revision_id,
                    allow_staged_operations=bool(payload.approved_operation_changes),
                )
            except GlobalWorkspaceError as exc:
                raise PlatformJobFailure(
                    "global_release_source_apply_failed",
                    str(exc),
                    retryable=False,
                ) from exc
        changed_paths = result.changed_paths
        source_applied = True
        async with get_db_context() as db:
            source_apply_id = await db.scalar(
                select(SolutionGlobalWorkspaceApply.id).where(
                    SolutionGlobalWorkspaceApply.solution_id == payload.solution_id,
                    SolutionGlobalWorkspaceApply.apply_job_id == context.job_id,
                    SolutionGlobalWorkspaceApply.state == "applied",
                )
            )
    if payload.approved_operation_changes:
        try:
            await context.report("Applying reviewed Global operation changes", percent=65)
            async with get_db_context() as db:
                operations_result = await apply_global_operations_for_release(
                    db,
                    solution_id=payload.solution_id,
                    context=global_operations_mcp_context(
                        authorized,
                        solution_id=payload.solution_id,
                    ),
                    requested_by=_user_id(context),
                    apply_job_id=context.job_id,
                    approved_changes=payload.approved_operation_changes,
                )
        except Exception as exc:
            if source_applied:
                await _compensate_source_after_operation_failure(
                    context,
                    payload,
                    source_apply_id=source_apply_id,
                    failure=exc,
                )
            raise
        applied_operations = list(operations_result.get("changes") or [])
        try:
            async with get_db_context() as db:
                await finalize_global_workspace_release_revision(
                    db,
                    solution_id=payload.solution_id,
                    requested_by=_user_id(context),
                    apply_job_id=context.job_id,
                )
        except Exception as exc:
            await _compensate_operations_after_finalize_failure(
                context,
                payload,
                authorized,
                exc,
            )
            if source_applied:
                await _compensate_source_after_operation_failure(
                    context,
                    payload,
                    source_apply_id=source_apply_id,
                    failure=exc,
                )
            raise PlatformJobFailure(
                "global_release_finalize_failed",
                "Global release operations applied, but final source snapshot failed. "
                f"Original error: {exc}",
                retryable=False,
            ) from exc
    await context.report("Global release applied", percent=100)
    return {
        "solution_id": str(payload.solution_id),
        "changed_paths": changed_paths,
        "applied_operations": applied_operations,
    }


async def run_builder_global_release_rollback(
    context: PlatformJobContext,
    payload: BuilderGlobalReleaseRollbackPayload,
) -> dict[str, object]:
    """Roll back one frozen Global release batch."""

    rolled_back_operations: list[dict[str, object]] = []
    authorized = await _authorize_release(
        context,
        payload,
        includes_source=payload.source_apply_id is not None,
    )
    if payload.approved_operation_changes:
        if payload.source_apply_id:
            async with get_db_context() as db:
                try:
                    await preflight_global_workspace_rollback(
                        db,
                        solution_id=payload.solution_id,
                        source_apply_id=payload.source_apply_id,
                    )
                except GlobalWorkspaceError as exc:
                    raise PlatformJobFailure(
                        "global_release_source_rollback_preflight_failed",
                        str(exc),
                        retryable=False,
                    ) from exc
        await context.report("Rolling back reviewed Global operation changes", percent=20)
        async with get_db_context() as db:
            operations_result = await rollback_global_operations_for_release(
                db,
                solution_id=payload.solution_id,
                context=global_operations_mcp_context(
                    authorized,
                    solution_id=payload.solution_id,
                ),
                requested_by=_user_id(context),
                rollback_job_id=context.job_id,
                approved_changes=payload.approved_operation_changes,
            )
        rolled_back_operations = list(operations_result.get("changes") or [])
    changed_paths: list[str] = []
    if payload.source_apply_id:
        async with get_db_context() as db:
            try:
                await context.report("Restoring reviewed Global source revision", percent=70)
                result = await rollback_global_workspace(
                    db,
                    solution_id=payload.solution_id,
                    requested_by=_user_id(context),
                    requested_by_email=context.requested_by_email,
                    source_apply_id=payload.source_apply_id,
                    rollback_job_id=context.job_id,
                    allow_generated_manifest_drift=bool(
                        payload.approved_operation_changes
                    ),
                )
            except Exception as exc:
                if payload.approved_operation_changes:
                    try:
                        await context.log(
                            "error",
                            "builder_global_release_partial_rollback",
                            (
                                "Global operation changes were rolled back, but "
                                "source rollback failed: "
                                f"{_failure_message(exc)}"
                            ),
                        )
                    except Exception as log_exc:
                        log_exc.add_note(
                            "Global release partial rollback diagnostic log failed."
                        )
                    raise PlatformJobFailure(
                        "global_release_partial_rollback_uncompensated",
                        "Global operation changes were rolled back, but source "
                        f"rollback failed: {_failure_message(exc)}",
                        retryable=False,
                    ) from exc
                raise PlatformJobFailure(
                    "global_release_source_rollback_failed",
                    _failure_message(exc),
                    retryable=False,
                ) from exc
        changed_paths = result.changed_paths
    await context.report("Global release rolled back", percent=100)
    return {
        "solution_id": str(payload.solution_id),
        "changed_paths": changed_paths,
        "rolled_back_operations": rolled_back_operations,
    }


BUILDER_GLOBAL_RELEASE_APPLY_DEFINITION = PlatformJobDefinition(
    job_type="builder.global_release.apply",
    payload_version=1,
    payload_model=BuilderGlobalReleaseApplyPayload,
    handler=run_builder_global_release_apply,
    policy=PlatformJobPolicy(
        timeout_seconds=10 * 60,
        max_attempts=1,
        retry_on_runner_loss=False,
        min_memory_headroom_mb=256,
    ),
)

BUILDER_GLOBAL_RELEASE_ROLLBACK_DEFINITION = PlatformJobDefinition(
    job_type="builder.global_release.rollback",
    payload_version=1,
    payload_model=BuilderGlobalReleaseRollbackPayload,
    handler=run_builder_global_release_rollback,
    policy=PlatformJobPolicy(
        timeout_seconds=10 * 60,
        max_attempts=1,
        retry_on_runner_loss=False,
        min_memory_headroom_mb=256,
    ),
)
