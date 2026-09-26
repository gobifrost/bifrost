"""Shared business service for SDK video generation PlatformJobs.

Single implementation used by the HTTP handler
(``api/src/routers/cli.py::sdk_generate_video_artifact``) serving
external SDK callers and reached by workflow children over the
worker-local engine socket with a parent-derived principal, so results
are identical by construction.

Covers the two fixed operations only:

- ``artifacts.create_video`` enqueue (``POST /api/sdk/artifacts/video``)
  into the canonical ``SDK_VIDEO_GENERATION_DEFINITION`` PlatformJob;
- SDK video status observation (``GET /api/platform-jobs/{id}`` shape)
  for local polling without recursive HTTP requests.

The caller passes an explicit trusted actor (the auth-verified
``UserPrincipal``) plus the DB session — never a FastAPI Request and
never a child-provided actor claim. Requester/org/resource metadata,
deduplication, notification creation, and the commit/refresh/update
ordering match the historical handler exactly. The HTTP adapter keeps
transport-specific concerns only: the 202 status, the ``Location``
header, and ``HTTPException`` mapping.

Status visibility is shared by construction: HTTP and worker-local
callers use the same requester-visibility rule (owner requester or
platform admin reads as present; anything else reads as missing) and the
same ``PlatformJobPublic`` serialization. The worker-local entry point is
fixed to SDK video jobs — it rejects other job types as missing — so
no generic job API is added. Cancellation and the other platform-job
routes keep their existing router behavior.

Parent-side only: imports SQLAlchemy sessions and ORM-backed services.
A workflow child never imports this module (it stays DB-free and reaches
it over the engine socket).
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.principal import UserPrincipal
from src.models.contracts.artifacts import VideoArtifactSpec
from src.models.contracts.platform_jobs import (
    PlatformJobAccepted,
    PlatformJobPublic,
)
from src.models.orm.platform_jobs import PlatformJob

logger = logging.getLogger(__name__)


class SdkVideoJobError(Exception):
    """SDK video job failure with an HTTP-style status.

    Raised by the shared service so the HTTP handler (``HTTPException``)
    and callers reached over the worker-local engine socket read the same status/detail. Currently 404 only (missing or
    not visible job, or a non-video job on the fixed local status call),
    matching the historical status handler exactly.
    """

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


async def enqueue_sdk_video_job(
    db: AsyncSession,
    principal: UserPrincipal,
    *,
    spec: VideoArtifactSpec,
    workspace_id: UUID | None = None,
    execution_id: UUID | None = None,
) -> tuple[PlatformJob, bool]:
    """Enqueue the canonical SDK video generation PlatformJob.

    Metadata matches the historical handler exactly: no deduplication
    key, the caller's org and requester identity, ``resource_type``
    ``"artifact"`` keyed on the requested filename, title
    ``"Generating {filename}"``, and no action URL.

    Flushes the row; the caller (HTTP adapter or local
    dispatcher) owns the commit via :func:`finalize_sdk_video_job`.
    """
    from src.jobs.platform.video_generation import (
        SDK_VIDEO_GENERATION_DEFINITION,
        SDKVideoGenerationPayload,
    )
    from src.services.platform_jobs import enqueue_platform_job

    return await enqueue_platform_job(
        db,
        SDK_VIDEO_GENERATION_DEFINITION,
        SDKVideoGenerationPayload(
            filename=spec.filename,
            prompt=spec.prompt,
            workspace_id=workspace_id,
            execution_id=execution_id,
        ),
        dedupe_key=None,
        organization_id=principal.organization_id,
        requested_by_user_id=principal.user_id,
        requested_by_email=principal.email,
        requested_by_name=principal.name or principal.email,
        resource_type="artifact",
        resource_id=spec.filename,
        title=f"Generating {spec.filename}",
        action_url=None,
    )


async def finalize_sdk_video_job(db: AsyncSession, job: PlatformJob) -> None:
    """Attach the notification, commit, refresh, and publish the update.

    Ordering matches the historical handler exactly: best-effort
    notification creation (failures only warn, never fail the enqueue),
    then commit, then refresh, then the WebSocket/notification update.
    """
    from src.services.platform_jobs import (
        ensure_platform_job_notification,
        publish_platform_job_update,
    )

    try:
        await ensure_platform_job_notification(db, job)
    except Exception:
        logger.warning(
            "SDK video generation queued without a progress notification",
            extra={"platform_job_id": str(job.id)},
            exc_info=True,
        )
    await db.commit()
    await db.refresh(job)
    await publish_platform_job_update(job)


def sdk_video_job_accepted(
    job: PlatformJob, reused: bool
) -> PlatformJobAccepted:
    """Build the enqueue accepted payload from the committed job."""
    return PlatformJobAccepted(
        job_id=job.id,
        notification_id=job.notification_id,
        status=job.status,  # type: ignore[arg-type]
        reused=reused,
    )


def can_read_platform_job(job: PlatformJob, user: UserPrincipal) -> bool:
    """Requester-visibility rule shared by HTTP and local status reads."""
    return user.is_platform_admin or job.requested_by_user_id == str(
        user.user_id
    )


async def get_visible_platform_job(
    db: AsyncSession,
    user: UserPrincipal,
    job_id: UUID,
) -> PlatformJob:
    """Load a platform job visible to ``user`` or raise transport-neutral 404.

    A missing row or a row outside the caller's visibility reads as
    missing (404), never forbidden — matching the historical status
    handler exactly.
    """
    job = await db.get(PlatformJob, job_id)
    if job is None or not can_read_platform_job(job, user):
        raise SdkVideoJobError(404, "Platform job not found")
    return job


async def get_platform_job_status(
    db: AsyncSession,
    user: UserPrincipal,
    job_id: UUID,
) -> PlatformJobPublic:
    """Serialize a visible platform job in the shared public shape."""
    from src.services.platform_jobs import platform_job_to_public

    return platform_job_to_public(
        await get_visible_platform_job(db, user, job_id)
    )


async def get_sdk_video_job_status(
    db: AsyncSession,
    user: UserPrincipal,
    job_id: UUID,
) -> PlatformJobPublic:
    """Serialize a visible SDK video job for fixed local polling.

    Shares the requester-visibility rule and ``PlatformJobPublic`` shape
    with the HTTP status endpoint. Rejects non-video job types as
    missing so the worker-local entry point stays fixed to SDK video status —
    no generic job API.
    """
    from src.jobs.platform.video_generation import (
        SDK_VIDEO_GENERATION_DEFINITION,
    )
    from src.services.platform_jobs import platform_job_to_public

    job = await get_visible_platform_job(db, user, job_id)
    if job.job_type != SDK_VIDEO_GENERATION_DEFINITION.job_type:
        raise SdkVideoJobError(404, "Platform job not found")
    return platform_job_to_public(job)


__all__ = [
    "SdkVideoJobError",
    "can_read_platform_job",
    "enqueue_sdk_video_job",
    "finalize_sdk_video_job",
    "get_platform_job_status",
    "get_sdk_video_job_status",
    "get_visible_platform_job",
    "sdk_video_job_accepted",
]
