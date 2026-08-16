"""Durable handlers for network-bound recurring platform maintenance."""

from __future__ import annotations

from pydantic import BaseModel

from src.core.database import get_db_context
from src.jobs.platform.base import (
    PlatformJobContext,
    PlatformJobDefinition,
    PlatformJobPolicy,
)
from src.scheduler.registry import ScheduledTaskOutcome
from src.services.platform_jobs import enqueue_platform_job, publish_platform_job_update


class OAuthRefreshPayload(BaseModel):
    trigger_type: str = "automatic"
    refresh_threshold_minutes: int | None = 20


class EmptyMaintenancePayload(BaseModel):
    pass


async def run_oauth_refresh(
    context: PlatformJobContext, payload: OAuthRefreshPayload
) -> dict:
    from src.jobs.schedulers.oauth_token_refresh import run_refresh_job

    await context.report("Finding tokens that need refresh", percent=5)
    await context.log("info", "oauth_refresh_started", "OAuth token refresh sweep started")
    result = await run_refresh_job(
        trigger_type=payload.trigger_type,
        trigger_user=context.requested_by_email,
        refresh_threshold_minutes=payload.refresh_threshold_minutes,
    )
    await context.report("OAuth refresh sweep complete", percent=100)
    await context.log(
        "warning" if result.get("refresh_failed") else "info",
        "oauth_refresh_completed",
        (
            f"OAuth refresh completed: {result.get('refreshed_successfully', 0)} "
            f"succeeded, {result.get('refresh_failed', 0)} failed"
        ),
    )
    return result


async def run_webhook_renewal(
    context: PlatformJobContext, payload: EmptyMaintenancePayload
) -> dict:
    from src.jobs.schedulers.webhook_renewal import renew_expiring_webhooks

    await context.report("Finding webhook subscriptions to renew", percent=5)
    result = await renew_expiring_webhooks()
    await context.report("Webhook renewal sweep complete", percent=100)
    await context.log(
        "warning" if result.get("renewal_failed") else "info",
        "webhook_renewal_completed",
        (
            f"Webhook renewal completed: {result.get('renewed_successfully', 0)} "
            f"succeeded, {result.get('renewal_failed', 0)} failed"
        ),
    )
    return result


async def run_solution_update_check(
    context: PlatformJobContext, payload: EmptyMaintenancePayload
) -> dict:
    from src.jobs.schedulers.solution_update_check import check_solution_updates

    await context.report("Checking connected Solution repositories", percent=5)
    result = await check_solution_updates()
    await context.report("Solution update check complete", percent=100)
    await context.log(
        "info",
        "solution_update_check_completed",
        (
            f"Solution update check completed: {result.get('checked', 0)} checked, "
            f"{result.get('updates_found', 0)} updates found"
        ),
    )
    return result


async def run_file_index_reconciliation(
    context: PlatformJobContext, payload: EmptyMaintenancePayload
) -> dict:
    from src.services.file_index_reconciler import reconcile_file_index

    await context.report("Reconciling the workspace file index", percent=5)
    async with get_db_context() as db:
        result = await reconcile_file_index(db)
    await context.report("Workspace file index reconciled", percent=100)
    await context.log(
        "info",
        "file_index_reconciliation_completed",
        (
            f"File index reconciliation completed: {result['added']} added, "
            f"{result['removed']} removed, {result['reverse_synced']} reverse-synced"
        ),
    )
    return result


async def run_artifact_retention_cleanup(
    context: PlatformJobContext, payload: EmptyMaintenancePayload
) -> dict:
    from src.services.artifact_retention import (
        ArtifactRetentionSettingsService,
        cleanup_expired_chat_artifacts,
    )

    await context.report("Finding expired artifacts", percent=5)
    async with get_db_context() as db:
        settings = await ArtifactRetentionSettingsService(db).get_settings()
        deleted, failed = await cleanup_expired_chat_artifacts(db)
        await db.commit()

    await context.report("Artifact retention cleanup complete", percent=100)
    await context.log(
        "warning" if failed else "info",
        "artifact_retention_cleanup_completed",
        f"Artifact retention cleanup deleted {deleted} files with {failed} failures",
    )
    return {
        "enabled": settings.enabled,
        "retention_days": settings.retention_days,
        "deleted_count": deleted,
        "failed_count": failed,
    }


OAUTH_REFRESH_DEFINITION = PlatformJobDefinition(
    job_type="oauth.refresh",
    payload_version=1,
    payload_model=OAuthRefreshPayload,
    handler=run_oauth_refresh,
    policy=PlatformJobPolicy(
        timeout_seconds=15 * 60,
        max_attempts=2,
        max_concurrency=1,
        min_memory_headroom_mb=128,
    ),
)

WEBHOOK_RENEWAL_DEFINITION = PlatformJobDefinition(
    job_type="webhook.renew",
    payload_version=1,
    payload_model=EmptyMaintenancePayload,
    handler=run_webhook_renewal,
    policy=PlatformJobPolicy(
        timeout_seconds=30 * 60,
        max_attempts=2,
        max_concurrency=1,
        min_memory_headroom_mb=128,
    ),
)

SOLUTION_UPDATE_CHECK_DEFINITION = PlatformJobDefinition(
    job_type="solution.update_check",
    payload_version=1,
    payload_model=EmptyMaintenancePayload,
    handler=run_solution_update_check,
    policy=PlatformJobPolicy(
        timeout_seconds=30 * 60,
        max_attempts=2,
        max_concurrency=1,
        min_memory_headroom_mb=256,
    ),
)

FILE_INDEX_RECONCILIATION_DEFINITION = PlatformJobDefinition(
    job_type="workspace.file_index_reconcile",
    payload_version=1,
    payload_model=EmptyMaintenancePayload,
    handler=run_file_index_reconciliation,
    policy=PlatformJobPolicy(
        timeout_seconds=60 * 60,
        max_attempts=2,
        max_concurrency=1,
        min_memory_headroom_mb=256,
    ),
)

ARTIFACT_RETENTION_CLEANUP_DEFINITION = PlatformJobDefinition(
    job_type="artifact.retention_cleanup",
    payload_version=1,
    payload_model=EmptyMaintenancePayload,
    handler=run_artifact_retention_cleanup,
    policy=PlatformJobPolicy(
        timeout_seconds=30 * 60,
        max_attempts=2,
        max_concurrency=1,
        min_memory_headroom_mb=128,
    ),
)


async def enqueue_system_maintenance(
    definition: PlatformJobDefinition,
    payload: BaseModel,
    *,
    title: str,
) -> ScheduledTaskOutcome:
    async with get_db_context() as db:
        job, reused = await enqueue_platform_job(
            db,
            definition,
            payload,
            dedupe_key="automatic",
            resource_lock_key=definition.job_type,
            priority=1000,
            organization_id=None,
            requested_by_user_id="system",
            requested_by_email="system@gobifrost.local",
            requested_by_name="Bifrost Scheduler",
            resource_type="system",
            resource_id=definition.job_type,
            title=title,
            action_url="/diagnostics",
        )
        await db.commit()
    await publish_platform_job_update(job)
    return ScheduledTaskOutcome(
        summary="Reused active job" if reused else "Durable job enqueued",
        platform_job_id=job.id,
    )


async def enqueue_automatic_oauth_refresh() -> ScheduledTaskOutcome:
    return await enqueue_system_maintenance(
        OAUTH_REFRESH_DEFINITION,
        OAuthRefreshPayload(),
        title="Refresh expiring OAuth tokens",
    )


async def enqueue_automatic_webhook_renewal() -> ScheduledTaskOutcome:
    return await enqueue_system_maintenance(
        WEBHOOK_RENEWAL_DEFINITION,
        EmptyMaintenancePayload(),
        title="Renew webhook subscriptions",
    )


async def enqueue_automatic_solution_update_check() -> ScheduledTaskOutcome:
    return await enqueue_system_maintenance(
        SOLUTION_UPDATE_CHECK_DEFINITION,
        EmptyMaintenancePayload(),
        title="Check Solution updates",
    )


async def enqueue_automatic_file_index_reconciliation() -> ScheduledTaskOutcome:
    return await enqueue_system_maintenance(
        FILE_INDEX_RECONCILIATION_DEFINITION,
        EmptyMaintenancePayload(),
        title="Reconcile workspace file index",
    )


async def enqueue_automatic_artifact_retention_cleanup() -> ScheduledTaskOutcome:
    return await enqueue_system_maintenance(
        ARTIFACT_RETENTION_CLEANUP_DEFINITION,
        EmptyMaintenancePayload(),
        title="Clean up expired artifacts",
    )
