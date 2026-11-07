"""GitHub App reconciliation uses the shared durable PlatformJob runner."""

from pydantic import BaseModel

from shared.github_app_auth import GitHubAppError
from shared.models import GitHubAppMaintenancePayload
from src.core.database import get_db_context
from src.jobs.platform.base import PlatformJobContext, PlatformJobFailure, define_platform_job
from src.scheduler.registry import ScheduledTaskOutcome


async def run_reconcile(context: PlatformJobContext, payload: BaseModel) -> dict:
    from shared.github_app_recovery import reconcile
    from uuid import UUID
    from src.core.principal import UserPrincipal
    from src.models.orm.users import User
    from src.services.authorization.enforce import allows_permission, load_caller

    if context.requested_by_user_id != "system":
        async with get_db_context() as db:
            user = await db.get(User, UUID(context.requested_by_user_id))
            if user is None or not user.is_active:
                raise PlatformJobFailure("requester_unavailable", "The requesting administrator is no longer active")
            caller = await load_caller(db, UserPrincipal(user.id, user.email, user.organization_id))
            if not allows_permission(caller, "platform.readwrite", None) or not allows_permission(caller, "secrets.read", None):
                raise PlatformJobFailure("permission_denied", "The requesting administrator no longer has GitHub App recovery access")

    try:
        return await reconcile(context)
    except GitHubAppError as exc:
        # Provider cooldown is shared; do not churn a retrying job against it.
        raise PlatformJobFailure(exc.code, str(exc), retryable=False) from None


GITHUB_APP_RECONCILE_DEFINITION = define_platform_job(
    "github_app.reconcile", GitHubAppMaintenancePayload, run_reconcile)


async def enqueue_automatic_reconcile() -> ScheduledTaskOutcome:
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import delete
    from src.jobs.platform.system_maintenance import enqueue_system_maintenance
    from src.models.orm.github_app import GitHubAppDelivery, GitHubAppRegistration

    async with get_db_context() as db:
        # This short maintenance operation needs no GitHub credential and
        # continues after an App is disconnected.
        await db.execute(delete(GitHubAppDelivery).where(
            GitHubAppDelivery.received_at < datetime.now(timezone.utc) - timedelta(days=30)))
        row = await db.get(GitHubAppRegistration, 1)
        await db.commit()
        if row is None or not row.enabled:
            return ScheduledTaskOutcome(summary="GitHub App is not configured")
    return await enqueue_system_maintenance(GITHUB_APP_RECONCILE_DEFINITION,
        GitHubAppMaintenancePayload(), title="Reconcile GitHub App")
