"""Current-caller visibility rules for durable platform jobs."""

from __future__ import annotations

from sqlalchemy import and_, or_, true

from src.models.orm.platform_jobs import PlatformJob
from src.services.authorization.enforce import (
    Caller,
    allows_permission,
    permitted_organizations,
    PermissionDenied,
    require_named_permission,
    user_reach,
)


class PlatformJobAccessDenied(PermissionError):
    """Visibility is intentionally represented as a transport-layer 404."""


def require_platform_job_access(
    caller: Caller, job: PlatformJob, *, cancel: bool = False
) -> None:
    """Record and enforce durable-job access for one already loaded row."""
    if not user_reach(caller).covers(job.organization_id):
        raise PlatformJobAccessDenied
    if platform_job_owned_by(caller, job):
        return
    permission = "platformjobs.readwrite.all" if cancel else "platformjobs.read.all"
    try:
        require_named_permission(caller, permission, job.organization_id)
    except PermissionDenied as exc:
        raise PlatformJobAccessDenied from exc


def platform_job_owned_by(caller: Caller, job: PlatformJob) -> bool:
    """Whether the original caller requested this durable job."""
    return caller.ctx is not None and job.requested_by_user_id == str(caller.ctx.user_id)


def can_read_platform_job(caller: Caller, job: PlatformJob, *, cancel: bool = False) -> bool:
    """Own reachable jobs are visible; other jobs need the scoped named power."""
    if not user_reach(caller).covers(job.organization_id):
        return False
    if platform_job_owned_by(caller, job):
        return True
    return allows_permission(
        caller,
        "platformjobs.readwrite.all" if cancel else "platformjobs.read.all",
        job.organization_id,
    )


def platform_job_visibility_filter(caller: Caller):
    """SQL predicate applied before platform-job list counting and paging."""
    reach = user_reach(caller).where(PlatformJob.organization_id)
    in_reach = true() if reach is None else reach
    if caller.ctx is None:
        return PlatformJob.id.is_(None)
    permitted = permitted_organizations(caller, "platformjobs.read.all").where(
        PlatformJob.organization_id
    )
    others = true() if permitted is None else permitted
    return and_(
        in_reach,
        or_(PlatformJob.requested_by_user_id == str(caller.ctx.user_id), others),
    )
