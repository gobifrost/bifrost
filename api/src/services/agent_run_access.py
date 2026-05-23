"""Authorization helpers for agent-run HTTP and WebSocket surfaces."""
from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import Select, and_, exists, false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth import UserPrincipal
from src.models.enums import AgentAccessLevel
from src.models.orm.agent_runs import AgentRun
from src.models.orm.agents import Agent, AgentRole
from src.models.orm.users import UserRole


def _agent_role_exists_for_user(user: UserPrincipal):
    user_role_ids = select(UserRole.role_id).where(UserRole.user_id == user.user_id)
    return (
        exists()
        .where(AgentRole.agent_id == Agent.id)
        .where(AgentRole.role_id.in_(user_role_ids))
    )


def agent_access_conditions(user: UserPrincipal) -> list:
    """Return SQLAlchemy conditions matching the normal agent access model."""
    if user.is_superuser:
        return []

    if user.organization_id is None:
        return [false()]

    private_owner = and_(
        Agent.access_level == AgentAccessLevel.PRIVATE,
        Agent.owner_user_id == user.user_id,
    )
    in_scope = or_(
        Agent.organization_id == user.organization_id,
        Agent.organization_id.is_(None),
        private_owner,
    )
    has_access = or_(
        Agent.access_level == AgentAccessLevel.AUTHENTICATED,
        private_owner,
        and_(
            Agent.access_level == AgentAccessLevel.ROLE_BASED,
            _agent_role_exists_for_user(user),
        ),
    )
    return [in_scope, has_access]


def apply_agent_run_access(
    query: Select[tuple[AgentRun]],
    user: UserPrincipal,
) -> Select[tuple[AgentRun]]:
    """Apply tenant and agent visibility checks to an AgentRun query."""
    query = query.join(Agent, AgentRun.agent_id == Agent.id)
    if user.is_superuser:
        return query

    if user.organization_id is None:
        return query.where(false())

    return query.where(
        AgentRun.org_id == user.organization_id,
        *agent_access_conditions(user),
    )


async def load_agent_for_user(
    db: AsyncSession,
    agent_id: UUID,
    user: UserPrincipal,
) -> Agent | None:
    query = select(Agent).where(Agent.id == agent_id)
    query = query.where(*agent_access_conditions(user))
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def load_agent_by_name_for_user(
    db: AsyncSession,
    agent_name: str,
    user: UserPrincipal,
) -> Agent | None:
    query = select(Agent).where(Agent.name.ilike(agent_name))
    query = query.where(*agent_access_conditions(user))
    if user.organization_id is not None:
        query = query.order_by((Agent.organization_id == user.organization_id).desc())
    result = await db.execute(query)
    return result.scalars().first()


async def load_agent_run_for_user(
    db: AsyncSession,
    run_id: UUID,
    user: UserPrincipal,
    *,
    options: Sequence | None = None,
) -> AgentRun | None:
    query = select(AgentRun).where(AgentRun.id == run_id)
    if options:
        query = query.options(*options)
    query = apply_agent_run_access(query, user)
    result = await db.execute(query)
    return result.scalar_one_or_none()
