"""Build unattended run context from its current durable user identity."""

from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from shared.run_lineage import RunLineage
from src.config import get_settings
from src.models.orm.users import User
from src.models.orm.organizations import Organization as OrganizationModel
from src.sdk.context import EventContext, ExecutionContext, Organization
from src.services.authorization.explain import load_run_user


async def create_unattended_context(
    db: AsyncSession,
    *,
    lineage: RunLineage,
    execution_id: str,
    org_id: str | None = None,
    event: EventContext | None = None,
    workflow_name: str = "",
    is_function_key: bool = False,
) -> ExecutionContext:
    """Use the selected run user for effects; role reach remains independent."""
    run_user = await load_run_user(db, lineage.run_user_id)
    actor = await db.get(User, lineage.run_user_id)
    if run_user is None or actor is None:
        raise ValueError("Unattended execution has no current run user")
    organization = None
    if org_id is not None:
        row = await db.get(OrganizationModel, UUID(org_id))
        if row is None:
            raise ValueError("Unattended execution organization was not found")
        organization = Organization(id=str(row.id), name=row.name, is_active=row.is_active, is_provider=row.is_provider)
    return ExecutionContext(
        user_id=str(actor.id),
        email=actor.email,
        name=actor.name or actor.email,
        scope=f"ORG:{org_id}" if org_id else "GLOBAL",
        organization=organization,
        is_platform_admin=run_user.is_platform_admin,
        is_function_key=is_function_key,
        execution_id=execution_id,
        workflow_name=workflow_name,
        public_url=get_settings().public_url,
        event=event,
    )
