"""Current-authority admission for running an Agent.

An Agent is both a resource and an executable capability.  Every entry point
therefore reloads the caller and the active Agent before spending model credit
or dispatching a native tool.  The original user in ``Caller.ctx`` decides
reach and permissions; the principal remains the effect actor.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shared.scope_resolver import UNSET, RequestedScope, shared_read_organization
from src.models.orm.agent_runs import AgentRun
from src.models.orm.agents import Agent
from src.services.agent_write_policy import can_read_agent
from src.services.authorization.callers import CallerUnavailable, refresh_caller
from src.services.authorization.enforce import (
    Caller,
    PermissionDenied,
    load_caller,
    require_named_permission,
    user_reach,
)
from src.services.authorization.runtime_auth import load_runtime_principal


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
            _active_agent_statement(agent_id)
            .options(
                selectinload(Agent.tools),
                selectinload(Agent.delegated_agents),
                selectinload(Agent.mcp_connections),
            )
        )
    ).scalar_one_or_none()
    return refreshed, await _authorize_current_agent(
        db, refreshed, agent, selected_organization_id=selected_organization_id
    )


def _active_agent_statement(agent_id: UUID) -> Select[tuple[Agent]]:
    return (
        select(Agent)
        .options(selectinload(Agent.roles))
        .execution_options(populate_existing=True)
        .where(Agent.id == agent_id, Agent.is_active.is_(True))
    )


async def _authorize_current_agent(
    db: AsyncSession,
    caller: Caller,
    agent: Agent | None,
    *,
    selected_organization_id: RequestedScope = UNSET,
) -> Agent:
    """Apply the same access checks immediately after a fresh authority load."""
    if agent is None:
        raise AgentRuntimeAuthorizationError("Agent is no longer available")

    selected = (
        selected_organization(caller)
        if selected_organization_id is UNSET
        else selected_organization_id
    )
    if not user_reach(caller).covers(agent.organization_id):
        raise AgentRuntimeAuthorizationError("Agent is outside the caller's organization reach")
    if not await can_read_agent(
        db, caller, agent, selected_organization_id=selected
    ):
        raise AgentRuntimeAuthorizationError("Agent is no longer available")
    try:
        require_named_permission(
            caller,
            "agents.execute",
            shared_read_organization(
                caller, agent.organization_id, selected_organization_id=selected
            ),
        )
    except PermissionDenied as exc:
        raise AgentRuntimeAuthorizationError(str(exc)) from exc
    return agent


async def authorize_agentless_ai(
    db: AsyncSession,
    caller: Caller,
) -> Caller:
    """Refresh an agentless chat caller before it can spend AI credit."""
    refreshed = await refresh_caller(db, caller)
    _require_agentless_ai(refreshed)
    return refreshed


def _require_agentless_ai(caller: Caller) -> None:
    if caller.ctx is None:
        raise AgentRuntimeAuthorizationError("Chat caller is unavailable")
    try:
        require_named_permission(caller, "ai.execute", selected_organization(caller))
    except PermissionDenied as exc:
        raise AgentRuntimeAuthorizationError(str(exc)) from exc


async def authorize_chat_execution(db: AsyncSession, caller: Caller) -> Caller:
    """Recheck Chat access when a queued conversation turn starts executing."""
    refreshed = await refresh_caller(db, caller)
    _require_chat(refreshed)
    return refreshed


def _require_chat(caller: Caller) -> None:
    if caller.ctx is None:
        raise AgentRuntimeAuthorizationError("Chat caller is unavailable")
    try:
        require_named_permission(
            caller, "chat.execute", caller.ctx.home_organization_id
        )
    except PermissionDenied as exc:
        raise AgentRuntimeAuthorizationError(str(exc)) from exc


async def authorize_agent_run_continuation(
    db: AsyncSession, run_id: UUID,
) -> Caller | None:
    """Reload current authority once for this effect or send boundary.

    This does not construct a runtime definition. It keeps current Agent
    visibility, roles and execution permissions without hydrating its entire
    tool/delegation/MCP graph for each status read or streamed reply frame.
    No authority survives into the next boundary.
    """
    run = await db.get(AgentRun, run_id, populate_existing=True)
    if run is None or run.status != "running":
        return None
    principal = await load_runtime_principal(
        db, {"credential_kind": "agent_run", "sub": str(run_id)}
    )
    if principal is None:
        return None
    caller = await load_caller(db, principal)
    if caller.ctx is None:
        return None
    if run.trigger_type == "chat":
        _require_chat(caller)
    if run.agent_id is None:
        _require_agentless_ai(caller)
    else:
        agent = (await db.execute(_active_agent_statement(run.agent_id))).scalar_one_or_none()
        await _authorize_current_agent(db, caller, agent)
    return caller
