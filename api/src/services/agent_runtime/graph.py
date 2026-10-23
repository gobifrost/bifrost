"""Authorized agent-delegation graph queries shared by runtime surfaces."""

from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.orm.agents import Agent, AgentDelegation

MAX_COMPOSITION_DEPTH = 5


async def load_approved_child(
    session: AsyncSession,
    *,
    parent: Agent,
    child_id: UUID,
    for_update: bool = False,
) -> Agent | None:
    """Load one active, in-scope child through its exact approved edge.

    ``for_update`` protects durable child-run admission against a concurrent
    delegation revocation. Capability planning and effect-time reauthorization
    only need a fresh read; the runner holds the lock until it commits the
    child ``AgentRun``.
    """
    statement = (
        select(Agent)
        .join(AgentDelegation, AgentDelegation.child_agent_id == Agent.id)
        .options(
            selectinload(Agent.tools),
            selectinload(Agent.delegated_agents),
            selectinload(Agent.mcp_connections),
            selectinload(Agent.roles),
        )
        .where(
            Agent.id == child_id,
            Agent.is_active.is_(True),
            AgentDelegation.parent_agent_id == parent.id,
            or_(
                Agent.organization_id.is_(None),
                Agent.organization_id == parent.organization_id,
            ),
        )
        .execution_options(populate_existing=True)
    )
    if for_update:
        statement = statement.with_for_update()
    result = await session.execute(statement)
    return result.scalar_one_or_none()
