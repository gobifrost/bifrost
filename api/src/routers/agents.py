"""
Agents Router

CRUD operations for AI agents.
Role-based access control following the forms pattern.

Agents are virtual entities stored only in the database.
Git sync serializes agents on-the-fly from the database.
"""

import asyncio
import base64
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from src.core.auth import CurrentActiveUser
from src.core.db_deps import DbSession
from src.core.log_safety import log_safe
from src.core.org_filter import OrgFilterType
from shared.scope_resolver import UNSET
from shared.resource_access import ResourceAccessResolver, ResourceKind
from src.models.contracts.agent_stats import AgentStatsResponse, FleetStatsResponse
from src.models.contracts.agents import (
    AgentAccessLevel,
    AgentCreate,
    AgentPromoteRequest,
    AgentPublic,
    AgentSummary,
    AgentUpdate,
    AccessibleTool,
)
from src.models.orm import (
    Agent,
    AgentDelegation,
    AgentMCPConnection,
    AgentRole,
    AgentTool,
    AIModelProfile,
    Role,
    Workflow,
)
from shared.logo_processing import (
    LogoProcessingError,
    is_logo_thumbnail_version,
    process_logo,
)
from src.repositories.agents import AgentRepository
from src.services.agent_write_policy import (
    can_configure_agent,
    can_read_agent,
    can_write_agent,
    caller_user_id,
    enforce_personal_agent_create,
    enforce_personal_agent_update,
    validate_agent_references,
    validate_user_tool_access,
)
from src.services.authorization.enforce import allows_permission, load_caller, user_reach
from src.services.solutions.guard import assert_not_solution_managed
from src.routers.tools import get_system_tool_ids
from src.services.agent_stats import get_agent_stats, get_agent_stats_batch, get_fleet_stats
from src.services.workflow_role_service import sync_agent_roles_to_workflows
from src.services.operation_catalog import operation_route

if TYPE_CHECKING:
    from src.services.authorization.enforce import Caller

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents", tags=["Agents"])


async def _validate_llm_profile_id(
    db: DbSession,
    llm_profile_id: UUID | None,
) -> None:
    """Validate that a referenced model profile exists."""
    if llm_profile_id is None:
        return
    exists = await db.scalar(
        select(AIModelProfile.id).where(AIModelProfile.id == llm_profile_id)
    )
    if exists is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"llm_profile_id '{llm_profile_id}' does not reference an existing model profile",
        )


def _may_promote_agent(caller: "Caller", agent: Agent) -> bool:
    """Whether caller holds agent write permission at this agent's boundary."""
    from src.services.authorization.enforce import permitted_organizations

    return permitted_organizations(caller, "agents.readwrite").covers(agent.organization_id)


async def _visible_agent(
    db: DbSession, user: CurrentActiveUser, agent_id: UUID, *, write: bool = False
) -> Agent:
    """Load an Agent only after the original caller's policy allows it."""
    caller = await load_caller(db, user)
    agent = (
        await db.execute(
            select(Agent)
            .options(
                selectinload(Agent.tools),
                selectinload(Agent.delegated_agents),
                selectinload(Agent.roles),
                selectinload(Agent.owner),
                selectinload(Agent.mcp_connections),
            )
            .where(Agent.id == agent_id)
        )
    ).scalar_one_or_none()
    allowed = can_write_agent(caller, agent) if agent is not None and write else (
        await can_read_agent(db, caller, agent) if agent is not None else False
    )
    if not allowed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent {agent_id} not found")
    assert agent is not None
    return agent


def _logo_data_url(data: bytes | None, content_type: str | None) -> str | None:
    """Encode a binary logo as a data URL, or None if no logo is set."""
    if not data:
        return None
    mime = content_type or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _agent_logo_url(agent: Agent) -> str | None:
    """Return a logo URL without hiding legacy images during thumbnail backfill."""
    if is_logo_thumbnail_version(agent.logo_thumbnail_version):
        return f"/api/agents/{agent.id}/logo?v={agent.logo_thumbnail_version}"
    if agent.logo_content_type:
        return f"/api/agents/{agent.id}/logo"
    return None


def _agent_to_public(agent: Agent) -> AgentPublic:
    """Convert Agent ORM to AgentPublic with relationships."""
    valid_system_tool_ids = set(get_system_tool_ids())

    owner_email = None
    if agent.owner_user_id and hasattr(agent, 'owner') and agent.owner:
        owner_email = agent.owner.email

    return AgentPublic(
        id=agent.id,
        name=agent.name,
        description=agent.description,
        system_prompt=agent.system_prompt,
        channels=agent.channels,
        access_level=agent.access_level,
        organization_id=agent.organization_id,
        is_active=agent.is_active,
        created_by=agent.created_by,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
        owner_user_id=agent.owner_user_id,
        owner_email=owner_email,
        tool_ids=[str(t.id) for t in agent.tools],
        delegated_agent_ids=[str(a.id) for a in agent.delegated_agents],
        role_ids=[str(r.id) for r in agent.roles],
        knowledge_sources=agent.knowledge_sources or [],
        system_tools=[t for t in (agent.system_tools or []) if t in valid_system_tool_ids],
        mcp_connection_ids=sorted(str(c.id) for c in (agent.mcp_connections or [])),
        llm_profile_id=agent.llm_profile_id,
        llm_max_tokens=agent.llm_max_tokens,
        max_iterations=agent.max_iterations,
        max_token_budget=agent.max_token_budget,
        logo=_logo_data_url(
            agent.logo_thumbnail_data or agent.logo_data,
            agent.logo_thumbnail_content_type or agent.logo_content_type,
        ),
        logo_url=_agent_logo_url(agent),
        logo_version=(
            agent.logo_thumbnail_version
            if is_logo_thumbnail_version(agent.logo_thumbnail_version)
            else None
        ),
        is_solution_managed=agent.solution_id is not None,
        solution_id=agent.solution_id,
    )


# =============================================================================
# Agent CRUD Endpoints
# =============================================================================


@router.get("", **operation_route("agents.list"))
async def list_agents(
    db: DbSession,
    user: CurrentActiveUser,
    scope: str | None = Query(
        default=None,
        description="Filter scope: omit for all (superusers), 'global' for global only, "
        "or org UUID for specific org."
    ),
    category: str | None = None,
    active_only: bool = True,
    include_stats: bool = Query(
        False,
        description="Include per-agent run stats in the list response.",
    ),
    discovery_only: bool = Query(
        False,
        description=(
            "Apply ordinary organization and role visibility even for "
            "impersonation-capable callers. Used by chat discovery."
        ),
    ),
) -> list[AgentSummary]:
    """
    List agents the user has access to.

    Organization filtering:
    - Superusers with scope omitted: show all agents
    - Superusers with scope='global': show only global agents
    - Superusers with scope={uuid}: show that org's agents only
    - Org users: always show their org's agents + global agents (scope ignored)

    Access level filtering (applied after org filter):
    - Platform admins see all agents
    - Users see AUTHENTICATED agents + ROLE_BASED agents assigned to their roles
    """
    caller = await load_caller(db, user)
    requested_org: UUID | None = None
    has_explicit_scope = scope is not None
    if scope is not None:
        if scope == "global":
            requested_org = None
        else:
            try:
                requested_org = UUID(scope)
            except ValueError as exc:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="scope must be 'global' or an organization UUID") from exc
        if not user_reach(caller).covers(requested_org):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You don't have permission to view agents")

    # The repository is a raw loader here.  Visibility below uses canonical
    # permission reach plus each Agent's object ACL, not repository bypasses.
    repo = AgentRepository(
        session=db,
        org_id=None,
        user_id=None,
        is_superuser=False,
        is_external=user.is_external,
    )
    candidates = await repo.list_all_in_scope(OrgFilterType.ALL, active_only=active_only)
    scoped_candidates: list[Agent] = []
    for agent in candidates:
        if has_explicit_scope and agent.organization_id != requested_org:
            continue
        if discovery_only and agent.organization_id not in (None, caller.ctx.home_organization_id if caller.ctx else None):
            continue
        scoped_candidates.append(agent)
    agents = await ResourceAccessResolver(db, caller).filter_readable(
        ResourceKind.AGENT,
        scoped_candidates,
        selected_organization_id=requested_org if has_explicit_scope else UNSET,
    )

    # Batch-compute dependency counts (tool count per agent)
    agent_ids = [a.id for a in agents]
    dep_counts: dict[UUID, int] = {}
    mcp_counts: dict[UUID, int] = {}
    if agent_ids:
        from sqlalchemy import func
        count_result = await db.execute(
            select(AgentTool.agent_id, func.count())
            .where(AgentTool.agent_id.in_(agent_ids))
            .group_by(AgentTool.agent_id)
        )
        dep_counts = {row[0]: row[1] for row in count_result.all()}

        mcp_count_result = await db.execute(
            select(AgentMCPConnection.agent_id, func.count())
            .where(AgentMCPConnection.agent_id.in_(agent_ids))
            .group_by(AgentMCPConnection.agent_id)
        )
        mcp_counts = {row[0]: row[1] for row in mcp_count_result.all()}

    stats_by_agent = (
        await get_agent_stats_batch(
            agent_ids,
            db,
            caller,
        )
        if include_stats and agent_ids
        else {}
    )

    result = []
    for a in agents:
        summary = AgentSummary.model_validate(a)
        summary.dependency_count = dep_counts.get(a.id, 0)
        summary.mcp_connection_count = mcp_counts.get(a.id, 0)
        summary.logo = None
        summary.logo_url = _agent_logo_url(a)
        summary.logo_version = (
            a.logo_thumbnail_version
            if is_logo_thumbnail_version(a.logo_thumbnail_version)
            else None
        )
        summary.stats = stats_by_agent.get(a.id)
        result.append(summary)

    return result


@router.post("", status_code=status.HTTP_201_CREATED, **operation_route("agents.create"))
async def create_agent(
    agent_data: AgentCreate,
    db: DbSession,
    user: CurrentActiveUser,
) -> AgentPublic:
    """
    Create a new agent.

    Platform admins can create any agent type.
    Regular users can only create private agents with tools they have access to.
    """
    caller = await load_caller(db, user)
    original_user_id = caller_user_id(caller)
    if original_user_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You don't have permission to manage agents")

    # Org targeting follows the unified --org standard: an OMITTED
    # organization_id (HOME) defaults to the caller's org, so a bare create
    # never silently writes a global row. Explicit null still means global.
    # (Non-admins are forced to their own org below regardless.)
    if "organization_id" not in agent_data.model_fields_set:
        agent_data.organization_id = caller.ctx.home_organization_id if caller.ctx else None

    can_manage_target = (
        user_reach(caller).covers(agent_data.organization_id)
        and allows_permission(caller, "agents.readwrite", agent_data.organization_id)
    )

    agent_id = uuid4()
    if not can_manage_target:
        # A privileged/budget field is applied or refused, never silently
        # dropped/overridden — see agent_write_policy for the full gate
        # order (access_level, privileged fields, budget fields, org).
        enforce_personal_agent_create(
            agent_data,
            caller_org_id=caller.ctx.home_organization_id if caller.ctx else None,
        )
        agent_data.organization_id = caller.ctx.home_organization_id if caller.ctx else None
    await validate_user_tool_access(db, caller, agent_data.tool_ids)

    # Validate references before creating the agent (every caller, admins
    # included).
    await validate_agent_references(
        db,
        tool_ids=agent_data.tool_ids,
        delegated_agent_ids=agent_data.delegated_agent_ids,
        role_ids=agent_data.role_ids,
        mcp_connection_ids=[str(c) for c in agent_data.mcp_connection_ids],
        organization_id=agent_data.organization_id,
        agent_id=None,
    )
    await _validate_llm_profile_id(db, agent_data.llm_profile_id)

    now = datetime.now(timezone.utc)

    # Set owner for private agents
    owner_user_id = None
    if agent_data.access_level == AgentAccessLevel.PRIVATE:
        owner_user_id = original_user_id

    # Create the agent
    agent = Agent(
        id=agent_id,
        name=agent_data.name,
        description=agent_data.description,
        system_prompt=agent_data.system_prompt,
        channels=[c.value for c in agent_data.channels],
        access_level=agent_data.access_level,
        organization_id=agent_data.organization_id,
        owner_user_id=owner_user_id,
        is_active=True,
        knowledge_sources=agent_data.knowledge_sources or [],
        system_tools=agent_data.system_tools or [],
        llm_profile_id=agent_data.llm_profile_id,
        llm_max_tokens=agent_data.llm_max_tokens,
        max_iterations=agent_data.max_iterations,
        max_token_budget=agent_data.max_token_budget,
        created_by=user.email,
        created_at=now,
        updated_at=now,
    )
    db.add(agent)

    # Add tool relationships. validate_agent_references above already
    # guarantees each ID exists, is active, and is type='tool'.
    if agent_data.tool_ids:
        for tool_id in agent_data.tool_ids:
            db.add(AgentTool(agent_id=agent_id, workflow_id=UUID(tool_id)))

    # Add delegation relationships. Reference validation guarantees each
    # delegate exists, is active, and isn't self-delegation.
    if agent_data.delegated_agent_ids:
        for delegate_id in agent_data.delegated_agent_ids:
            db.add(AgentDelegation(
                parent_agent_id=agent_id,
                child_agent_id=UUID(delegate_id),
            ))

    # Add role relationships. Reference validation guarantees each role
    # exists.
    if agent_data.role_ids:
        for role_id in agent_data.role_ids:
            db.add(AgentRole(
                agent_id=agent_id,
                role_id=UUID(role_id),
                assigned_by=user.email,
            ))

    await db.flush()

    # Reference validation above checks the request shape. The repository
    # independently authorizes the parent Agent and verifies each persisted
    # connection target before replacing association rows.
    if agent_data.mcp_connection_ids:
        await AgentRepository(
            session=db,
            org_id=agent.organization_id,
        ).set_mcp_connection_grants(
            agent_id,
            agent_data.mcp_connection_ids,
            caller=caller,
            granted_by=user.user_id,
        )

    # Reload with relationships
    result = await db.execute(
        select(Agent)
        .options(
            selectinload(Agent.tools),
            selectinload(Agent.delegated_agents),
            selectinload(Agent.roles),
            selectinload(Agent.owner),
            selectinload(Agent.mcp_connections),
        )
        .where(Agent.id == agent_id)
    )
    agent = result.scalar_one()

    # Sync agent roles to referenced workflows (tools) - additive
    await sync_agent_roles_to_workflows(db, agent, assigned_by=user.email)

    return _agent_to_public(agent)


@router.get("/accessible-tools")
async def get_accessible_tools(
    db: DbSession,
    user: CurrentActiveUser,
) -> list[AccessibleTool]:
    """Get tools the current user can assign to their agents (via role intersection).

    Role membership alone isn't org scope: also require the tool workflow
    to be in the caller's own org or global, so a role grant on another
    org's tool-type workflow never surfaces here.
    """
    from src.models.orm.users import UserRole
    from src.models.orm.workflow_roles import WorkflowRole

    caller = await load_caller(db, user)
    original_user_id = caller_user_id(caller)
    if original_user_id is None:
        return []
    result = await db.execute(
        select(UserRole.role_id).where(UserRole.user_id == original_user_id)
    )
    role_ids = list(result.scalars().all())

    if not role_ids:
        return []

    stmt = (
        select(Workflow)
        .join(WorkflowRole, WorkflowRole.workflow_id == Workflow.id)
        .where(Workflow.type == "tool")
        .where(Workflow.is_active.is_(True))
        .where(WorkflowRole.role_id.in_(role_ids))
        .distinct()
    )
    reach_clause = user_reach(caller).where(Workflow.organization_id)
    if reach_clause is not None:
        stmt = stmt.where(reach_clause)
    result = await db.execute(stmt)
    tools = result.scalars().all()

    return [
        AccessibleTool(id=str(t.id), name=t.name, description=t.tool_description or t.description)
        for t in tools
    ]


@router.get("/stats/fleet", response_model=FleetStatsResponse)
async def get_fleet_stats_endpoint(
    db: DbSession,
    user: CurrentActiveUser,
    window_days: int = Query(7, ge=1, le=90),
) -> FleetStatsResponse:
    """Fleet-wide agent run stats over the last ``window_days``.

    Bypass callers (platform admin / provider org) see cross-org totals;
    regular users are scoped to their own org, their own runs/chats, and
    never see another user's private agent folded into the count. A
    regular caller with no org never falls through to the platform-wide
    view — they get an empty fleet instead.
    Route is registered before ``/{agent_id}`` so the literal ``stats``
    prefix is not parsed as a UUID.
    """
    caller = await load_caller(db, user)
    return await get_fleet_stats(
        db,
        caller,
        window_days=window_days,
    )


@router.get("/{agent_id}", **operation_route("agents.get"))
async def get_agent(
    agent_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
) -> AgentPublic:
    """Get agent by ID."""
    agent = await _visible_agent(db, user, agent_id)
    return _agent_to_public(agent)


@router.put("/{agent_id}", **operation_route("agents.update"))
async def update_agent(
    agent_id: UUID,
    agent_data: AgentUpdate,
    db: DbSession,
    user: CurrentActiveUser,
) -> AgentPublic:
    """Update an agent. Admins can update any agent. Users can update their own private agents."""
    result = await db.execute(
        select(Agent)
        .options(
            selectinload(Agent.tools),
            selectinload(Agent.delegated_agents),
            selectinload(Agent.roles),
            selectinload(Agent.owner),
            selectinload(Agent.mcp_connections),
        )
        .where(Agent.id == agent_id)
    )
    agent = result.scalar_one_or_none()

    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {agent_id} not found",
        )

    caller = await load_caller(db, user)
    if agent is None or not can_write_agent(caller, agent):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent {agent_id} not found")

    # Solution-managed agents are read-only here; deploy is the writer.
    assert_not_solution_managed(agent)

    can_manage_configuration = can_configure_agent(caller, agent)

    if not can_manage_configuration:
        if agent.owner_user_id != caller_user_id(caller) or agent.access_level != AgentAccessLevel.PRIVATE:
            raise HTTPException(403, "You can only edit your own private agents")
        if agent_data.access_level is not None and agent_data.access_level != AgentAccessLevel.PRIVATE:
            raise HTTPException(403, "Use the promote endpoint to change access level")
        # A privileged field is applied or refused, never silently dropped —
        # see agent_write_policy for the full gate (includes clear_roles and
        # the organization_id rescope gate below).
        enforce_personal_agent_update(agent, agent_data)

    if agent_data.tool_ids is not None:
        await validate_user_tool_access(db, caller, agent_data.tool_ids)

    final_access_level = agent_data.access_level or agent.access_level

    # Effective organization after this mutation: the new value when
    # provided, else the Agent's current org.
    target_organization_id = (
        agent_data.organization_id
        if "organization_id" in agent_data.model_fields_set
        else agent.organization_id
    )
    if (
        target_organization_id != agent.organization_id
        and not (
            user_reach(caller).covers(target_organization_id)
            and allows_permission(caller, "agents.readwrite", target_organization_id)
        )
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You don't have permission to manage agents")
    if (
        "organization_id" in agent_data.model_fields_set
        and target_organization_id != agent.organization_id
        and agent.mcp_connections
        and agent_data.mcp_connection_ids is None
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Rescoping an Agent with MCP connections requires an explicit "
                "mcp_connection_ids list; pass [] to revoke all grants"
            ),
        )

    # Validate references being updated (every caller, admins included).
    await validate_agent_references(
        db,
        tool_ids=agent_data.tool_ids,
        delegated_agent_ids=agent_data.delegated_agent_ids,
        role_ids=agent_data.role_ids,
        mcp_connection_ids=(
            [str(c) for c in agent_data.mcp_connection_ids]
            if agent_data.mcp_connection_ids is not None
            else None
        ),
        organization_id=target_organization_id,
        agent_id=agent_id,  # For self-delegation check
        clear_roles=agent_data.clear_roles,
    )
    if "llm_profile_id" in agent_data.model_fields_set:
        await _validate_llm_profile_id(db, agent_data.llm_profile_id)

    was_private = agent.access_level == AgentAccessLevel.PRIVATE

    # Update fields
    if agent_data.name is not None:
        agent.name = agent_data.name
    if agent_data.description is not None:
        agent.description = agent_data.description
    if agent_data.system_prompt is not None:
        agent.system_prompt = agent_data.system_prompt
    if agent_data.channels is not None:
        agent.channels = [c.value for c in agent_data.channels]
    if agent_data.access_level is not None:
        agent.access_level = agent_data.access_level
        if agent_data.access_level == AgentAccessLevel.PRIVATE:
            if not was_private or agent.owner_user_id is None:
                agent.owner_user_id = user.user_id
        elif can_manage_configuration:
            agent.owner_user_id = None
    # Use model_fields_set to distinguish "not provided" from "explicitly null"
    if "organization_id" in agent_data.model_fields_set:
        agent.organization_id = agent_data.organization_id
    if agent_data.is_active is not None:
        agent.is_active = agent_data.is_active
    if agent_data.knowledge_sources is not None:
        agent.knowledge_sources = agent_data.knowledge_sources
    if agent_data.system_tools is not None:
        agent.system_tools = agent_data.system_tools
    if "llm_profile_id" in agent_data.model_fields_set:
        agent.llm_profile_id = agent_data.llm_profile_id
    if agent_data.llm_max_tokens is not None:
        agent.llm_max_tokens = agent_data.llm_max_tokens if agent_data.llm_max_tokens else None
    if "max_iterations" in agent_data.model_fields_set:
        agent.max_iterations = agent_data.max_iterations
    if "max_token_budget" in agent_data.model_fields_set:
        agent.max_token_budget = agent_data.max_token_budget

    agent.updated_at = datetime.now(timezone.utc)

    # Update tool relationships if provided. validate_agent_references above
    # already guarantees each ID exists, is active, and is type='tool'.
    if agent_data.tool_ids is not None:
        await db.execute(
            delete(AgentTool).where(AgentTool.agent_id == agent_id)
        )
        for tool_id in agent_data.tool_ids:
            db.add(AgentTool(agent_id=agent_id, workflow_id=UUID(tool_id)))

    # Update delegation relationships if provided. Reference validation
    # guarantees each delegate exists, is active, and isn't self-delegation.
    if agent_data.delegated_agent_ids is not None:
        await db.execute(
            delete(AgentDelegation).where(AgentDelegation.parent_agent_id == agent_id)
        )
        for delegate_id in agent_data.delegated_agent_ids:
            db.add(AgentDelegation(
                parent_agent_id=agent_id,
                child_agent_id=UUID(delegate_id),
            ))

    # Private agents are owner-only; do not retain stale role grants when
    # changing visibility via the generic update endpoint.
    roles_changed = False
    if final_access_level == AgentAccessLevel.PRIVATE:
        await db.execute(
            delete(AgentRole).where(AgentRole.agent_id == agent_id)
        )
        roles_changed = True

    # Clear all role assignments if requested
    elif agent_data.clear_roles:
        await db.execute(
            delete(AgentRole).where(AgentRole.agent_id == agent_id)
        )
        roles_changed = True
        # Also set to role_based access level (effectively no access)
        agent.access_level = AgentAccessLevel.ROLE_BASED
        logger.info(f"Cleared all role assignments for agent '{log_safe(agent.name)}'")

    # Update role relationships if provided (and not clearing). Reference
    # validation above guarantees each role exists.
    elif agent_data.role_ids is not None:
        await db.execute(
            delete(AgentRole).where(AgentRole.agent_id == agent_id)
        )
        roles_changed = True
        for role_id in agent_data.role_ids:
            db.add(AgentRole(
                agent_id=agent_id,
                role_id=UUID(role_id),
                assigned_by=user.email,
            ))

    if roles_changed:
        db.expire(agent, ["roles"])

    # Sync MCP connection grants if provided. ``mcp_connection_ids=None``
    # means "leave grants alone"; an empty list explicitly revokes all.
    if agent_data.mcp_connection_ids is not None:
        repo = AgentRepository(
            session=db,
            org_id=agent.organization_id,
        )
        await repo.set_mcp_connection_grants(
            agent_id,
            agent_data.mcp_connection_ids,
            caller=caller,
            granted_by=user.user_id,
        )

    await db.flush()

    # Reload with relationships
    result = await db.execute(
        select(Agent)
        .options(
            selectinload(Agent.tools),
            selectinload(Agent.delegated_agents),
            selectinload(Agent.roles),
            selectinload(Agent.owner),
            selectinload(Agent.mcp_connections),
        )
        .where(Agent.id == agent_id)
    )
    agent = result.scalar_one()

    # Sync agent roles to referenced workflows (tools) - additive
    await sync_agent_roles_to_workflows(db, agent, assigned_by=user.email)

    return _agent_to_public(agent)


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT, **operation_route("agents.delete"))
async def delete_agent(
    agent_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
) -> None:
    """Permanently delete an agent. Admins can delete any agent. Users can delete their own private agents.

    System agents can be deleted - they will be recreated on next startup
    if they are still defined in the system agent definitions.
    """
    agent = await _visible_agent(db, user, agent_id, write=True)

    # Solution-managed agents are read-only here; deploy is the writer.
    assert_not_solution_managed(agent)

    # Use a SQL DELETE so database-level cascades remove run history and agent
    # memberships while SET NULL references (such as conversations) are preserved.
    await db.execute(delete(Agent).where(Agent.id == agent_id))
    await db.flush()


@router.post("/{agent_id}/promote")
async def promote_agent(
    agent_id: UUID,
    request: AgentPromoteRequest,
    db: DbSession,
    user: CurrentActiveUser,
) -> AgentPublic:
    """Promote a private agent to organization scope."""
    result = await db.execute(
        select(Agent)
        .options(
            selectinload(Agent.tools),
            selectinload(Agent.delegated_agents),
            selectinload(Agent.roles),
            selectinload(Agent.owner),
        )
        .where(Agent.id == agent_id)
    )
    agent = result.scalar_one_or_none()
    caller = await load_caller(db, user)
    if agent is None or not can_write_agent(caller, agent):
        raise HTTPException(404, f"Agent {agent_id} not found")
    assert_not_solution_managed(agent)

    if agent.access_level != AgentAccessLevel.PRIVATE:
        raise HTTPException(400, "Agent is not private — nothing to promote")

    if not _may_promote_agent(caller, agent):
        raise HTTPException(403, "You do not have permission to promote agents")

    # Promote: change access_level, clear owner
    agent.access_level = request.access_level
    agent.owner_user_id = None
    agent.updated_at = datetime.now(timezone.utc)

    # Set roles if role_based
    if request.access_level == AgentAccessLevel.ROLE_BASED and request.role_ids:
        await db.execute(delete(AgentRole).where(AgentRole.agent_id == agent_id))
        for role_id in request.role_ids:
            try:
                role_uuid = UUID(role_id)
                result = await db.execute(
                    select(Role).where(Role.id == role_uuid)
                )
                role = result.scalar_one_or_none()
                if role:
                    db.add(AgentRole(agent_id=agent_id, role_id=role.id, assigned_by=user.email))
            except ValueError as e:
                # Non-UUID role_id (e.g. role name) — skip, only UUIDs supported here
                logger.debug(f"role_id {log_safe(role_id)!r} is not a UUID, skipping: {log_safe(e)}")

    await db.flush()

    # Reload
    result = await db.execute(
        select(Agent)
        .options(
            selectinload(Agent.tools),
            selectinload(Agent.delegated_agents),
            selectinload(Agent.roles),
            selectinload(Agent.owner),
        )
        .where(Agent.id == agent_id)
    )
    agent = result.scalar_one()
    return _agent_to_public(agent)


# =============================================================================
# Tool Assignment Endpoints
# =============================================================================


@router.get("/{agent_id}/stats", response_model=AgentStatsResponse)
async def get_agent_stats_endpoint(
    agent_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
    window_days: int = Query(7, ge=1, le=90),
) -> AgentStatsResponse:
    """Per-agent run stats. Reuses the same access check as ``GET /{agent_id}``."""
    await _visible_agent(db, user, agent_id)
    caller = await load_caller(db, user)

    return await get_agent_stats(
        agent_id,
        db,
        caller,
        window_days=window_days,
    )


@router.get("/{agent_id}/tools")
async def get_agent_tools(
    agent_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
) -> list[dict]:
    """Get tools assigned to an agent."""
    agent = await _visible_agent(db, user, agent_id)

    return [
        {
            "id": str(t.id),
            "name": t.name,
            "description": t.tool_description or t.description,
            "category": t.category,
        }
        for t in agent.tools
    ]


# =============================================================================
# Delegation Assignment Endpoints
# =============================================================================


@router.get("/{agent_id}/delegations")
async def get_agent_delegations(
    agent_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
) -> list[AgentSummary]:
    """Get agents this agent can delegate to."""
    caller = await load_caller(db, user)
    agent = await _visible_agent(db, user, agent_id)

    # The parent agent being accessible doesn't make every delegate
    # accessible — a delegate can be private, role_based, or another org's.
    # Re-check each one individually rather than trusting the raw
    # relationship (which carries no access filter of its own).
    summaries = []
    for a in agent.delegated_agents:
        if not await can_read_agent(db, caller, a):
            continue
        s = AgentSummary.model_validate(a)
        s.logo = None
        s.logo_url = _agent_logo_url(a)
        s.logo_version = (
            a.logo_thumbnail_version
            if is_logo_thumbnail_version(a.logo_thumbnail_version)
            else None
        )
        summaries.append(s)
    return summaries


@router.post("/{agent_id}/logo")
async def upload_agent_logo(
    agent_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
    file: UploadFile = File(..., description="Logo image (PNG/JPEG/SVG, ≤5MB)"),
) -> dict:
    """Upload a square logo for an agent."""
    agent = await _visible_agent(db, user, agent_id, write=True)
    assert_not_solution_managed(agent)

    content = await file.read()
    try:
        processed = await asyncio.to_thread(process_logo, content, file.content_type or "")
    except LogoProcessingError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    agent.logo_data = processed.original_data
    agent.logo_content_type = processed.original_content_type
    agent.logo_thumbnail_data = processed.thumbnail_data
    agent.logo_thumbnail_content_type = processed.thumbnail_content_type
    agent.logo_thumbnail_version = processed.thumbnail_version
    await db.commit()
    return {"ok": True}


@router.get(
    "/{agent_id}/logo",
    responses={
        200: {
            "content": {
                "image/webp": {},
                "image/png": {},
                "image/jpeg": {},
                "image/svg+xml": {},
            }
        },
        404: {"description": "No logo set"},
    },
)
async def get_agent_logo(
    agent_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
) -> Response:
    agent = await _visible_agent(db, user, agent_id)
    if not agent or not agent.logo_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Logo not set",
        )
    thumbnail_ready = bool(agent.logo_thumbnail_data and agent.logo_thumbnail_version)
    headers = (
        {
            "Cache-Control": "private, max-age=31536000, immutable",
            "ETag": f'"{agent.logo_thumbnail_version}"',
        }
        if thumbnail_ready
        else {"Cache-Control": "no-store"}
    )
    return Response(
        content=agent.logo_thumbnail_data or agent.logo_data,
        media_type=(
            agent.logo_thumbnail_content_type
            or agent.logo_content_type
            or "application/octet-stream"
        ),
        headers=headers,
    )


@router.delete("/{agent_id}/logo", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent_logo(
    agent_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
) -> Response:
    agent = await _visible_agent(db, user, agent_id, write=True)
    assert_not_solution_managed(agent)
    agent.logo_data = None
    agent.logo_content_type = None
    agent.logo_thumbnail_data = None
    agent.logo_thumbnail_content_type = None
    agent.logo_thumbnail_version = None
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
