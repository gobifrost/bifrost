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
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, Response
from starlette.background import BackgroundTask
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
    AgentSkillFilePublic,
    AgentSkillPublic,
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
    AIModelProfile,
    MCPConnection,
    Role,
    Workflow,
)
from shared.logo_processing import (
    LogoProcessingError,
    is_logo_thumbnail_version,
    process_logo,
)
from src.repositories.agents import AgentRepository
from src.services.agent_skills import (
    build_agent_skill_archive,
    get_agent_skill_markdown,
    list_agent_skill_files,
    parse_skill_frontmatter,
    read_agent_skill_file,
    refresh_agent_skill_revision,
    resolve_agent_skill_revision,
    skill_slug,
)
from src.services.agent_skill_import import (
    AGENT_SKILL_ARCHIVE_LIMIT,
    import_agent_skill_archive,
    skill_instruction_body,
)
from src.services.agent_skill_storage import AgentSkillStorage
from src.services.audit import emit_audit
from src.services.builder.fs_tools import WorkspaceViolation
from src.services.operation_catalog import operation_route
from src.services.repo_sync_writer import RepoSyncWriter
from src.services.solutions.guard import assert_not_solution_managed
from src.routers.tools import get_system_tool_ids
from src.services.agent_stats import get_agent_stats, get_agent_stats_batch, get_fleet_stats
from src.services.workflow_role_service import sync_agent_roles_to_workflows

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents", tags=["Agents"])

SKILL_UPLOAD_CHUNK_SIZE = 1024 * 1024


async def _accessible_agent(
    db: DbSession,
    user,
    agent_id: UUID,
) -> Agent | None:
    """Resolve an agent through repository-level visibility checks."""
    repo = AgentRepository(
        session=db,
        org_id=user.organization_id,
        user_id=user.user_id,
        is_superuser=user.is_platform_admin,
        is_external=user.is_external,
    )
    return await repo.get_agent(agent_id)


async def _validate_agent_references(
    db: DbSession,
    tool_ids: list[str] | None,
    delegated_agent_ids: list[str] | None,
    role_ids: list[str] | None,
    mcp_connection_ids: list[UUID] | None,
    organization_id: UUID | None,
    agent_id: UUID | None = None,  # For self-delegation check
) -> None:
    """
    Validate that all referenced tools and agents exist and are valid.

    Args:
        db: Database session
        tool_ids: List of tool IDs to validate (must be type='tool')
        delegated_agent_ids: List of agent IDs to delegate to
        role_ids: List of resource-assignable role IDs
        mcp_connection_ids: List of MCP connection IDs to grant
        organization_id: Effective Agent organization after the mutation
        agent_id: The agent being created/updated (for self-delegation check)

    Raises:
        HTTPException: 422 if any reference is invalid
    """
    errors: list[str] = []

    for field_name, values in (
        ("tool_ids", tool_ids),
        ("delegated_agent_ids", delegated_agent_ids),
        ("role_ids", role_ids),
        ("mcp_connection_ids", mcp_connection_ids),
    ):
        if not values:
            continue
        seen: set[str] = set()
        for value in values:
            normalized = str(value)
            if normalized in seen:
                errors.append(
                    f"{field_name} contains duplicate reference '{normalized}'"
                )
            seen.add(normalized)

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

    if role_ids:
        for role_id in role_ids:
            try:
                role_uuid = UUID(role_id)
                result = await db.execute(select(Role).where(Role.id == role_uuid))
                role = result.scalar_one_or_none()
                if role is None:
                    errors.append(
                        f"role_id '{role_id}' does not reference an existing role"
                    )
                elif not role.assignable_to_resources:
                    errors.append(
                        f"role_id '{role_id}' is a capability role and cannot be "
                        "assigned to Agents"
                    )
            except ValueError:
                errors.append(f"role_id '{role_id}' is not a valid UUID")

    if mcp_connection_ids:
        if organization_id is None:
            errors.append(
                "Global Agents cannot be granted organization MCP connections"
            )
        else:
            for connection_id in mcp_connection_ids:
                result = await db.execute(
                    select(MCPConnection).where(MCPConnection.id == connection_id)
                )
                connection = result.scalar_one_or_none()
                if connection is None:
                    errors.append(
                        f"mcp_connection_id '{connection_id}' does not reference "
                        "an existing connection"
                    )
                elif connection.organization_id != organization_id:
                    errors.append(
                        f"mcp_connection_id '{connection_id}' belongs to a different organization"
                    )

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


def _agent_logo_url(agent: Agent) -> str | None:
    """Return a logo URL without hiding legacy images during thumbnail backfill."""
    if is_logo_thumbnail_version(agent.logo_thumbnail_version):
        return f"/api/agents/{agent.id}/logo?v={agent.logo_thumbnail_version}"
    if agent.logo_content_type:
        return f"/api/agents/{agent.id}/logo"
    return None


def _assert_can_manage_skill(agent: Agent, user) -> None:
    """Authorize direct Skill uploads without exposing a shared storage root."""
    assert_not_solution_managed(agent)
    if user.is_platform_admin:
        return
    if (
        agent.owner_user_id != user.user_id
        or agent.access_level != AgentAccessLevel.PRIVATE
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only manage Skills for your own private agents",
        )


async def _spool_skill_upload(file: UploadFile) -> Path:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".zip", ".skill"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Upload a .zip or .skill archive",
        )
    tmp = tempfile.NamedTemporaryFile(
        prefix="bifrost-agent-skill-", suffix=suffix, delete=False
    )
    path = Path(tmp.name)
    total = 0
    try:
        with tmp:
            while chunk := await file.read(SKILL_UPLOAD_CHUNK_SIZE):
                total += len(chunk)
                if total > AGENT_SKILL_ARCHIVE_LIMIT:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail="Agent Skill archive exceeds the 25 MiB upload limit",
                    )
                tmp.write(chunk)
        return path
    except BaseException:
        path.unlink(missing_ok=True)
        raise


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
        bundle_path=agent.bundle_path,
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

    stats_by_agent = (
        await get_agent_stats_batch(agent_ids, db)
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


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    **operation_route("agents.create"),
)
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
    if "bundle_path" in agent_data.model_fields_set:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Create the agent first, then upload its .skill or .zip bundle",
        )

    # Org targeting follows the unified --org standard: an OMITTED
    # organization_id (HOME) defaults to the caller's org, so a bare create
    # never silently writes a global row. Explicit null still means global.
    # (Non-admins are forced to their own org below regardless.)
    if is_admin and "organization_id" not in agent_data.model_fields_set:
        agent_data.organization_id = user.organization_id

    if not is_admin:
        # Non-admin: enforce private-only creation
        if agent_data.access_level != AgentAccessLevel.PRIVATE:
            raise HTTPException(403, "Non-admin users can only create private agents")
        privileged_fields = [
            field_name
            for field_name, value in (
                ("system_tools", agent_data.system_tools),
                ("knowledge_sources", agent_data.knowledge_sources),
                ("delegated_agent_ids", agent_data.delegated_agent_ids),
                ("role_ids", agent_data.role_ids),
                ("mcp_connection_ids", agent_data.mcp_connection_ids),
            )
            if value
        ]
        if privileged_fields:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Only platform administrators can set Agent fields: "
                    + ", ".join(privileged_fields)
                ),
            )
        agent_data.organization_id = user.organization_id
        await _validate_user_tool_access(
            db, user.user_id, agent_data.tool_ids, is_external=user.is_external
        )
        agent_data.system_tools = []
        agent_data.knowledge_sources = []
        agent_data.delegated_agent_ids = []
        agent_data.role_ids = []
        agent_data.bundle_path = None
        # Non-admins cannot grant MCP connections — those are an
        # org-admin tool. Private agents simply don't surface MCP tools.
        agent_data.mcp_connection_ids = []

    # Validate references before creating the agent
    await _validate_agent_references(
        db=db,
        tool_ids=agent_data.tool_ids,
        delegated_agent_ids=agent_data.delegated_agent_ids,
        role_ids=agent_data.role_ids,
        mcp_connection_ids=agent_data.mcp_connection_ids,
        organization_id=agent_data.organization_id,
        agent_id=None,
    )
    await _validate_llm_profile_id(db, agent_data.llm_profile_id)

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
        bundle_path=agent_data.bundle_path,
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

    # Add tool relationships
    if agent_data.tool_ids:
        for tool_id in agent_data.tool_ids:
            db.add(AgentTool(agent_id=agent_id, workflow_id=UUID(tool_id)))

    # Add delegation relationships
    if agent_data.delegated_agent_ids:
        for delegate_id in agent_data.delegated_agent_ids:
            db.add(
                AgentDelegation(
                    parent_agent_id=agent_id,
                    child_agent_id=UUID(delegate_id),
                )
            )

    # Add role relationships
    if agent_data.role_ids:
        for role_id in agent_data.role_ids:
            db.add(
                AgentRole(
                    agent_id=agent_id,
                    role_id=UUID(role_id),
                    assigned_by=user.email,
                )
            )

    # Reference validation above guarantees that every connection exists and
    # belongs to this Agent's organization.
    if agent_data.mcp_connection_ids:
        for cid in agent_data.mcp_connection_ids:
            db.add(
                AgentMCPConnection(
                    agent_id=agent_id,
                    connection_id=cid,
                    granted_by=user.user_id,
                )
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
            selectinload(Agent.llm_profile),
        )
        .where(Agent.id == agent_id)
    )
    agent = result.scalar_one()

    # Sync agent roles to referenced workflows (tools) - additive
    await sync_agent_roles_to_workflows(db, agent, assigned_by=user.email)

    # A newly created Agent is always an inline projection (bundle_path is
    # rejected above), so the digest covers the rendered SKILL.md.
    await refresh_agent_skill_revision(agent)

    await emit_audit(
        db,
        "agent.create",
        resource_type="agent",
        resource_id=agent.id,
        details={
            "name": agent.name,
            "organization_id": (
                str(agent.organization_id) if agent.organization_id else None
            ),
            "access_level": agent.access_level.value if agent.access_level else None,
        },
    )
    await RepoSyncWriter(db).regenerate_manifest()

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


@router.get("/{agent_id}", **operation_route("agents.get"))
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


@router.get(
    "/{agent_id}/skill",
    response_model=AgentSkillPublic,
    summary="Inspect an Agent's portable skill projection",
)
async def get_agent_skill(
    agent_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
) -> AgentSkillPublic:
    repo = AgentRepository(
        session=db,
        org_id=user.organization_id,
        user_id=user.user_id,
        is_superuser=user.is_platform_admin,
        is_external=user.is_external,
    )
    agent = await repo.get_agent_with_access_check(agent_id)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {agent_id} not found",
        )

    try:
        markdown = await get_agent_skill_markdown(agent)
        companion_files = await list_agent_skill_files(agent)
        revision = await resolve_agent_skill_revision(agent)
        if agent.bundle_path:
            skill_name, skill_description = parse_skill_frontmatter(markdown)
        else:
            skill_name = skill_slug(agent.name)
            skill_description = (
                agent.description or f"Use the {agent.name} agent"
            ).strip()
    except (WorkspaceViolation, FileNotFoundError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return AgentSkillPublic(
        name=skill_name,
        description=skill_description,
        revision=revision,
        bundle_path=agent.bundle_path,
        skill_markdown=markdown,
        files=["SKILL.md", *companion_files],
        companion_files=companion_files,
        automatic_capabilities=["bifrost_read_agent_skill_file"] if agent.bundle_path else [],
        source=(
            "solution"
            if agent.solution_id is not None
            else "upload"
            if agent.bundle_path
            else "inline"
        ),
        is_managed=agent.solution_id is not None,
    )


@router.get(
    "/{agent_id}/skill/file",
    response_model=AgentSkillFilePublic,
    summary="Read one file from an Agent Skill bundle",
)
async def get_agent_skill_file(
    agent_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
    path: str = Query(..., min_length=1, max_length=1024),
) -> AgentSkillFilePublic:
    repo = AgentRepository(
        session=db,
        org_id=user.organization_id,
        user_id=user.user_id,
        is_superuser=user.is_platform_admin,
        is_external=user.is_external,
    )
    agent = await repo.get_agent_with_access_check(agent_id)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {agent_id} not found",
        )
    try:
        content = await read_agent_skill_file(agent, path)
    except WorkspaceViolation as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Skill file not found: {path}",
        ) from exc
    try:
        encoded = content.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        encoded = base64.b64encode(content).decode("ascii")
        encoding = "base64"
    return AgentSkillFilePublic(path=path, encoding=encoding, content=encoded)


@router.put(
    "/{agent_id}/skill/bundle",
    response_model=AgentSkillPublic,
    summary="Upload or replace an Agent Skill bundle",
)
async def upload_agent_skill(
    agent_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
    file: UploadFile = File(..., description=".skill or .zip Agent Skill archive"),
) -> AgentSkillPublic:
    agent = await _accessible_agent(db, user, agent_id)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {agent_id} not found",
        )
    _assert_can_manage_skill(agent, user)
    archive_path = await _spool_skill_upload(file)
    try:
        imported = import_agent_skill_archive(archive_path)
    except WorkspaceViolation as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    finally:
        archive_path.unlink(missing_ok=True)

    storage = AgentSkillStorage(agent.id)
    previous_paths = set(await storage.list())
    for storage_path, content in imported.files.items():
        await storage.write(storage_path, content)

    agent.bundle_path = imported.bundle_path
    agent.system_prompt = imported.skill_markdown
    agent.created_by = user.email
    agent.updated_at = datetime.now(timezone.utc)
    await db.commit()

    current_paths = set(imported.files)
    for stale_path in sorted(previous_paths - current_paths):
        await storage.delete(stale_path)

    # Stamp the revision only once storage reflects the new bundle: the stale
    # sweep above runs after the first commit, so digesting earlier would hash
    # files that are about to disappear.
    revision = await refresh_agent_skill_revision(agent)
    await db.commit()

    companion_files = sorted(
        path[len(imported.bundle_path) + 1 :]
        for path in current_paths
        if not path.endswith("/SKILL.md")
    )
    return AgentSkillPublic(
        name=imported.name,
        description=imported.description,
        revision=revision,
        bundle_path=imported.bundle_path,
        skill_markdown=imported.skill_markdown,
        files=["SKILL.md", *companion_files],
        companion_files=companion_files,
        automatic_capabilities=["bifrost_read_agent_skill_file"],
        source="upload",
        is_managed=False,
    )


@router.delete(
    "/{agent_id}/skill/bundle",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Detach an uploaded bundle and return the Agent to inline instructions",
)
async def detach_agent_skill(
    agent_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
) -> Response:
    agent = await _accessible_agent(db, user, agent_id)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {agent_id} not found",
        )
    _assert_can_manage_skill(agent, user)
    if not agent.bundle_path:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    markdown = await get_agent_skill_markdown(agent)
    instructions = skill_instruction_body(markdown)
    if not instructions:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="SKILL.md has no instruction body to preserve",
        )
    agent.system_prompt = instructions
    agent.bundle_path = None
    agent.updated_at = datetime.now(timezone.utc)
    # Now an inline projection: the digest covers the rendered SKILL.md alone.
    await refresh_agent_skill_revision(agent)
    await db.commit()
    await AgentSkillStorage(agent.id).clear()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{agent_id}/skill/download",
    summary="Download an Agent as a portable Agent Skill",
)
async def download_agent_skill(
    agent_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
) -> FileResponse:
    """Stream ``SKILL.md`` plus companion bundle assets for an accessible Agent."""
    repo = AgentRepository(
        session=db,
        org_id=user.organization_id,
        user_id=user.user_id,
        is_superuser=user.is_platform_admin,
        is_external=user.is_external,
    )
    agent = await repo.get_agent_with_access_check(agent_id)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {agent_id} not found",
        )

    archive_context = build_agent_skill_archive(agent)
    try:
        archive_path = await archive_context.__aenter__()
    except WorkspaceViolation as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    async def cleanup() -> None:
        await archive_context.__aexit__(None, None, None)

    portable_name = (
        parse_skill_frontmatter(await get_agent_skill_markdown(agent))[0]
        if agent.bundle_path
        else agent.name
    )
    filename = f"{skill_slug(portable_name)}.zip"
    return FileResponse(
        archive_path,
        media_type="application/zip",
        filename=filename,
        background=BackgroundTask(cleanup),
    )


@router.put("/{agent_id}", **operation_route("agents.update"))
async def update_agent(
    agent_id: UUID,
    agent_data: AgentUpdate,
    db: DbSession,
    user: CurrentActiveUser,
) -> AgentPublic:
    """Update an agent. Admins can update any agent. Users can update their own private agents."""
    agent = await _accessible_agent(db, user, agent_id)

    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {agent_id} not found",
        )

    # Solution-managed agents are read-only here; deploy is the writer.
    assert_not_solution_managed(agent)
    if "bundle_path" in agent_data.model_fields_set:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Use the Agent Skill upload or remove action to manage bundles",
        )
    if agent.bundle_path and agent_data.system_prompt is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Bundled Agent instructions come from SKILL.md; replace or remove the bundle",
        )

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
        privileged_fields = [
            field_name
            for field_name, value in (
                ("system_tools", agent_data.system_tools),
                ("knowledge_sources", agent_data.knowledge_sources),
                ("delegated_agent_ids", agent_data.delegated_agent_ids),
                ("role_ids", agent_data.role_ids),
            )
            if value
        ]
        if agent_data.clear_roles:
            privileged_fields.append("clear_roles")
        if privileged_fields:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Only platform administrators can set Agent fields: "
                    + ", ".join(privileged_fields)
                ),
            )
        agent_data.system_tools = None
        agent_data.knowledge_sources = None
        agent_data.delegated_agent_ids = None
        agent_data.role_ids = None
        if agent_data.mcp_connection_ids is not None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only platform administrators can manage Agent MCP connections",
            )

    if agent_data.clear_roles and agent_data.role_ids is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="clear_roles and role_ids cannot be provided together",
        )

    target_organization_id = (
        agent_data.organization_id
        if "organization_id" in agent_data.model_fields_set
        else agent.organization_id
    )
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

    # Validate references being updated
    await _validate_agent_references(
        db=db,
        tool_ids=agent_data.tool_ids,
        delegated_agent_ids=agent_data.delegated_agent_ids,
        role_ids=agent_data.role_ids,
        mcp_connection_ids=agent_data.mcp_connection_ids,
        organization_id=target_organization_id,
        agent_id=agent_id,  # For self-delegation check
    )
    if "llm_profile_id" in agent_data.model_fields_set:
        await _validate_llm_profile_id(db, agent_data.llm_profile_id)

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
    if "llm_profile_id" in agent_data.model_fields_set:
        agent.llm_profile_id = agent_data.llm_profile_id
    if agent_data.llm_max_tokens is not None:
        agent.llm_max_tokens = agent_data.llm_max_tokens if agent_data.llm_max_tokens else None
    if "max_iterations" in agent_data.model_fields_set:
        agent.max_iterations = agent_data.max_iterations
    if "max_token_budget" in agent_data.model_fields_set:
        agent.max_token_budget = agent_data.max_token_budget

    agent.updated_at = datetime.now(timezone.utc)

    # Update tool relationships if provided
    if agent_data.tool_ids is not None:
        await db.execute(
            delete(AgentTool).where(AgentTool.agent_id == agent_id)
        )
        for tool_id in agent_data.tool_ids:
            db.add(AgentTool(agent_id=agent_id, workflow_id=UUID(tool_id)))

    # Update delegation relationships if provided
    if agent_data.delegated_agent_ids is not None:
        await db.execute(
            delete(AgentDelegation).where(AgentDelegation.parent_agent_id == agent_id)
        )
        for delegate_id in agent_data.delegated_agent_ids:
            db.add(
                AgentDelegation(
                    parent_agent_id=agent_id,
                    child_agent_id=UUID(delegate_id),
                )
            )

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
            db.add(
                AgentRole(
                    agent_id=agent_id,
                    role_id=UUID(role_id),
                    assigned_by=user.email,
                )
            )

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
            selectinload(Agent.llm_profile),
            selectinload(Agent.mcp_connections),
        )
        .where(Agent.id == agent_id)
    )
    agent = result.scalar_one()

    # Sync agent roles to referenced workflows (tools) - additive
    await sync_agent_roles_to_workflows(db, agent, assigned_by=user.email)

    # Name, description, and system_prompt all feed the rendered SKILL.md of an
    # inline Agent, so restamp. A bundled Agent's content is unreachable from
    # here (system_prompt edits are rejected above), but recomputing is cheap
    # and keeps one rule rather than a field-set special case.
    await refresh_agent_skill_revision(agent)

    await emit_audit(
        db,
        "agent.update",
        resource_type="agent",
        resource_id=agent.id,
        details={
            "name": agent.name,
            "fields": sorted(agent_data.model_fields_set),
        },
    )
    await RepoSyncWriter(db).regenerate_manifest()

    return _agent_to_public(agent)


@router.delete(
    "/{agent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    **operation_route("agents.delete"),
)
async def delete_agent(
    agent_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
) -> None:
    """Permanently delete an agent. Admins can delete any agent. Users can delete their own private agents.

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

    agent_name = agent.name
    # Use a SQL DELETE so database-level cascades remove run history and agent
    # memberships while SET NULL references (such as conversations) are preserved.
    await db.execute(delete(Agent).where(Agent.id == agent_id))
    await db.flush()
    await emit_audit(
        db,
        "agent.delete",
        resource_type="agent",
        resource_id=agent_id,
        details={"name": agent_name},
    )
    await RepoSyncWriter(db).regenerate_manifest()


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
            selectinload(Agent.llm_profile),
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
    is_admin = user.has_platform_admin_grant()

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
    agent.logo_thumbnail_data = None
    agent.logo_thumbnail_content_type = None
    agent.logo_thumbnail_version = None
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
