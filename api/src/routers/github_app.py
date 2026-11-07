"""Thin administration and tenant inventory endpoints for the optional GitHub App."""

from typing import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from shared import github_app
from shared.github_app_auth import GitHubAppError
from shared.models import GitHubAppBind, GitHubAppConfigure, GitHubAppConfigView, GitHubAppInstallations, GitHubAppInstallationView, GitHubAppMaintenancePayload
from src.core.auth import CurrentActiveUser
from src.core.db_deps import DbSession
from src.models.contracts.platform_jobs import PlatformJobAccepted, PlatformJobStatus
from src.models.orm.github_app import GitHubAppRegistration
from src.services.authorization.enforce import authorize_operation, org_target, require_permission
from src.services.authorization.evaluator import GLOBAL
from src.services.audit import emit_audit


async def errors(_user: CurrentActiveUser) -> AsyncIterator[None]:
    try:
        yield
    except GitHubAppError as exc:
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
        raise HTTPException(exc.status_code, str(exc), headers=headers) from None


router = APIRouter(prefix="/api/github-app", tags=["GitHub App"], dependencies=[Depends(errors)])


@router.get("/config", response_model=GitHubAppConfigView)
async def read_config(db: DbSession, user: CurrentActiveUser) -> GitHubAppConfigView:
    await authorize_operation(db, user, "GET /api/github-app/config", GLOBAL)
    return github_app.config_view(await db.get(GitHubAppRegistration, 1))


@router.put("/config", response_model=GitHubAppConfigView)
async def save_config(body: GitHubAppConfigure, db: DbSession, user: CurrentActiveUser) -> GitHubAppConfigView:
    caller = await authorize_operation(db, user, "PUT /api/github-app/config", GLOBAL)
    require_permission(caller, "secrets.read", None)
    view = await github_app.configure(db, body)
    await emit_audit(db, "github_app.configure", resource_type="github_app", details={"app_id": view.app_id})
    return view


@router.delete("/config", response_model=GitHubAppConfigView)
async def delete_config(db: DbSession, user: CurrentActiveUser) -> GitHubAppConfigView:
    await authorize_operation(db, user, "DELETE /api/github-app/config", GLOBAL)
    await github_app.disable(db)
    await emit_audit(db, "github_app.disconnect", resource_type="github_app")
    return github_app.config_view(await db.get(GitHubAppRegistration, 1))


@router.post("/installations", response_model=GitHubAppInstallationView, status_code=201)
async def bind_installation(body: GitHubAppBind, db: DbSession, user: CurrentActiveUser) -> GitHubAppInstallationView:
    caller = await authorize_operation(db, user, "POST /api/github-app/installations", GLOBAL)
    require_permission(caller, "secrets.read", None)
    row = await github_app.bind(db, body, user.email)
    await emit_audit(db, "github_app.bind", resource_type="github_app_installation", resource_id=row.id,
                     details={"installation_id": row.installation_id, "organization_id": str(row.organization_id)})
    return github_app.installation_view(row)


@router.get("/installations", response_model=GitHubAppInstallations)
async def list_installations(organization_id: UUID, db: DbSession, user: CurrentActiveUser) -> GitHubAppInstallations:
    await authorize_operation(db, user, "GET /api/github-app/installations", org_target(organization_id))
    return GitHubAppInstallations(installations=await github_app.list_installations(db, organization_id))


@router.delete("/installations/{binding_id}", response_model=GitHubAppInstallationView)
async def disconnect_installation(binding_id: UUID, db: DbSession, user: CurrentActiveUser) -> GitHubAppInstallationView:
    await authorize_operation(db, user, "DELETE /api/github-app/installations/{binding_id}", GLOBAL)
    view = await github_app.disconnect_installation(db, binding_id)
    await emit_audit(db, "github_app.disconnect_installation", resource_type="github_app_installation", resource_id=binding_id)
    return view


@router.post("/reconcile", response_model=PlatformJobAccepted, status_code=202)
async def reconcile(db: DbSession, user: CurrentActiveUser) -> PlatformJobAccepted:
    caller = await authorize_operation(db, user, "POST /api/github-app/reconcile", GLOBAL)
    require_permission(caller, "secrets.read", None)
    from src.jobs.platform.github_app import GITHUB_APP_RECONCILE_DEFINITION
    from src.services.platform_jobs import enqueue_platform_job, ensure_platform_job_notification, publish_platform_job_update

    await github_app.credentials(db)
    job, reused = await enqueue_platform_job(db, GITHUB_APP_RECONCILE_DEFINITION,
        GitHubAppMaintenancePayload(), dedupe_key="manual", resource_lock_key=GITHUB_APP_RECONCILE_DEFINITION.job_type,
        organization_id=None, requested_by_user_id=user.user_id, requested_by_email=user.email,
        requested_by_name=user.name or user.email, resource_type="system", resource_id="github-app",
        title="Reconcile GitHub App", action_url="/settings/github-app")
    if job.notification_id is None:
        await ensure_platform_job_notification(db, job)
    await db.commit()
    await db.refresh(job)
    await publish_platform_job_update(job)
    return PlatformJobAccepted(job_id=job.id, status=PlatformJobStatus(job.status), reused=reused, notification_id=job.notification_id)
