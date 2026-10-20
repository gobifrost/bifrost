"""Current-authority admission for running an Agent.

An Agent is both a resource and an executable capability.  Every entry point
therefore reloads the caller and the active Agent before spending model credit
or dispatching a native tool.  The original user in ``Caller.ctx`` decides
reach and permissions; the principal remains the effect actor.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shared.scope_resolver import UNSET, RequestedScope, shared_read_organization
from src.models.orm.agents import Agent
from src.services.agent_write_policy import can_read_agent
from src.services.authorization.callers import CallerUnavailable, refresh_caller
from src.services.authorization.enforce import (
    Caller,
    PermissionDenied,
    require_named_permission,
    user_reach,
)


class AgentRuntimeAuthorizationError(CallerUnavailable):
    """The original caller may no longer execute the requested Agent."""


def selected_organization(caller: Caller) -> UUID | None:
    """The original execution scope, never the effect actor's home org."""
    if caller.ctx is None:
        raise AgentRuntimeAuthorizationError("Agent caller is unavailable")
    runtime = caller.principal.runtime_authority
    return runtime.organization_id if runtime is not None else caller.ctx.home_organization_id


async def authorize_agent_execution(
    db: AsyncSession,
    caller: Caller,
    agent_id: UUID,
    *,
    selected_organization_id: RequestedScope = UNSET,
) -> tuple[Caller, Agent]:
    """Refresh and authorize an active Agent at its actual resource boundary."""
    refreshed = await refresh_caller(db, caller)
    if refreshed.ctx is None:
        raise AgentRuntimeAuthorizationError("Agent caller is unavailable")
    agent = (
        await db.execute(
            select(Agent)
            .options(
                selectinload(Agent.tools),
                selectinload(Agent.delegated_agents),
                selectinload(Agent.mcp_connections),
                selectinload(Agent.roles),
            )
            .execution_options(populate_existing=True)
            .where(Agent.id == agent_id, Agent.is_active.is_(True))
        )
    ).scalar_one_or_none()
    if agent is None:
        raise AgentRuntimeAuthorizationError("Agent is no longer available")

    selected = (
        selected_organization(refreshed)
        if selected_organization_id is UNSET
        else selected_organization_id
    )
    if not user_reach(refreshed).covers(agent.organization_id):
        raise AgentRuntimeAuthorizationError("Agent is outside the caller's organization reach")
    if not await can_read_agent(
        db, refreshed, agent, selected_organization_id=selected
    ):
        raise AgentRuntimeAuthorizationError("Agent is no longer available")
    try:
        require_named_permission(
            refreshed,
            "agents.execute",
            shared_read_organization(
                refreshed, agent.organization_id, selected_organization_id=selected
            ),
        )
    except PermissionDenied as exc:
        raise AgentRuntimeAuthorizationError(str(exc)) from exc
    return refreshed, agent


async def authorize_agentless_ai(
    db: AsyncSession,
    caller: Caller,
) -> Caller:
    """Refresh an agentless chat caller before it can spend AI credit."""
    refreshed = await refresh_caller(db, caller)
    if refreshed.ctx is None:
        raise AgentRuntimeAuthorizationError("Chat caller is unavailable")
    try:
        require_named_permission(refreshed, "ai.execute", selected_organization(refreshed))
    except PermissionDenied as exc:
        raise AgentRuntimeAuthorizationError(str(exc)) from exc
    return refreshed
