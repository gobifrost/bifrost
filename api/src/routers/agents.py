"""
Agents Router

CRUD operations for AI agents.
Role-based access control following the forms pattern.

Agents are virtual entities stored only in the database.
Git sync serializes agents on-the-fly from the database.
"""

import base64
import logging
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from src.core.auth import CurrentActiveUser
from src.core.db_deps import DbSession
from src.core.log_safety import log_safe
from src.core.org_filter import resolve_org_filter
from src.models.contracts.agent_stats import AgentStatsResponse, FleetStatsResponse
from src.models.contracts.agents import (
    AgentAccessLevel,
    AgentCreate,
    AgentPromoteRequest,
    AgentPublic,
    AgentSummary,
    AgentUpdate,
    AccessibleKnowledgeSource,
    AccessibleTool,
)
from src.models.orm import (
    Agent,
    AgentDelegation,
    AgentMCPConnection,
    AgentRole,
    AgentTool,
    MCPConnection,
    Role,
    Workflow,
)
from shared.svg_sanitizer import SvgSanitizationError, sanitize_svg
from src.repositories.agents import AgentRepository
from src.services.solutions.guard import assert_not_solution_managed
from src.routers.tools import get_system_tool_ids
from src.services.agent_stats import get_agent_stats, get_fleet_stats
from src.services.workflow_role_service import sync_agent_roles_to_workflows

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents", tags=["Agents"])

LOGO_ALLOWED_CONTENT_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/svg+xml"}
LOGO_MAX_SIZE = 5 * 1024 * 1024  # 5 MB


async def _validate_agent_references(
    db: DbSession,
    tool_ids: list[str] | None,
    delegated_agent_ids: list[str] | None,
    agent_id: UUID | None = None,  # For self-delegation check
) -> None:
    """
    Validate that all referenced tools and agents exist and are valid.

    Args:
        db: Database session
        tool_ids: List of tool IDs to validate (must be type='tool')
        delegated_agent_ids: List of agent IDs to delegate to
        agent_id: The agent being created/updated (for self-delegation check)

    Raises:
        HTTPException: 422 if any reference is invalid
    """
    errors: list[str] = []

    # Validate tool_ids
    if tool_ids:
        for tool_id in tool_ids:
            try:
                workflow_uuid = UUID(tool_id)
                result = await db.execute(
                    select(Workflow).where(Workflow.id == workflow_uuid)
                )
                workflow = result.scalar_one_or_none()
                if workflow is None:
                    errors.append(f"tool_id '{tool_id}' does not reference an existing workflow")
                elif not workflow.is_active:
                    errors.append(f"tool_id '{tool_id}' references an inactive workflow")
                elif workflow.type != "tool":
                    errors.append(
                        f"tool_id '{tool_id}' references a {workflow.type}, not a tool"
                    )
            except ValueError:
                errors.append(f"tool_id '{tool_id}' is not a valid UUID")

    # Validate delegated_agent_ids
    if delegated_agent_ids:
        for delegate_id in delegated_agent_ids:
            try:
                delegate_uuid = UUID(delegate_id)

                # Check for self-delegation
                if agent_id and delegate_uuid == agent_id:
                    errors.append(f"Agent cannot delegate to itself ('{delegate_id}')")
                    continue

                result = await db.execute(
                    select(Agent).where(Agent.id == delegate_uuid)
                )
                delegate = result.scalar_one_or_none()
                if delegate is None:
                    errors.append(f"delegated_agent_id '{delegate_id}' does not reference an existing agent")
                elif not delegate.is_active:
                    errors.append(f"delegated_agent_id '{delegate_id}' references an inactive agent")
            except ValueError:
                errors.append(f"delegated_agent_id '{delegate_id}' is not a valid UUID")

    if errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"errors": errors, "message": "Invalid agent references"},
        )


async def _validate_user_tool_access(
    db: DbSession,
    user_id: UUID,
    tool_ids: list[str],
    is_external: bool = False,
) -> None:
    """Validate user can access all specified tools via their roles.

    External users get no authenticated-tier entitlement (EXT-1 rule 2):
    a workflow with access_level='authenticated' still requires a role
    intersection for them.
    """
    if not tool_ids:
        return

    from src.models.orm.users import UserRole
    from src.models.orm.workflow_roles import WorkflowRole

    # Get user's role IDs
    result = await db.execute(
        select(UserRole.role_id).where(UserRole.user_id == user_id)
    )
    user_role_ids = set(result.scalars().all())

    for tool_id in tool_ids:
        try:
            workflow_uuid = UUID(tool_id)
        except ValueError:
            raise HTTPException(422, f"Invalid tool ID: {tool_id}")

        result = await db.execute(
            select(Workflow).where(Workflow.id == workflow_uuid)
        )
        workflow = result.scalar_one_or_none()
        if not workflow:
            raise HTTPException(422, f"Tool '{tool_id}' not found")
        if not workflow.is_active:
            raise HTTPException(422, f"Tool '{workflow.name}' is inactive")

        if workflow.access_level == "everyone":
            continue

        if workflow.access_level == "authenticated" and not is_external:
            continue

        result = await db.execute(
            select(WorkflowRole.role_id).where(WorkflowRole.workflow_id == workflow_uuid)
        )
        workflow_role_ids = set(result.scalars().all())

        if not workflow_role_ids or not workflow_role_ids.intersection(user_role_ids):
            raise HTTPException(403, f"You do not have role access to tool '{workflow.name}'")


async def _user_has_permission(
    db: DbSession,
    user_id: UUID,
    permission: str,
) -> bool:
    """Check if a user has a permission via any of their roles."""
    from src.models.orm.users import UserRole

    result = await db.execute(
        select(Role.permissions)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user_id)
    )
    for permissions in result.scalars().all():
        if permissions and permissions.get(permission):
            return True
    return False


def _logo_data_url(data: bytes | None, content_type: str | None) -> str | None:
    """Encode a binary logo as a data URL, or None if no logo is set."""
    if not data:
        return None
    mime = content_type or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


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
        llm_model=agent.llm_model,
        llm_max_tokens=agent.llm_max_tokens,
        max_iterations=agent.max_iterations,
        max_token_budget=agent.max_token_budget,
        logo=_logo_data_url(agent.logo_data, agent.logo_content_type),
        is_solution_managed=agent.solution_id is not None,
        solution_id=agent.solution_id,
    )


# =============================================================================
# Agent CRUD Endpoints
# =============================================================================


@router.get("")
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
    # Apply organization filter using repository
    try:
        filter_type, filter_org_id = resolve_org_filter(user, scope)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )

    # Check if user is platform admin
    is_admin = user.is_platform_admin

    # Create repository with appropriate access context
    repo = AgentRepository(
        session=db,
        org_id=filter_org_id,
        user_id=user.user_id,
        is_superuser=is_admin,
        is_external=user.is_external,
    )

    if is_admin:
        # Admins use list_all_in_scope with filter_type for flexibility
        agents = await repo.list_all_in_scope(filter_type, active_only=active_only)
    else:
        # Regular users use list_agents with built-in cascade + role-based access
        agents = await repo.list_agents(active_only=active_only)

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

    result = []
    for a in agents:
        summary = AgentSummary.model_validate(a)
        summary.dependency_count = dep_counts.get(a.id, 0)
        summary.mcp_connection_count = mcp_counts.get(a.id, 0)
        summary.logo = _logo_data_url(a.logo_data, a.logo_content_type)
        result.append(summary)

    return result


@router.post("", status_code=status.HTTP_201_CREATED)
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
    is_admin = user.is_platform_admin

    if not is_admin:
        # Non-admin: enforce private-only creation
        if agent_data.access_level != AgentAccessLevel.PRIVATE:
            raise HTTPException(403, "Non-admin users can only create private agents")
        agent_data.organization_id = user.organization_id
        await _validate_user_tool_access(
            db, user.user_id, agent_data.tool_ids, is_external=user.is_external
        )
        agent_data.system_tools = []
        agent_data.knowledge_sources = []
        agent_data.delegated_agent_ids = []
        agent_data.role_ids = []
        # Non-admins cannot grant MCP connections — those are an
        # org-admin tool. Private agents simply don't surface MCP tools.
        agent_data.mcp_connection_ids = []

    # Validate references before creating the agent
    await _validate_agent_references(
        db=db,
        tool_ids=agent_data.tool_ids,
        delegated_agent_ids=agent_data.delegated_agent_ids,
        agent_id=None,
    )

    agent_id = uuid4()
    now = datetime.now(timezone.utc)

    # Set owner for private agents
    owner_user_id = None
    if agent_data.access_level == AgentAccessLevel.PRIVATE:
        owner_user_id = user.user_id

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
        llm_model=agent_data.llm_model,
        llm_max_tokens=agent_data.llm_max_tokens,
        max_iterations=agent_data.max_iterations,
        max_token_budget=agent_data.max_token_budget,
        created_by=user.email,
        created_at=now,
        updated_at=now,
    )
    db.add(agent)

    # Add tool relationships
    tools: list[Workflow] = []
    if agent_data.tool_ids:
        for tool_id in agent_data.tool_ids:
            try:
                workflow_uuid = UUID(tool_id)
                result = await db.execute(
                    select(Workflow)
                    .where(Workflow.id == workflow_uuid)
                    .where(Workflow.type == "tool")
                    .where(Workflow.is_active.is_(True))
                )
                workflow = result.scalar_one_or_none()
                if workflow:
                    tools.append(workflow)
                    db.add(AgentTool(agent_id=agent_id, workflow_id=workflow.id))
            except ValueError:
                logger.warning(f"Invalid tool ID: {log_safe(tool_id)}")

    # Add delegation relationships
    delegated_agents: list[Agent] = []
    if agent_data.delegated_agent_ids:
        for delegate_id in agent_data.delegated_agent_ids:
            try:
                delegate_uuid = UUID(delegate_id)
                result = await db.execute(
                    select(Agent)
                    .where(Agent.id == delegate_uuid)
                    .where(Agent.is_active.is_(True))
                )
                delegate = result.scalar_one_or_none()
                if delegate:
                    delegated_agents.append(delegate)
                    db.add(AgentDelegation(
                        parent_agent_id=agent_id,
                        child_agent_id=delegate.id,
                    ))
            except ValueError:
                logger.warning(f"Invalid delegate agent ID: {log_safe(delegate_id)}")

    # Add role relationships
    if agent_data.role_ids:
        for role_id in agent_data.role_ids:
            try:
                role_uuid = UUID(role_id)
                result = await db.execute(
                    select(Role).where(Role.id == role_uuid)
                )
                role = result.scalar_one_or_none()
                if role:
                    db.add(AgentRole(
                        agent_id=agent_id,
                        role_id=role.id,
                        assigned_by=user.email,
                    ))
            except ValueError:
                logger.warning(f"Invalid role ID: {log_safe(role_id)}")

    # Add MCP connection grants. Connections must belong to the same org
    # as the agent — connections are strictly per-org so a grant from
    # another org is silently dropped here. Platform-level agents
    # (organization_id IS NULL) cannot carry MCP grants.
    if agent_data.mcp_connection_ids and agent_data.organization_id is not None:
        valid_result = await db.execute(
            select(MCPConnection.id).where(
                MCPConnection.id.in_(agent_data.mcp_connection_ids),
                MCPConnection.organization_id == agent_data.organization_id,
            )
        )
        valid_ids = {row[0] for row in valid_result.all()}
        for cid in agent_data.mcp_connection_ids:
            if cid in valid_ids:
                db.add(AgentMCPConnection(
                    agent_id=agent_id,
                    connection_id=cid,
                    granted_by=user.user_id,
                ))

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


@router.get("/accessible-tools")
async def get_accessible_tools(
    db: DbSession,
    user: CurrentActiveUser,
) -> list[AccessibleTool]:
    """Get tools the current user can assign to their agents (via role intersection)."""
    from src.models.orm.users import UserRole
    from src.models.orm.workflow_roles import WorkflowRole

    result = await db.execute(
        select(UserRole.role_id).where(UserRole.user_id == user.user_id)
    )
    role_ids = list(result.scalars().all())

    if not role_ids:
        return []

    result = await db.execute(
        select(Workflow)
        .join(WorkflowRole, WorkflowRole.workflow_id == Workflow.id)
        .where(Workflow.type == "tool")
        .where(Workflow.is_active.is_(True))
        .where(WorkflowRole.role_id.in_(role_ids))
        .distinct()
    )
    tools = result.scalars().all()

    return [
        AccessibleTool(id=str(t.id), name=t.name, description=t.tool_description or t.description)
        for t in tools
    ]


@router.get("/accessible-knowledge")
async def get_accessible_knowledge(
    db: DbSession,
    user: CurrentActiveUser,
) -> list[AccessibleKnowledgeSource]:
    """Get knowledge sources the current user can assign to their agents."""
    from src.models.orm.users import UserRole
    from src.models.orm.knowledge_sources import KnowledgeNamespaceRole

    result = await db.execute(
        select(UserRole.role_id).where(UserRole.user_id == user.user_id)
    )
    role_ids = list(result.scalars().all())

    if not role_ids:
        return []

    result = await db.execute(
        select(KnowledgeNamespaceRole.namespace)
        .where(KnowledgeNamespaceRole.role_id.in_(role_ids))
        .distinct()
    )
    accessible_namespaces = list(result.scalars().all())

    return [
        AccessibleKnowledgeSource(id=ns, name=ns, namespace=ns, description=None)
        for ns in sorted(accessible_namespaces)
    ]


@router.get("/stats/fleet", response_model=FleetStatsResponse)
async def get_fleet_stats_endpoint(
    db: DbSession,
    user: CurrentActiveUser,
    window_days: int = Query(7, ge=1, le=90),
) -> FleetStatsResponse:
    """Fleet-wide agent run stats over the last ``window_days``.

    Superusers see cross-org totals; org users are scoped to their org.
    Route is registered before ``/{agent_id}`` so the literal ``stats``
    prefix is not parsed as a UUID.
    """
    org_id = None if user.is_superuser else user.organization_id
    return await get_fleet_stats(db, org_id=org_id, window_days=window_days)


@router.get("/{agent_id}")
async def get_agent(
    agent_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
) -> AgentPublic:
    """Get agent by ID."""
    # Check if user is platform admin
    is_admin = user.is_platform_admin

    repo = AgentRepository(
        session=db,
        org_id=user.organization_id,
        user_id=user.user_id,
        is_superuser=is_admin,
        is_external=user.is_external,
    )

    agent = await repo.get_agent_with_access_check(agent_id)

    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {agent_id} not found",
        )

    return _agent_to_public(agent)


@router.put("/{agent_id}")
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

    # Solution-managed agents are read-only here; deploy is the writer.
    assert_not_solution_managed(agent)

    is_admin = user.is_platform_admin

    if not is_admin:
        # Budget fields gate: only platform admins can set per-agent budgets.
        # Block before the ownership check so the response is the same whether
        # the user owns the agent or not (no information leak about ownership).
        budget_fields_set = [
            f
            for f in ("max_iterations", "max_token_budget", "llm_max_tokens")
            if f in agent_data.model_fields_set
        ]
        if budget_fields_set:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Budget fields ("
                    + ", ".join(budget_fields_set)
                    + ") can only be set by platform administrators"
                ),
            )

        if agent.owner_user_id != user.user_id or agent.access_level != AgentAccessLevel.PRIVATE:
            raise HTTPException(403, "You can only edit your own private agents")
        if agent_data.access_level is not None and agent_data.access_level != AgentAccessLevel.PRIVATE:
            raise HTTPException(403, "Use the promote endpoint to change access level")
        if agent_data.tool_ids is not None:
            await _validate_user_tool_access(
                db, user.user_id, agent_data.tool_ids, is_external=user.is_external
            )
        agent_data.system_tools = None
        agent_data.knowledge_sources = None
        agent_data.delegated_agent_ids = None
        agent_data.role_ids = None
        # Non-admins cannot manage MCP connection grants — those are an
        # org-admin tool. Silently drop the field rather than 403 so the
        # UI can submit a single payload regardless of role.
        agent_data.mcp_connection_ids = None

    # Validate references being updated
    await _validate_agent_references(
        db=db,
        tool_ids=agent_data.tool_ids,
        delegated_agent_ids=agent_data.delegated_agent_ids,
        agent_id=agent_id,  # For self-delegation check
    )

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
    # Use model_fields_set to distinguish "not provided" from "explicitly null"
    if "organization_id" in agent_data.model_fields_set:
        agent.organization_id = agent_data.organization_id
    if agent_data.is_active is not None:
        agent.is_active = agent_data.is_active
    if agent_data.knowledge_sources is not None:
        agent.knowledge_sources = agent_data.knowledge_sources
    if agent_data.system_tools is not None:
        agent.system_tools = agent_data.system_tools
    if agent_data.llm_model is not None:
        agent.llm_model = agent_data.llm_model if agent_data.llm_model else None
    if agent_data.llm_max_tokens is not None:
        agent.llm_max_tokens = agent_data.llm_max_tokens if agent_data.llm_max_tokens else None
    if "max_iterations" in agent_data.model_fields_set:
        agent.max_iterations = agent_data.max_iterations
    if "max_token_budget" in agent_data.model_fields_set:
        agent.max_token_budget = agent_data.max_token_budget

    agent.updated_at = datetime.now(timezone.utc)

    # Update tool relationships if provided
    tools: list[Workflow] = []
    if agent_data.tool_ids is not None:
        await db.execute(
            delete(AgentTool).where(AgentTool.agent_id == agent_id)
        )
        for tool_id in agent_data.tool_ids:
            try:
                workflow_uuid = UUID(tool_id)
                result = await db.execute(
                    select(Workflow)
                    .where(Workflow.id == workflow_uuid)
                    .where(Workflow.type == "tool")
                    .where(Workflow.is_active.is_(True))
                )
                workflow = result.scalar_one_or_none()
                if workflow:
                    tools.append(workflow)
                    db.add(AgentTool(agent_id=agent_id, workflow_id=workflow.id))
            except ValueError:
                logger.warning(f"Invalid tool ID: {log_safe(tool_id)}")

    # Update delegation relationships if provided
    delegated_agents: list[Agent] = []
    if agent_data.delegated_agent_ids is not None:
        await db.execute(
            delete(AgentDelegation).where(AgentDelegation.parent_agent_id == agent_id)
        )
        for delegate_id in agent_data.delegated_agent_ids:
            try:
                delegate_uuid = UUID(delegate_id)
                result = await db.execute(
                    select(Agent)
                    .where(Agent.id == delegate_uuid)
                    .where(Agent.is_active.is_(True))
                )
                delegate = result.scalar_one_or_none()
                if delegate:
                    delegated_agents.append(delegate)
                    db.add(AgentDelegation(
                        parent_agent_id=agent_id,
                        child_agent_id=delegate.id,
                    ))
            except ValueError:
                logger.warning(f"Invalid delegate agent ID: {log_safe(delegate_id)}")

    # Clear all role assignments if requested
    if agent_data.clear_roles:
        await db.execute(
            delete(AgentRole).where(AgentRole.agent_id == agent_id)
        )
        # Also set to role_based access level (effectively no access)
        agent.access_level = AgentAccessLevel.ROLE_BASED
        logger.info(f"Cleared all role assignments for agent '{log_safe(agent.name)}'")

    # Update role relationships if provided (and not clearing)
    elif agent_data.role_ids is not None:
        await db.execute(
            delete(AgentRole).where(AgentRole.agent_id == agent_id)
        )
        for role_id in agent_data.role_ids:
            try:
                role_uuid = UUID(role_id)
                result = await db.execute(
                    select(Role).where(Role.id == role_uuid)
                )
                role = result.scalar_one_or_none()
                if role:
                    db.add(AgentRole(
                        agent_id=agent_id,
                        role_id=role.id,
                        assigned_by=user.email,
                    ))
            except ValueError:
                logger.warning(f"Invalid role ID: {log_safe(role_id)}")

    # Sync MCP connection grants if provided. ``mcp_connection_ids=None``
    # means "leave grants alone"; an empty list explicitly revokes all.
    if agent_data.mcp_connection_ids is not None:
        repo = AgentRepository(
            session=db,
            org_id=user.organization_id,
            user_id=user.user_id,
            is_superuser=is_admin,
            is_external=user.is_external,
        )
        await repo.set_mcp_connection_grants(
            agent_id,
            agent_data.mcp_connection_ids,
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


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    agent_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
) -> None:
    """Soft delete an agent. Admins can delete any agent. Users can delete their own private agents.

    System agents can be deleted - they will be recreated on next startup
    if they are still defined in the system agent definitions.
    """
    result = await db.execute(
        select(Agent).where(Agent.id == agent_id)
    )
    agent = result.scalar_one_or_none()

    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {agent_id} not found",
        )

    # Solution-managed agents are read-only here; deploy is the writer.
    assert_not_solution_managed(agent)

    is_admin = user.is_platform_admin

    if not is_admin:
        if agent.owner_user_id != user.user_id:
            raise HTTPException(403, "You can only delete your own private agents")

    # Soft delete
    agent.is_active = False
    agent.updated_at = datetime.now(timezone.utc)
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
    if not agent:
        raise HTTPException(404, f"Agent {agent_id} not found")
    assert_not_solution_managed(agent)

    if agent.access_level != AgentAccessLevel.PRIVATE:
        raise HTTPException(400, "Agent is not private — nothing to promote")

    is_admin = user.is_platform_admin

    if not is_admin:
        if agent.owner_user_id != user.user_id:
            raise HTTPException(403, "You can only promote your own agents")
        if not await _user_has_permission(db, user.user_id, "can_promote_agent"):
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
    is_admin = user.is_superuser or any(
        role in ["Platform Admin", "Platform Owner"]
        for role in user.roles
    )

    repo = AgentRepository(
        session=db,
        org_id=user.organization_id,
        user_id=user.user_id,
        is_superuser=is_admin,
        is_external=user.is_external,
    )

    agent = await repo.get_agent_with_access_check(agent_id)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {agent_id} not found",
        )

    return await get_agent_stats(agent_id, db, window_days=window_days)


@router.get("/{agent_id}/tools")
async def get_agent_tools(
    agent_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
) -> list[dict]:
    """Get tools assigned to an agent."""
    # Check if user is platform admin
    is_admin = user.is_platform_admin

    repo = AgentRepository(
        session=db,
        org_id=user.organization_id,
        user_id=user.user_id,
        is_superuser=is_admin,
        is_external=user.is_external,
    )

    agent = await repo.get_agent_with_access_check(agent_id)

    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {agent_id} not found",
        )

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
    # Check if user is platform admin
    is_admin = user.is_platform_admin

    repo = AgentRepository(
        session=db,
        org_id=user.organization_id,
        user_id=user.user_id,
        is_superuser=is_admin,
        is_external=user.is_external,
    )

    agent = await repo.get_agent_with_access_check(agent_id)

    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {agent_id} not found",
        )

    summaries = []
    for a in agent.delegated_agents:
        s = AgentSummary.model_validate(a)
        s.logo = _logo_data_url(a.logo_data, a.logo_content_type)
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
    is_admin = user.is_platform_admin
    repo = AgentRepository(
        session=db,
        org_id=user.organization_id,
        user_id=user.user_id,
        is_superuser=is_admin,
        is_external=user.is_external,
    )
    agent = await repo.get_agent_with_access_check(agent_id)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {agent_id} not found",
        )
    assert_not_solution_managed(agent)

    if file.content_type not in LOGO_ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid file type. Allowed: {', '.join(sorted(LOGO_ALLOWED_CONTENT_TYPES))}",
        )

    content = await file.read()
    if len(content) > LOGO_MAX_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File too large. Maximum size: {LOGO_MAX_SIZE // 1024 // 1024} MB",
        )

    if file.content_type == "image/svg+xml":
        try:
            content = sanitize_svg(content)
        except SvgSanitizationError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid SVG: {exc}",
            )

    agent.logo_data = content
    agent.logo_content_type = file.content_type
    await db.commit()
    return {"ok": True}


@router.get(
    "/{agent_id}/logo",
    responses={
        200: {"content": {"image/png": {}, "image/jpeg": {}, "image/svg+xml": {}}},
        404: {"description": "No logo set"},
    },
)
async def get_agent_logo(
    agent_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
) -> Response:
    is_admin = user.is_platform_admin
    repo = AgentRepository(
        session=db,
        org_id=user.organization_id,
        user_id=user.user_id,
        is_superuser=is_admin,
        is_external=user.is_external,
    )
    agent = await repo.get_agent_with_access_check(agent_id)
    if not agent or not agent.logo_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Logo not set",
        )
    return Response(
        content=agent.logo_data,
        media_type=agent.logo_content_type or "application/octet-stream",
    )


@router.delete("/{agent_id}/logo", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent_logo(
    agent_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
) -> Response:
    is_admin = user.is_platform_admin
    repo = AgentRepository(
        session=db,
        org_id=user.organization_id,
        user_id=user.user_id,
        is_superuser=is_admin,
        is_external=user.is_external,
    )
    agent = await repo.get_agent_with_access_check(agent_id)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {agent_id} not found",
        )
    assert_not_solution_managed(agent)
    agent.logo_data = None
    agent.logo_content_type = None
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
