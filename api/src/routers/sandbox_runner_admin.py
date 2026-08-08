"""Platform-admin setup for native Builder sandbox execution."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import func, select

from src.config import get_settings
from src.core.auth import CurrentActiveUser, RequirePlatformAdmin
from src.core.db_deps import DbSession
from src.jobs.platform.sandbox_runner_provision import (
    SANDBOX_RUNNER_PROVISION_DEFINITION,
    SandboxRunnerProvisionPayload,
)
from src.models.contracts.platform_jobs import PlatformJobAccepted, PlatformJobStatus
from src.models.contracts.sandbox_runner import (
    SandboxRunnerConfigPublic,
    SandboxRunnerConfigSave,
    SandboxRunnerSetupState,
)
from src.models.orm.platform_jobs import PlatformJob
from src.services.platform_jobs import (
    ACTIVE_PLATFORM_JOB_STATUSES,
    ensure_platform_job_notification,
    enqueue_platform_job,
    publish_platform_job_update,
)
from src.services.sandbox_runner_config import (
    SandboxRunnerConfigService,
    get_builder_readiness,
)
from src.services.sandbox_runner_provisioning import configured_runner_image

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin/builder/runner",
    tags=["Builder Runner Configuration"],
    dependencies=[RequirePlatformAdmin],
)


@router.get("", response_model=SandboxRunnerSetupState)
async def get_runner_setup(
    request: Request,
    db: DbSession,
    user: CurrentActiveUser,
) -> SandboxRunnerSetupState:
    """Return masked configuration and live setup blockers."""
    del user
    config = await SandboxRunnerConfigService(db).get_config()
    _ai_configured, readiness = await get_builder_readiness(db)
    configured_url = get_settings().public_url.strip().rstrip("/")
    recommended_url = configured_url or str(request.base_url).rstrip("/")
    return SandboxRunnerSetupState(
        config=config,
        readiness=readiness,
        recommended_callback_base_url=recommended_url,
        runner_image=configured_runner_image(),
    )


@router.put("", response_model=SandboxRunnerConfigPublic)
async def save_runner_setup(
    body: SandboxRunnerConfigSave,
    db: DbSession,
    user: CurrentActiveUser,
) -> SandboxRunnerConfigPublic:
    """Save encrypted provider settings; connection state is never client-set."""
    await _require_no_active_sandbox_work(db)
    try:
        saved = await SandboxRunnerConfigService(db).save_config(
            body,
            updated_by=user.email,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await db.commit()
    return saved


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_runner_setup(
    db: DbSession,
    user: CurrentActiveUser,
) -> Response:
    """Remove provider settings only while no sandbox work is active."""
    del user
    await _require_no_active_sandbox_work(db)
    deleted = await SandboxRunnerConfigService(db).delete_config()
    if not deleted:
        raise HTTPException(status_code=404, detail="Sandbox runner is not configured")
    await db.commit()
    return Response(status_code=204)


@router.post(
    "/provision",
    response_model=PlatformJobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def provision_runner(
    response: Response,
    db: DbSession,
    user: CurrentActiveUser,
) -> PlatformJobAccepted:
    """Queue deployment and a real container self-test as one durable job."""
    service = SandboxRunnerConfigService(db)
    config = await service.get_config()
    if config is None:
        raise HTTPException(status_code=400, detail="Save runner settings first")
    _ai_configured, readiness = await get_builder_readiness(db)
    setup_blockers = {
        blocker.code for blocker in readiness.blockers
    } & {"credentials_missing", "callback_missing"}
    if setup_blockers:
        raise HTTPException(
            status_code=400,
            detail="Complete the provider credentials and callback address first",
        )

    job, reused = await enqueue_platform_job(
        db,
        SANDBOX_RUNNER_PROVISION_DEFINITION,
        SandboxRunnerProvisionPayload(provider=config.provider),
        dedupe_key="global",
        resource_lock_key="sandbox-runner:global",
        priority=500,
        organization_id=None,
        requested_by_user_id=user.user_id,
        requested_by_email=user.email,
        requested_by_name=user.name or user.email,
        resource_type="sandbox_runner",
        resource_id="global",
        title="Setting up Builder runner",
        action_url="/settings/builder",
    )
    if job.notification_id is None:
        try:
            await ensure_platform_job_notification(db, job)
        except Exception:
            logger.warning(
                "Sandbox runner provisioning queued without a notification",
                extra={"platform_job_id": str(job.id)},
                exc_info=True,
            )
    await db.commit()
    await db.refresh(job)
    await publish_platform_job_update(job)
    response.headers["Location"] = f"/api/platform-jobs/{job.id}"
    return PlatformJobAccepted(
        job_id=job.id,
        status=PlatformJobStatus(job.status),
        reused=reused,
        notification_id=job.notification_id,
    )


async def _require_no_active_sandbox_work(db: DbSession) -> None:
    count = await db.scalar(
        select(func.count(PlatformJob.id)).where(
            PlatformJob.job_type.in_(
                (
                    "sandbox.runner.provision",
                    "solution.build",
                    "solution.builder.turn",
                )
            ),
            PlatformJob.status.in_(ACTIVE_PLATFORM_JOB_STATUSES),
        )
    )
    if count:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Wait for active Builder setup or work to finish before changing the runner",
        )


__all__ = ["router"]
