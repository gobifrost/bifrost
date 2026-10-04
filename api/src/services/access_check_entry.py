"""Whether a run's user may open an entity themselves (report-only entry checks).

Today an agent nobody started opens workflows and agents with execution
credentials; the model opens them as the run's user, exactly like a person:
the entity's organization scope, access level and roles.
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.users import User
from src.core.exceptions import AccessDeniedError
from src.repositories.org_scoped import OrgScopedRepository

logger = logging.getLogger(__name__)


async def run_user_may_open(
    session: AsyncSession,
    repository: type[OrgScopedRepository],
    run_user_id: UUID,
    entity_id: UUID,
) -> bool:
    """Whether ``run_user_id`` may open entity ``entity_id`` through ``repository``."""
    user = (
        await session.execute(
            select(User.organization_id, User.is_superuser, User.is_external).where(User.id == run_user_id)
        )
    ).one_or_none()
    if user is None:
        return False
    organization_id, is_superuser, is_external = user
    repo = repository(
        session,
        org_id=organization_id,
        user_id=run_user_id,
        is_superuser=is_superuser,
        is_external=is_external,
    )
    try:
        await repo.can_access(id=entity_id)
    except AccessDeniedError:
        return False
    return True


async def check_agent_run_entry(run_user_id: UUID, agent_id: UUID, organization_id: UUID | None) -> None:
    """Report-only: an agent run nobody started opens its agent as its identity.

    Written straight away with its own session (there is no request); never
    raises.
    """
    from shared.access_checks import Collector, Note
    from src.core.database import get_db_context
    from src.repositories.agents import AgentRepository
    from src.services.access_check_writer import flush

    try:
        async with get_db_context() as db:
            try:
                allowed = await run_user_may_open(db, AgentRepository, run_user_id, agent_id)
                note = Note("entry", organization_id, {"allowed": allowed, "subject": f"agent:{agent_id}"})
            except Exception as exc:
                note = Note("entry", organization_id, {"gap": f"observer_error:{type(exc).__name__}"})
            collector = Collector(execution_id=None, run_user_id=run_user_id, workflow_id=None, notes=[note])
            await flush(db, collector, operation="event.agent_run", route=None)
    except Exception:
        logger.warning("agent run entry check not written (agent=%s)", agent_id, exc_info=True)
