"""Shared manual and automatic App SDK upgrade admission.

Automatic attempts use ordinary PlatformJobs, including their terminal history.
An app/target pair is attempted once; a failed attempt requires the existing
manual update action instead of being silently retried after every restart.
"""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from sqlalchemy import String, exists, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.app_sdk_upgrade_settings import AppSdkUpgradeSettingsService
from src.core.database import get_db_context
from src.jobs.platform.application_sdk_update import (
    APPLICATION_SDK_UPDATE_DEFINITION,
    ApplicationSdkUpdatePayload,
)
from src.models.orm.applications import Application
from src.models.orm.platform_jobs import PlatformJob
from src.scheduler.registry import ScheduledTaskOutcome
from src.services.application_sdk_status import (
    CurrentApplicationSdkMetadata,
    application_sdk_status,
    current_sdk_metadata,
    load_current_sdk_metadata,
    sdk_source_available,
)
from src.services.platform_job_memory_profiles import build_resource_memory_profile_key
from src.services.platform_jobs import (
    ACTIVE_PLATFORM_JOB_STATUSES,
    enqueue_platform_job,
    ensure_platform_job_notification,
    publish_platform_job_update,
)

logger = logging.getLogger(__name__)
_BATCH_SIZE = 20


class AppSdkUpgradeConflict(ValueError):
    """The app or its owning Solution already has a conflicting operation."""


def automatic_upgrade_eligible(
    app: Application, target: CurrentApplicationSdkMetadata,
) -> bool:
    if app.sdk_binding_api_major is not None:
        # A server implementation change never authorizes a binding rebuild or
        # a public SDK major migration.
        return False
    reviewed_contracts = (
        target.automatic_hosted_migration_from
        if target.binding_api_major is not None
        else target.automatic_upgrade_from
    )
    return (
        app.app_model == "standalone_v2"
        and app.active_deployment_id is not None
        and app.sdk_contract_version in reviewed_contracts
        and application_sdk_status(app, target) in ("update_available", "update_required")
        and sdk_source_available(app)
    )


def _automatic_key(app_id: UUID, target: CurrentApplicationSdkMetadata) -> str:
    return f"automatic:{app_id}:{_automatic_target_key(target)}"


def _automatic_target_key(target: CurrentApplicationSdkMetadata) -> str:
    if target.binding_api_major is not None:
        return f"binding-{target.binding_api_major}:{target.fingerprint}"
    return f"{target.contract_version}:{target.fingerprint}"


async def active_solution_sdk_conflict(db: AsyncSession, solution_id: UUID) -> bool:
    return bool(await db.scalar(
        select(exists().where(
            PlatformJob.resource_lock_key == f"solution:{solution_id}",
            PlatformJob.status.in_(ACTIVE_PLATFORM_JOB_STATUSES),
        ))
    ))


async def enqueue_application_sdk_upgrade(
    db: AsyncSession,
    app: Application,
    *,
    requested_by_user_id: UUID | str,
    requested_by_email: str,
    requested_by_name: str,
    target: CurrentApplicationSdkMetadata | None = None,
    automatic: bool = False,
) -> tuple[PlatformJob, bool] | None:
    """Share resource locks, target pinning, and admission across all callers."""
    target = target or await load_current_sdk_metadata()
    if not target.fingerprint or target.contract_version is None:
        raise AppSdkUpgradeConflict("Current App SDK provenance is unavailable.")

    original_scope = app.organization_id, app.solution_id
    if app.solution_id is not None:
        await db.execute(text(
            "SELECT pg_advisory_xact_lock("
            "hashtext('bifrost:solution-operation:' || :solution_id))"
        ), {"solution_id": str(app.solution_id)})
        if await active_solution_sdk_conflict(db, app.solution_id):
            raise AppSdkUpgradeConflict("A Solution deployment is already in progress for this App.")

    await db.execute(text(
        "SELECT pg_advisory_xact_lock("
        "hashtext('bifrost:app-sdk-upgrade:' || :application_id))"
    ), {"application_id": str(app.id)})
    fresh = await db.get(Application, app.id, populate_existing=True)
    if fresh is None:
        raise AppSdkUpgradeConflict("The App is no longer available.")
    if (fresh.organization_id, fresh.solution_id) != original_scope:
        raise AppSdkUpgradeConflict("The App's ownership changed. Refresh and try again.")
    app = fresh
    if app.sdk_binding_api_major is not None and app.sdk_binding_api_major != target.binding_api_major:
        raise AppSdkUpgradeConflict(
            "Changing an App's public SDK major requires an explicit source migration."
        )

    key = _automatic_key(app.id, target) if automatic else str(app.id)
    if automatic:
        if not (await AppSdkUpgradeSettingsService(db).get_settings()).enabled:
            return None
        if not automatic_upgrade_eligible(app, target):
            return None
        if await db.scalar(select(exists().where(
            PlatformJob.job_type == APPLICATION_SDK_UPDATE_DEFINITION.job_type,
            PlatformJob.dedupe_key == key,
        ))):
            return None

    active = await db.scalar(select(PlatformJob).where(
        PlatformJob.resource_lock_key == f"application:{app.id}",
        PlatformJob.status.in_(ACTIVE_PLATFORM_JOB_STATUSES),
    ).order_by(PlatformJob.created_at).limit(1))
    if active is not None:
        if automatic:
            return None
        if active.job_type == APPLICATION_SDK_UPDATE_DEFINITION.job_type:
            return active, True
        raise AppSdkUpgradeConflict("An App deployment is already in progress.")

    job, reused = await enqueue_platform_job(
        db,
        APPLICATION_SDK_UPDATE_DEFINITION,
        ApplicationSdkUpdatePayload(
            application_id=app.id,
            expected_active_deployment_id=app.active_deployment_id,
            expected_sdk_package_version=app.sdk_package_version,
            expected_sdk_fingerprint=app.sdk_fingerprint,
            expected_sdk_contract_version=app.sdk_contract_version,
            expected_sdk_binding_api_major=app.sdk_binding_api_major,
            expected_sdk_built_at=app.sdk_built_at,
            target_sdk_fingerprint=target.fingerprint,
            target_sdk_contract_version=target.contract_version,
            target_sdk_binding_api_major=target.binding_api_major,
            automatic_upgrade=automatic,
        ),
        dedupe_key=key,
        resource_lock_key=f"application:{app.id}",
        organization_id=app.organization_id,
        requested_by_user_id=requested_by_user_id,
        requested_by_email=requested_by_email,
        requested_by_name=requested_by_name,
        resource_type="application",
        resource_id=str(app.id),
        title=f"Updating SDK for {app.name}",
        action_url=f"/apps/{app.slug}",
        memory_profile_key=build_resource_memory_profile_key(
            APPLICATION_SDK_UPDATE_DEFINITION.job_type, app.id,
        ),
    )
    return job, reused


async def enqueue_automatic_app_sdk_upgrades() -> ScheduledTaskOutcome:
    """Bounded, elected startup reconciliation; compilation stays in workers."""
    jobs: list[PlatformJob] = []
    async with get_db_context() as db:
        if not (await AppSdkUpgradeSettingsService(db).get_settings()).enabled:
            return ScheduledTaskOutcome(summary="Automatic App SDK upgrades are disabled")
        target = await asyncio.to_thread(current_sdk_metadata)
        reviewed_contracts = (
            target.automatic_hosted_migration_from
            if target.binding_api_major is not None
            else target.automatic_upgrade_from
        )
        if not target.fingerprint or target.contract_version is None or not reviewed_contracts:
            return ScheduledTaskOutcome(summary="No automatic App SDK transition is declared")

        # Exclude all previous attempts before limiting so terminal rows cannot
        # strand later apps behind the first batch after a leader restart.
        automatic_key = (
            "automatic:" + Application.id.cast(String)
            + f":{_automatic_target_key(target)}"
        )
        attempted = exists().where(
            PlatformJob.job_type == APPLICATION_SDK_UPDATE_DEFINITION.job_type,
            PlatformJob.dedupe_key == automatic_key,
        )
        apps = list((await db.scalars(select(Application).where(
            Application.app_model == "standalone_v2",
            Application.active_deployment_id.is_not(None),
            Application.sdk_package_version.is_not(None),
            Application.sdk_package_version != "",
            Application.sdk_fingerprint.is_not(None),
            Application.sdk_fingerprint != "",
            Application.sdk_built_at.is_not(None),
            or_(Application.solution_id.is_(None), Application.repo_path.is_not(None)),
            Application.sdk_binding_api_major.is_(None),
            Application.sdk_contract_version.in_(reviewed_contracts),
            ~attempted,
            ~exists().where(
                PlatformJob.status.in_(ACTIVE_PLATFORM_JOB_STATUSES),
                or_(
                    PlatformJob.resource_lock_key == "application:" + Application.id.cast(String),
                    PlatformJob.resource_lock_key == "solution:" + Application.solution_id.cast(String),
                ),
            ),
        ).order_by(Application.id).limit(_BATCH_SIZE))).all())

        for app in apps:
            try:
                result = await enqueue_application_sdk_upgrade(
                    db, app,
                    requested_by_user_id="system",
                    requested_by_email="system@gobifrost.local",
                    requested_by_name="Bifrost Scheduler",
                    target=target, automatic=True,
                )
            except AppSdkUpgradeConflict:
                continue
            if result is None:
                continue
            job, reused = result
            if not reused:
                try:
                    await ensure_platform_job_notification(db, job)
                except Exception:
                    logger.warning("Automatic App SDK upgrade queued without a notification", exc_info=True)
                jobs.append(job)
        await db.commit()

    for job in jobs:
        await publish_platform_job_update(job)
    return ScheduledTaskOutcome(summary=f"Queued {len(jobs)} automatic App SDK upgrades")
