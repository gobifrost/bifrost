"""Re-evaluate a stored resource-entry decision from current original roles."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.principal import UserPrincipal
from src.models.orm.agents import Agent
from src.repositories.agents import AgentRepository
from src.repositories.org_scoped import OrgScopedRepository
from src.repositories.workflows import WorkflowRepository
from src.services.agent_write_policy import can_read_agent
from src.services.authorization.enforce import Caller
from src.services.authorization.explain import load_run_user

async def run_user_may_open(
    session: AsyncSession,
    repository: type[OrgScopedRepository],
    run_user_id: UUID,
    entity_id: UUID,
) -> bool:
    """Whether ``run_user_id`` may open entity ``entity_id`` through ``repository``."""
    run_user = await load_run_user(session, run_user_id)
    if run_user is None:
        return False
    caller = Caller(
        UserPrincipal(
            user_id=run_user_id,
            email="access-check-replay",
            organization_id=run_user.home,
            is_active=run_user.is_active,
            is_external=run_user.ctx.is_external,
        ),
        run_user.ctx,
    )
    if repository is AgentRepository:
        agent = await session.get(Agent, entity_id, populate_existing=True)
        return agent is not None and await can_read_agent(session, caller, agent)
    if repository is WorkflowRepository:
        workflow = await WorkflowRepository(
            session, org_id=run_user.home, caller=caller,
        ).get(id=entity_id)
        return workflow is not None
    raise ValueError("Unsupported entry resource")
