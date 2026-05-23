"""
Workflows Router

Handles workflow discovery, execution, and validation.

Note: Workflows are discovered by the Discovery container and synced to the
database. This router queries the database for workflow metadata, providing
fast O(1) lookups instead of file system scanning.

Organization Scoping:
- Workflows with organization_id = NULL are global (available to all orgs)
- Workflows with organization_id set are org-scoped
- Queries filter: global + user's org (unless platform admin requests all)
"""

import logging
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy import delete, distinct, func, or_, select, union_all, update

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# Import existing Pydantic models for API compatibility
from src.models.enums import ExecutionStatus
from src.models import (
    AssignRolesToWorkflowRequest,
    CompatibleReplacement,
    CompatibleReplacementsResponse,
    DeactivateWorkflowResponse,
    DeleteWorkflowRequest,
    EntityUsage,
    OrphanedWorkflowInfo,
    OrphanedWorkflowsResponse,
    RecreateFileResponse,
    RegisterWorkflowRequest,
    RegisterWorkflowResponse,
    ReplaceWorkflowRequest,
    ReplaceWorkflowResponse,
    WorkflowExecutionRequest,
    WorkflowExecutionResponse,
    WorkflowMetadata,
    WorkflowParameter,
    WorkflowReference,
    WorkflowRolesResponse,
    WorkflowUpdateRequest,
    WorkflowUsageStats,
    WorkflowValidationRequest,
    WorkflowValidationResponse,
)
from src.models import Workflow as WorkflowORM
from src.models.orm.workflow_roles import WorkflowRole
from src.models.orm.forms import Form, FormField
from src.models.orm.applications import Application
from src.models.orm.agents import Agent, AgentTool
from src.models.orm.developer import DeveloperContext
from src.models.orm.users import Role
from src.services.workflow_validation import _extract_relative_path

from src.core.auth import Context, CurrentActiveUser, CurrentSuperuser
from src.core.database import DbSession
from src.core.log_safety import log_safe
from src.core.pubsub import publish_execution_update, publish_history_update
from src.core.cache import get_cached_data_provider

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/workflows", tags=["Workflows"])


# =============================================================================
# Helper Functions
# =============================================================================


def _convert_workflow_orm_to_schema(workflow: WorkflowORM, used_by_count: int = 0) -> WorkflowMetadata:
    """Convert ORM model to Pydantic schema for API response."""
    from typing import Literal
    from src.models.contracts.workflows import ExecutableType

    # Convert parameters from JSONB to WorkflowParameter objects
    parameters = []
    for param in workflow.parameters_schema or []:
        if isinstance(param, dict):
            parameters.append(WorkflowParameter(**param))

    # Validate execution_mode - default to "sync" if invalid
    raw_mode = workflow.execution_mode or "sync"
    execution_mode: Literal["sync", "async"] = "async" if raw_mode == "async" else "sync"

    # Convert string type to ExecutableType enum
    workflow_type = ExecutableType(workflow.type or "workflow")

    return WorkflowMetadata(
        id=str(workflow.id),
        name=workflow.name,
        function_name=workflow.function_name,
        display_name=workflow.display_name,
        description=workflow.description if workflow.description else None,
        category=workflow.category or "General",
        tags=workflow.tags or [],
        type=workflow_type,
        organization_id=str(workflow.organization_id) if workflow.organization_id else None,
        access_level=workflow.access_level or "role_based",
        parameters=parameters,
        execution_mode=execution_mode,
        timeout_seconds=workflow.timeout_seconds if workflow.timeout_seconds is not None else 1800,
        retry_policy=None,
        endpoint_enabled=workflow.endpoint_enabled or False,
        allowed_methods=workflow.allowed_methods or ["POST"],
        disable_global_key=workflow.disable_global_key or False,
        public_endpoint=workflow.public_endpoint or False,
        is_tool=workflow.type == "tool",  # Derive from type field
        tool_description=workflow.tool_description,
        cache_ttl_seconds=workflow.cache_ttl_seconds or 300,
        time_saved=workflow.time_saved or 0,
        value=float(workflow.value or 0.0),
        used_by_count=used_by_count,
        source_file_path=workflow.path,
        relative_file_path=_extract_relative_path(workflow.path),
        created_at=workflow.created_at,
    )


def _extract_workflows_from_props(obj: Any, workflow_ids: set[str]) -> None:
    """Recursively extract workflowId and dataProviderId values from JSONB props.

    Modifies workflow_ids in place to collect all workflow references found in:
    - props.workflowId
    - props.onClick.workflowId
    - props.rowActions[].onClick.workflowId
    - props.headerActions[].onClick.workflowId
    - props.footerActions[].workflowId
    - Any nested structure containing workflowId or dataProviderId
    """
    if obj is None:
        return

    if isinstance(obj, dict):
        # Check for workflowId key
        if wf_id := obj.get("workflowId"):
            if isinstance(wf_id, str):
                workflow_ids.add(wf_id)

        # Check for dataProviderId key
        if dp_id := obj.get("dataProviderId"):
            if isinstance(dp_id, str):
                workflow_ids.add(dp_id)

        # Recursively process all values
        for value in obj.values():
            _extract_workflows_from_props(value, workflow_ids)

    elif isinstance(obj, list):
        for item in obj:
            _extract_workflows_from_props(item, workflow_ids)


async def _get_form_workflow_ids(db: DbSession, form_id: UUID) -> set[UUID]:
    """
    Get all workflow IDs referenced by a form.

    Extracts from:
    - form.workflow_id (main execution workflow)
    - form.launch_workflow_id (startup/pre-execution workflow)
    - form_fields.data_provider_id (dynamic field data providers)
    """
    from sqlalchemy.orm import selectinload

    result = await db.execute(
        select(Form)
        .options(selectinload(Form.fields))
        .where(Form.id == form_id)
    )
    form = result.scalar_one_or_none()

    if not form:
        return set()

    workflow_ids: set[UUID] = set()

    # Main workflow
    if form.workflow_id:
        try:
            workflow_ids.add(UUID(form.workflow_id))
        except ValueError as e:
            # Non-UUID portable ref (e.g. "path::func") — not a real workflow ID
            logger.debug(f"form.workflow_id not a UUID, skipping: {e}")

    # Launch workflow
    if form.launch_workflow_id:
        try:
            workflow_ids.add(UUID(form.launch_workflow_id))
        except ValueError as e:
            # Non-UUID portable ref — not a real workflow ID
            logger.debug(f"form.launch_workflow_id not a UUID, skipping: {e}")

    # Data providers from fields
    for field in form.fields:
        if field.data_provider_id:
            workflow_ids.add(field.data_provider_id)

    return workflow_ids


async def _get_app_workflow_ids(db: DbSession, app_id: UUID) -> set[UUID]:
    """
    Get all workflow IDs referenced by an app.

    Scans file_index for app source files and parses for workflow references.
    """
    from src.models.orm.file_index import FileIndex
    from src.models.orm.applications import Application
    from src.models.orm.workflows import Workflow as WfORM
    from src.services.app_dependencies import parse_dependencies

    # Get app
    app_result = await db.execute(
        select(Application).where(Application.id == app_id)
    )
    app = app_result.scalar_one_or_none()
    if not app:
        return set()

    # Scan file_index for source code
    prefix = app.repo_prefix
    fi_result = await db.execute(
        select(FileIndex.content).where(
            FileIndex.path.startswith(prefix),
        )
    )

    # Collect all refs from all files
    all_refs: set[str] = set()
    for (content,) in fi_result.all():
        if content:
            all_refs.update(parse_dependencies(content))

    if not all_refs:
        return set()

    # Resolve refs to workflow UUIDs
    wf_result = await db.execute(
        select(WfORM.id, WfORM.name).where(WfORM.is_active.is_(True))
    )
    matched: set[UUID] = set()
    for wf_id, wf_name in wf_result.all():
        if str(wf_id) in all_refs or wf_name in all_refs:
            matched.add(wf_id)

    return matched


async def _compute_used_by_counts(db: DbSession, workflow_ids: list[UUID]) -> dict[UUID, int]:
    """
    Batch-compute how many entities reference each workflow.

    Counts references from:
    - forms.workflow_id (main execution workflow)
    - forms.launch_workflow_id (pre-execution workflow)
    - form_fields.data_provider_id (dynamic data providers)
    - agent_tools.workflow_id (agent tool bindings)

    Returns a dict mapping workflow UUID -> count of referencing entities.
    """
    # Build individual reference queries. Form.workflow_id/launch_workflow_id
    # are String(255) while others are proper UUID columns, so cast form
    # columns to UUID for a consistent union.
    from sqlalchemy.dialects.postgresql import UUID as PG_UUID

    refs_form_wf = (
        select(Form.workflow_id.cast(PG_UUID(as_uuid=True)).label("wf_id"))
        .where(
            Form.is_active == True,  # noqa: E712
            Form.workflow_id.isnot(None),
            func.length(Form.workflow_id) == 36,  # filter non-UUID strings (e.g. portable refs)
        )
    )
    refs_form_launch = (
        select(Form.launch_workflow_id.cast(PG_UUID(as_uuid=True)).label("wf_id"))
        .where(
            Form.is_active == True,  # noqa: E712
            Form.launch_workflow_id.isnot(None),
            func.length(Form.launch_workflow_id) == 36,  # filter non-UUID strings
        )
    )
    refs_form_dp = (
        select(FormField.data_provider_id.label("wf_id"))
        .where(FormField.data_provider_id.isnot(None))
    )
    refs_agent = (
        select(AgentTool.workflow_id.label("wf_id"))
    )

    # Union all reference sources and count per workflow
    all_refs = union_all(
        refs_form_wf, refs_form_launch, refs_form_dp, refs_agent
    ).subquery("all_refs")

    count_query = (
        select(
            all_refs.c.wf_id,
            func.count().label("cnt"),
        )
        .where(all_refs.c.wf_id.in_(workflow_ids))
        .group_by(all_refs.c.wf_id)
    )

    result = await db.execute(count_query)
    return {row.wf_id: row.cnt for row in result.all()}


# =============================================================================
# HTTP Endpoints
# =============================================================================


@router.get(
    "",
    response_model=list[WorkflowMetadata],
    summary="List all workflows",
    description="Returns metadata for all registered workflows in the system",
)
async def list_workflows(
    user: CurrentSuperuser,
    db: DbSession,
    type: str | None = None,
    is_tool: bool | None = None,  # Deprecated, use type="tool" instead
    scope: str | None = Query(
        None,
        description="Filter scope: omit for user's org + global, 'global' for global only, "
                    "'all' for all workflows (platform admins only), or org UUID for specific org."
    ),
    filter_by_form: UUID | None = Query(
        None,
        description="Filter to workflows used by a specific form"
    ),
    filter_by_app: UUID | None = Query(
        None,
        description="Filter to workflows used by a specific app"
    ),
    filter_by_agent: UUID | None = Query(
        None,
        description="Filter to workflows used by a specific agent"
    ),
) -> list[WorkflowMetadata]:
    """List all registered workflows from the database.

    Workflows are discovered by the Discovery container and synced to the
    database. This endpoint queries the database for fast lookups.

    Organization scoping (consistent with forms, agents):
    - scope omitted: All workflows (platform admins only)
    - scope='global': Only global workflows (organization_id IS NULL)
    - scope=<uuid>: Only that org's workflows (no global fallback)

    Entity filtering:
    - filter_by_form: Show only workflows used by the specified form
    - filter_by_app: Show only workflows used by the specified app
    - filter_by_agent: Show only workflows used by the specified agent

    Args:
        type: Filter by workflow type ('workflow', 'tool', 'data_provider').
        is_tool: [Deprecated] Use type="tool" instead. Filter by tool-enabled workflows.
        scope: Organization scope filter. Omit for all (platform admins only).
        filter_by_form: Form UUID to filter workflows by.
        filter_by_app: App UUID to filter workflows by.
        filter_by_agent: Agent UUID to filter workflows by.
    """
    from src.core.org_filter import resolve_org_filter, OrgFilterType

    try:
        # Resolve organization filter using shared helper (consistent with forms)
        try:
            filter_type, filter_org = resolve_org_filter(user, scope)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e),
            )

        # Query active workflows from database
        query = select(WorkflowORM).where(WorkflowORM.is_active.is_(True))

        # Apply organization scope filter
        if filter_type == OrgFilterType.ALL:
            # Platform admin sees all - no org filter
            pass
        elif filter_type == OrgFilterType.GLOBAL_ONLY:
            # Only global workflows (no organization)
            query = query.where(WorkflowORM.organization_id.is_(None))
        elif filter_type == OrgFilterType.ORG_ONLY:
            # Only that org's workflows (platform admin filtering)
            query = query.where(WorkflowORM.organization_id == filter_org)
        elif filter_type == OrgFilterType.ORG_PLUS_GLOBAL:
            # User's org + global (org users)
            query = query.where(
                or_(
                    WorkflowORM.organization_id == filter_org,
                    WorkflowORM.organization_id.is_(None),
                )
            )

        # Filter by type
        if type is not None:
            query = query.where(WorkflowORM.type == type)
        # Legacy support: is_tool=True maps to type="tool"
        elif is_tool is not None:
            if is_tool:
                query = query.where(WorkflowORM.type == "tool")
            else:
                query = query.where(WorkflowORM.type != "tool")

        # Apply entity filters by querying entities directly
        if filter_by_form:
            # Get workflow IDs used by this form (direct query)
            workflow_ids = await _get_form_workflow_ids(db, filter_by_form)
            if workflow_ids:
                query = query.where(WorkflowORM.id.in_(workflow_ids))
            else:
                # No workflows found, return empty result
                return []
        elif filter_by_app:
            # Get workflow IDs used by this app (query pages/components)
            workflow_ids = await _get_app_workflow_ids(db, filter_by_app)
            if workflow_ids:
                query = query.where(WorkflowORM.id.in_(workflow_ids))
            else:
                # No workflows found, return empty result
                return []
        elif filter_by_agent:
            # Get workflow IDs used by this agent (via agent_tools)
            workflow_ids_subquery = select(AgentTool.workflow_id).where(
                AgentTool.agent_id == filter_by_agent,
            )
            query = query.where(WorkflowORM.id.in_(workflow_ids_subquery))

        result = await db.execute(query)
        workflows = result.scalars().all()

        # Batch-compute used_by_count for all workflows in a single query.
        # Counts references from: forms (workflow_id, launch_workflow_id),
        # form_fields (data_provider_id), and agent_tools.
        workflow_ids = [w.id for w in workflows]
        used_by_counts: dict[UUID, int] = {}
        if workflow_ids:
            used_by_counts = await _compute_used_by_counts(db, workflow_ids)

        # Convert ORM models to Pydantic schemas
        workflow_list = []
        for w in workflows:
            try:
                workflow_list.append(
                    _convert_workflow_orm_to_schema(w, used_by_count=used_by_counts.get(w.id, 0))
                )
            except Exception as e:
                logger.error(f"Failed to convert workflow '{w.name}': {e}")

        logger.info(f"Returning {len(workflow_list)} workflows (scope={log_safe(scope) or 'default'})")
        return workflow_list

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving workflows: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve workflows",
        )


@router.get(
    "/usage-stats",
    response_model=WorkflowUsageStats,
    summary="Get workflow usage stats by entity",
    description="Returns counts of workflows used by each form, app, and agent",
)
async def get_workflow_usage_stats(
    user: CurrentSuperuser,
    db: DbSession,
    scope: str | None = Query(
        None,
        description="Filter scope: omit for all (superusers), 'global' for global only, "
                    "or org UUID for specific org only."
    ),
) -> WorkflowUsageStats:
    """Get workflow usage statistics grouped by entity type.

    Uses query-time aggregation for accurate counts (includes draft/unpublished).
    Returns counts of workflows used by each form, app, and agent.
    Useful for identifying which entities use workflows and filtering.

    Scope parameter (consistent with forms, agents):
    - Omitted: show all entities (superusers only)
    - "global": show only global entities (org_id IS NULL) - returns empty for usage stats
    - UUID string: show only that org's entities (no global fallback)
    """
    from src.core.org_filter import resolve_org_filter, OrgFilterType

    try:
        # Use shared org filter helper for consistency with forms, agents
        filter_type, filter_org = resolve_org_filter(user, scope)

        # Determine org_filter based on filter_type
        if filter_type == OrgFilterType.ALL:
            org_filter = None  # No filtering - show all
        elif filter_type == OrgFilterType.GLOBAL_ONLY:
            # Global entities only - doesn't make sense for usage stats, return empty
            return WorkflowUsageStats(forms=[], apps=[], agents=[])
        elif filter_type == OrgFilterType.ORG_ONLY:
            # Platform admin filtering by specific org - only that org (no global)
            org_filter = filter_org
        else:
            # ORG_PLUS_GLOBAL - shouldn't happen for superuser, but handle it
            org_filter = filter_org

        # =========================================================================
        # Forms: workflow_id, launch_workflow_id, and fields.data_provider_id
        # =========================================================================
        # Count distinct workflows per form from all three sources
        forms_query = (
            select(Form.id, Form.name)
            .where(Form.is_active.is_(True))
            .order_by(Form.name)
        )
        if org_filter:
            forms_query = forms_query.where(Form.organization_id == org_filter)

        forms_result = await db.execute(forms_query)
        forms_list = forms_result.all()

        forms: list[EntityUsage] = []
        for form_row in forms_list:
            # Get workflow_id and launch_workflow_id from form
            form_wf_query = select(Form.workflow_id, Form.launch_workflow_id).where(
                Form.id == form_row.id
            )
            form_wf_result = await db.execute(form_wf_query)
            form_wf = form_wf_result.first()

            workflow_ids: set[str] = set()
            if form_wf:
                if form_wf.workflow_id:
                    workflow_ids.add(form_wf.workflow_id)
                if form_wf.launch_workflow_id:
                    workflow_ids.add(form_wf.launch_workflow_id)

            # Get data_provider_id from fields
            fields_query = select(FormField.data_provider_id).where(
                FormField.form_id == form_row.id,
                FormField.data_provider_id.isnot(None),
            )
            fields_result = await db.execute(fields_query)
            for field_row in fields_result.all():
                workflow_ids.add(str(field_row.data_provider_id))

            forms.append(
                EntityUsage(
                    id=str(form_row.id),
                    name=form_row.name,
                    workflow_count=len(workflow_ids),
                )
            )

        # =========================================================================
        # Agents: via agent_tools junction table
        # =========================================================================
        agents_query = (
            select(
                Agent.id,
                Agent.name,
                func.count(distinct(AgentTool.workflow_id)).label("workflow_count"),
            )
            .outerjoin(AgentTool, AgentTool.agent_id == Agent.id)
            .where(Agent.is_active.is_(True))
            .group_by(Agent.id, Agent.name)
            .order_by(Agent.name)
        )
        if org_filter:
            agents_query = agents_query.where(Agent.organization_id == org_filter)

        agents_result = await db.execute(agents_query)
        agents = [
            EntityUsage(
                id=str(row.id), name=row.name, workflow_count=row.workflow_count or 0
            )
            for row in agents_result.all()
        ]

        # =========================================================================
        # Apps: scan file_index for workflow references in source code
        # =========================================================================
        from src.models.orm.file_index import FileIndex
        from src.services.app_dependencies import parse_dependencies

        apps_base_query = (
            select(Application.id, Application.name, Application.slug, Application.repo_path)
            .order_by(Application.name)
        )
        if org_filter:
            apps_base_query = apps_base_query.where(Application.organization_id == org_filter)

        apps_base_result = await db.execute(apps_base_query)
        all_apps = apps_base_result.all()

        apps: list[EntityUsage] = []
        for app_row in all_apps:
            prefix = app_row.repo_path.rstrip("/") + "/"
            fi_result = await db.execute(
                select(FileIndex.content).where(
                    FileIndex.path.startswith(prefix),
                )
            )
            all_refs: set[str] = set()
            for (content,) in fi_result.all():
                if content:
                    all_refs.update(parse_dependencies(content))

            apps.append(
                EntityUsage(
                    id=str(app_row.id),
                    name=app_row.name,
                    workflow_count=len(all_refs),  # Count of unique refs (approximate)
                )
            )

        return WorkflowUsageStats(forms=forms, apps=apps, agents=agents)

    except ValueError as e:
        # Invalid scope value from resolve_org_filter
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving workflow usage stats: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve workflow usage stats",
        )


async def _insert_scheduled_execution(
    *,
    db: "AsyncSession",
    workflow_id: UUID,
    workflow_name: str,
    parameters: dict,
    scheduled_at: datetime,
    organization_id: UUID | None,
    executed_by: UUID,
    executed_by_name: str,
    form_id: UUID | None,
    api_key_id: UUID | None,
    is_platform_admin: bool,
) -> UUID:
    """Insert a SCHEDULED execution row.

    Skips Redis/RabbitMQ — the deferred_execution_promoter job will publish
    the row when scheduled_at matures.
    """
    from uuid import uuid4

    from src.models.orm.executions import Execution

    exec_id = uuid4()
    db.add(
        Execution(
            id=exec_id,
            workflow_id=workflow_id,
            workflow_name=workflow_name,
            status=ExecutionStatus.SCHEDULED,
            parameters=parameters,
            scheduled_at=scheduled_at,
            organization_id=organization_id,
            executed_by=executed_by,
            executed_by_name=executed_by_name,
            form_id=form_id,
            api_key_id=api_key_id,
            execution_context={"is_platform_admin": is_platform_admin},
        )
    )
    await db.commit()
    return exec_id


@router.post(
    "/execute",
    response_model=WorkflowExecutionResponse,
    summary="Execute a workflow, data provider, or script",
    description="Execute a workflow or data provider by ID. For data providers, returns options list in result field. Requires platform admin, API key, or access via form/app/integration.",
)
async def execute_workflow(
    request: WorkflowExecutionRequest,
    ctx: Context,
    db: DbSession,
    user: CurrentActiveUser,  # Changed from CurrentSuperuser - auth check below
) -> WorkflowExecutionResponse:
    """Execute a workflow, data provider, or inline script.

    Authorization:
    - Inline code execution requires platform admin
    - Workflow/data provider execution requires one of:
      - Platform admin
      - User has access to a form using this workflow
      - User has access to an app using this workflow
      - Data provider is tied to an integration (any authenticated user)
    """
    from uuid import uuid4
    from src.sdk.context import ExecutionContext as SharedContext, Organization
    from src.services.execution.service import (
        run_workflow,
        run_code,
        WorkflowNotFoundError,
        WorkflowLoadError,
    )
    from src.repositories import AccessDeniedError, WorkflowRepository
    from src.core.org_filter import resolve_target_org

    # Resolve org scope for workflow lookup — follows the same pattern as
    # configs, tables, etc. Superusers can pass org_id to search that org;
    # regular users always use their own org.
    lookup_org_id = resolve_target_org(
        user=user,
        scope=request.org_id,
        default_org_id=ctx.org_id,
    )

    workflow_repo = WorkflowRepository(
        session=db,
        org_id=lookup_org_id,
        user_id=ctx.user.user_id,
        is_superuser=ctx.user.is_superuser,
    )

    # Look up workflow metadata for type checking (needed for data provider handling)
    workflow = None
    if request.workflow_id:
        workflow = await workflow_repo.resolve(request.workflow_id)
        if not workflow:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Workflow '{request.workflow_id}' not found",
            )

    # Authorization check
    if request.code:
        # Inline code execution requires platform admin
        if not ctx.user.is_superuser:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Inline code execution requires platform admin access",
            )
    elif request.workflow_id:
        # Workflow execution - check access via repository cascade scoping
        # resolve() already checked scoping; use can_access with the resolved UUID
        assert workflow is not None  # guaranteed by resolve() + 404 above
        try:
            await workflow_repo.can_access(id=workflow.id)
        except AccessDeniedError:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied to execute this workflow",
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either workflow_id or code must be provided",
        )

    # Validate admin-only overrides (org_id, run_as)
    if (request.org_id or request.run_as) and not ctx.user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="org_id and run_as overrides require platform admin",
        )

    # Resolve run_as user if provided
    exec_user_id = str(ctx.user.user_id)
    exec_user_name = ctx.user.name or ctx.user.email or "Unknown"
    exec_user_email = ctx.user.email or ""
    exec_is_admin = ctx.user.is_superuser

    if request.run_as:
        from src.models.orm.users import User
        run_as_result = await db.execute(
            select(User).where(User.id == UUID(request.run_as))
        )
        run_as_user = run_as_result.scalar_one_or_none()
        if not run_as_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"run_as user '{request.run_as}' not found",
            )
        exec_user_id = str(run_as_user.id)
        exec_user_name = run_as_user.name or run_as_user.email or "Unknown"
        exec_user_email = run_as_user.email or ""
        exec_is_admin = run_as_user.is_superuser
        logger.info(f"Impersonating user: {exec_user_id} ({exec_user_email})")

    # Determine execution org_id
    # Priority order:
    # 0. Explicit org_id override (admin only, checked above)
    # 1. Org-scoped workflow: use workflow's organization_id (enforces workflow isolation)
    # 2. Global workflow: use caller's org context (ctx.org_id or developer context)
    # 3. Inline code: use caller's org context
    if request.org_id:
        execution_org_id = UUID(request.org_id)
        logger.info(f"Using explicit org_id override: {execution_org_id}")
    elif workflow and workflow.organization_id:
        # Org-scoped workflow - execution MUST use workflow's org for data isolation
        execution_org_id = workflow.organization_id
        logger.info(f"Using workflow's organization: {execution_org_id}")
    else:
        # Global workflow or inline code - use caller's org context
        execution_org_id = ctx.org_id
        if ctx.user.is_superuser:
            # Platform admin - developer context overrides default org
            dev_ctx_result = await db.execute(
                select(DeveloperContext).where(DeveloperContext.user_id == ctx.user.user_id)
            )
            dev_ctx = dev_ctx_result.scalar_one_or_none()
            if dev_ctx and dev_ctx.default_org_id:
                execution_org_id = dev_ctx.default_org_id
                logger.info(f"Using developer context org: {execution_org_id}")

    # Scheduled execution: normalize delay_seconds -> scheduled_at and insert row.
    # The deferred_execution_promoter job will publish this row when it matures.
    scheduled_at: datetime | None = request.scheduled_at
    if request.delay_seconds is not None:
        scheduled_at = datetime.now(timezone.utc) + timedelta(seconds=request.delay_seconds)

    if scheduled_at is not None:
        # Schedule-with-code is rejected by the contract validator; workflow must exist.
        assert workflow is not None
        exec_id = await _insert_scheduled_execution(
            db=db,
            workflow_id=workflow.id,
            workflow_name=workflow.name,
            parameters=request.input_data,
            scheduled_at=scheduled_at,
            organization_id=execution_org_id,
            executed_by=UUID(exec_user_id),
            executed_by_name=exec_user_name,
            form_id=UUID(request.form_id) if request.form_id else None,
            api_key_id=None,  # API-key-triggered scheduling not supported in v1
            is_platform_admin=exec_is_admin,
        )
        return WorkflowExecutionResponse(
            execution_id=str(exec_id),
            workflow_id=str(workflow.id),
            workflow_name=workflow.name,
            status=ExecutionStatus.SCHEDULED,
            scheduled_at=scheduled_at,
        )

    # Build shared context for execution
    org = None
    if execution_org_id:
        org = Organization(id=str(execution_org_id), name="", is_active=True)

    logger.info(
        f"Building execution context: org_id={execution_org_id}, user={exec_user_id}, is_superuser={exec_is_admin}, scope={'GLOBAL' if not execution_org_id else str(execution_org_id)}"
    )

    shared_ctx = SharedContext(
        user_id=exec_user_id,
        name=exec_user_name,
        email=exec_user_email,
        scope=str(execution_org_id) if execution_org_id else "GLOBAL",
        organization=org,
        is_platform_admin=exec_is_admin,
        is_function_key=False,
        execution_id=str(uuid4()),
    )

    try:
        if request.code:
            # Execute inline code
            result = await run_code(
                context=shared_ctx,
                code=request.code,
                script_name=request.script_name or "inline_script",
                input_data=request.input_data,
                transient=request.transient,
            )
        elif workflow and workflow.type == "data_provider":
            # Only short-circuit on the sync/transient hot path. A non-transient
            # request (e.g. manual "Execute" from the workflows page) expects a
            # tracked execution row to navigate to — returning a synthetic
            # execution_id would 404 the history detail page.
            if request.transient and workflow.cache_ttl_seconds > 0:
                cached_result = await get_cached_data_provider(
                    str(execution_org_id) if execution_org_id else None,
                    workflow.name,
                    request.input_data,
                )
                if cached_result:
                    return WorkflowExecutionResponse(
                        execution_id=shared_ctx.execution_id,
                        workflow_id=str(workflow.id),
                        workflow_name=workflow.name,
                        status=ExecutionStatus.SUCCESS,
                        result=cached_result.get("data"),
                        duration_ms=0,
                        is_transient=True,
                    )

            # Data providers always run sync (small payloads, no UI poll flow),
            # but honor the caller's transient flag: dropdown-options pass
            # transient=True for the fast path, the manual Execute page passes
            # transient=False and expects a tracked execution row.
            result = await run_workflow(
                context=shared_ctx,
                workflow_id=str(workflow.id),
                input_data=request.input_data,
                transient=request.transient,
                sync=True,
            )
            return WorkflowExecutionResponse(
                execution_id=result.execution_id,
                workflow_id=str(workflow.id),
                workflow_name=workflow.name,
                status=result.status,
                result=result.result,
                is_transient=request.transient,
            )
        elif workflow:
            # Execute workflow by ID
            result = await run_workflow(
                context=shared_ctx,
                workflow_id=str(workflow.id),
                input_data=request.input_data,
                form_id=request.form_id,
                transient=request.transient,
                sync=request.sync or False,
            )
        else:
            # This shouldn't happen due to earlier validation
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Either workflow_id or code must be provided",
            )

        # If the result already has a terminal status (sync mode), mark as transient
        # so the frontend uses the inline result instead of waiting on WebSocket
        if result.status and result.status not in (ExecutionStatus.PENDING, ExecutionStatus.RUNNING):
            result.is_transient = True

        # Publish execution update via WebSocket
        if not request.transient and result.execution_id:
            await publish_execution_update(
                execution_id=result.execution_id,
                status=result.status.value,
                data={
                    "result": result.result,
                    "error": result.error,
                    "duration_ms": result.duration_ms,
                },
            )
            await publish_history_update(
                execution_id=result.execution_id,
                status=result.status.value,
                executed_by=exec_user_id,
                executed_by_name=exec_user_name or exec_user_email or "Unknown",
                workflow_name=result.workflow_name or request.script_name or "inline_script",
                org_id=execution_org_id,
                started_at=result.started_at,
                completed_at=result.completed_at,
                duration_ms=result.duration_ms,
            )

        return result

    except WorkflowNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )
    except WorkflowLoadError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Error executing workflow: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to execute workflow: {type(e).__name__}: {str(e)}",
        )


@router.post(
    "/executions/{execution_id}/cancel",
    summary="Cancel a scheduled execution",
    description=(
        "Cancel a SCHEDULED execution (row not yet promoted to the queue). "
        "Returns 409 if the row is in any other status (including already PENDING). "
        "Cancelling a RUNNING execution is a separate feature and is not handled here."
    ),
)
async def cancel_scheduled_execution(
    execution_id: UUID,
    ctx: Context,
    db: DbSession,
    user: CurrentActiveUser,
) -> dict:
    """Flip a SCHEDULED execution to CANCELLED via a status-guarded UPDATE.

    Authorization:
    - Row must be in the caller's org (unless platform admin).
    - Only the original submitter or a platform admin may cancel.

    Race handling: the UPDATE is guarded on ``status = SCHEDULED``. If another
    caller (promoter, concurrent cancel) has already moved the row, we refetch
    and return 409 with the current status.
    """
    from src.models.orm.executions import Execution

    row = await db.get(Execution, execution_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Execution not found",
        )

    # Org-scoped access: row's org must match caller's org, unless admin.
    if (
        not ctx.user.is_superuser
        and row.organization_id is not None
        and row.organization_id != ctx.org_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    # Non-admin can only cancel their own scheduled rows.
    if not ctx.user.is_superuser and row.executed_by != ctx.user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the submitter or an admin may cancel",
        )

    # Status-guarded UPDATE (wins or loses atomically vs. the promoter).
    from sqlalchemy.engine import CursorResult

    result = cast(
        CursorResult,
        await db.execute(
            update(Execution)
            .where(Execution.id == execution_id)
            .where(Execution.status == ExecutionStatus.SCHEDULED)
            .values(
                status=ExecutionStatus.CANCELLED,
                completed_at=datetime.now(timezone.utc),
            )
        ),
    )
    await db.commit()

    if result.rowcount == 0:
        # Another actor (promoter or concurrent cancel) changed status first.
        await db.refresh(row)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Execution is not Scheduled (current status: {row.status.value})",
        )

    return {
        "execution_id": str(execution_id),
        "status": ExecutionStatus.CANCELLED.value,
    }


@router.post(
    "/validate",
    response_model=WorkflowValidationResponse,
    summary="Validate a workflow file",
    description="Validate a workflow file for syntax errors and decorator issues",
)
async def validate_workflow(
    request: WorkflowValidationRequest,
    user: CurrentActiveUser,
) -> WorkflowValidationResponse:
    """Validate a workflow file for errors."""
    from src.services.workflow_validation import validate_workflow_file

    try:
        result = await validate_workflow_file(
            path=request.path,
            content=request.content,
        )

        logger.info(f"Validation result for {log_safe(request.path)}: valid={result.valid}, issues={len(result.issues)}")
        return result

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid request: {str(e)}",
        )
    except Exception as e:
        logger.error(f"Error validating workflow: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to validate workflow",
        )


@router.post(
    "/register",
    response_model=RegisterWorkflowResponse,
    status_code=201,
    summary="Register a workflow function",
    description="Register a decorated function from an existing .py file as a workflow.",
)
async def register_workflow(
    request: RegisterWorkflowRequest,
    db: DbSession,
    user: CurrentSuperuser,
) -> RegisterWorkflowResponse:
    """Register a workflow function from an existing Python file."""
    import ast
    from uuid import uuid4
    from src.services.file_storage import FileStorageService
    from src.services.file_storage.indexers.workflow import WorkflowIndexer

    service = FileStorageService(db)

    # 1. Verify file exists
    try:
        content_tuple = await service.read_file(request.path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"File not found: {request.path}")

    if not request.path.endswith(".py"):
        raise HTTPException(status_code=400, detail="Path must be a .py file")

    # read_file returns tuple[bytes, None]
    content = content_tuple[0]

    # 2. AST parse and find the function with a decorator
    content_str = content.decode("utf-8", errors="replace")
    try:
        tree = ast.parse(content_str, filename=request.path)
    except SyntaxError as e:
        raise HTTPException(status_code=400, detail=f"Syntax error: {e}")

    # Find the target function
    target_node = None
    target_decorator_type = None
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != request.function_name:
            continue
        for dec in node.decorator_list:
            dec_name = None
            if isinstance(dec, ast.Name):
                dec_name = dec.id
            elif isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name):
                dec_name = dec.func.id
            if dec_name in ("workflow", "tool", "data_provider"):
                target_node = node
                target_decorator_type = dec_name
                break
        if target_node:
            break

    if not target_node:
        raise HTTPException(
            status_code=404,
            detail=f"No decorated function '{request.function_name}' found in {request.path}",
        )

    # 3. Check if already registered (include inactive rows for reactivation)
    existing = await db.execute(
        select(WorkflowORM).where(
            WorkflowORM.path == request.path,
            WorkflowORM.function_name == request.function_name,
        )
    )
    existing_wf = existing.scalar_one_or_none()

    wf_type = "data_provider" if target_decorator_type == "data_provider" else (
        "tool" if target_decorator_type == "tool" else "workflow"
    )

    # Parse organization_id if provided
    org_uuid = UUID(request.organization_id) if request.organization_id else None

    # Validate access_level early so we don't half-register on a typo
    if request.access_level is not None and request.access_level not in ("authenticated", "role_based"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid access_level: '{request.access_level}'. Must be 'authenticated' or 'role_based'",
        )

    # Parse role_ids and verify they exist before any DB mutation
    role_uuids: list[UUID] = []
    if request.role_ids:
        for rid_str in request.role_ids:
            try:
                role_uuids.append(UUID(rid_str))
            except (ValueError, AttributeError):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid role ID: '{rid_str}' (expected a UUID)",
                )
        role_check = await db.execute(
            select(Role.id).where(Role.id.in_(role_uuids))
        )
        found_role_ids = set(role_check.scalars().all())
        missing_roles = [str(rid) for rid in role_uuids if rid not in found_role_ids]
        if missing_roles:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Role(s) not found: {', '.join(missing_roles)}",
            )

    if existing_wf and existing_wf.is_active:
        raise HTTPException(status_code=409, detail="Workflow already registered")
    elif existing_wf and not existing_wf.is_active:
        # Reactivate the existing inactive workflow, preserving its UUID
        workflow_id = existing_wf.id
        existing_wf.is_active = True
        existing_wf.is_orphaned = False
        existing_wf.type = wf_type
        existing_wf.organization_id = org_uuid
        if request.access_level is not None:
            existing_wf.access_level = request.access_level
        existing_wf.updated_at = datetime.now(timezone.utc)
        await db.flush()
    else:
        # 4. Create minimal DB record
        workflow_id = uuid4()
        new_wf = WorkflowORM(
            id=workflow_id,
            name=request.function_name,
            function_name=request.function_name,
            path=request.path,
            type=wf_type,
            is_active=True,
            organization_id=org_uuid,
            access_level=request.access_level if request.access_level is not None else "role_based",
        )
        db.add(new_wf)
        await db.flush()

    # Apply role assignments. Replaces any pre-existing rows when reactivating
    # an inactive workflow, so the caller's role list is authoritative.
    if request.role_ids is not None:
        await db.execute(
            delete(WorkflowRole).where(WorkflowRole.workflow_id == workflow_id)
        )
        now = datetime.now(timezone.utc)
        for role_uuid in role_uuids:
            db.add(
                WorkflowRole(
                    workflow_id=workflow_id,
                    role_id=role_uuid,
                    assigned_by=user.email,
                    assigned_at=now,
                )
            )
        await db.flush()

    # 5. Run indexer to enrich with content-derived fields
    indexer = WorkflowIndexer(db)
    await indexer.index_python_file(request.path, content)

    # 6. Re-fetch enriched record
    result = await db.execute(
        select(WorkflowORM).where(WorkflowORM.id == workflow_id)
    )
    workflow = result.scalar_one()

    # Commit before refreshing MCP tools. refresh_workflow_tools() opens its
    # own session via get_db_context(), and at READ COMMITTED it cannot see
    # this request's still-uncommitted INSERT — leaving the freshly-registered
    # workflow missing from FastMCP's in-memory registry until the next API
    # restart. update_workflow / delete_workflow already follow this pattern.
    await db.commit()

    try:
        from src.services.mcp_server.server import refresh_workflow_tools
        await refresh_workflow_tools()
    except Exception as e:
        logger.warning(f"Failed to refresh MCP workflow tools: {e}")

    return RegisterWorkflowResponse(
        id=str(workflow.id),
        name=workflow.name,
        function_name=workflow.function_name,
        path=workflow.path,
        type=workflow.type,
        description=workflow.description,
        organization_id=str(workflow.organization_id) if workflow.organization_id else None,
    )


@router.patch(
    "/{workflow_id}",
    response_model=WorkflowMetadata,
    summary="Update a workflow",
    description="Update editable workflow properties like organization scope (Platform admin only)",
)
async def update_workflow(
    workflow_id: UUID,
    request: WorkflowUpdateRequest,
    user: CurrentSuperuser,
    db: DbSession,
) -> WorkflowMetadata:
    """Update a workflow's editable properties.

    Supports updating:
    - organization_id: Set to null for global scope, or an org UUID for org-scoped
    - access_level: 'authenticated' or 'role_based'
    - clear_roles: If true, clear all role assignments
    - display_name: User-facing display name (can be set to null to use code name)
    - timeout_seconds: Max execution time (0-86400 seconds, where 0 disables the timeout)
    - execution_mode: 'sync' or 'async'
    - time_saved: Minutes saved per execution (for ROI reporting)
    - value: Flexible value unit per execution
    - tool_description: Description for AI tool selection (can be set to null)
    - cache_ttl_seconds: Cache TTL for data providers (0-86400 seconds)
    - endpoint_enabled: Whether workflow is exposed as HTTP endpoint
    - allowed_methods: Allowed HTTP methods when endpoint is enabled

    Requires platform admin access.
    """
    try:
        # Find the workflow
        result = await db.execute(
            select(WorkflowORM).where(WorkflowORM.id == workflow_id)
        )
        workflow = result.scalar_one_or_none()

        if not workflow:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Workflow with ID '{workflow_id}' not found",
            )

        # Update organization_id - use model_fields_set to distinguish "not provided" from "explicitly null"
        if "organization_id" in request.model_fields_set:
            if request.organization_id is not None:
                # Validate organization exists if not setting to global
                from src.models.orm.organizations import Organization
                org_result = await db.execute(
                    select(Organization).where(Organization.id == UUID(request.organization_id))
                )
                if not org_result.scalar_one_or_none():
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Organization with ID '{request.organization_id}' not found",
                    )
                workflow.organization_id = UUID(request.organization_id)
            else:
                # Explicitly set to global scope
                workflow.organization_id = None

        # Update access_level if provided
        if request.access_level is not None:
            if request.access_level not in ("authenticated", "role_based"):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid access_level: '{request.access_level}'. Must be 'authenticated' or 'role_based'",
                )
            workflow.access_level = request.access_level

        # Role assignment edits. ``role_ids`` (when explicitly provided) bulk-replaces
        # the assignment set; ``clear_roles`` is the legacy single-purpose flag and
        # wipes assignments when ``role_ids`` was not supplied. If both are provided,
        # ``role_ids`` wins because it carries the more specific intent.
        if request.role_ids is not None:
            target_role_uuids: list[UUID] = []
            for rid_str in request.role_ids:
                try:
                    target_role_uuids.append(UUID(rid_str))
                except (ValueError, AttributeError):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Invalid role ID: '{rid_str}' (expected a UUID)",
                    )

            if target_role_uuids:
                role_check = await db.execute(
                    select(Role.id).where(Role.id.in_(target_role_uuids))
                )
                found_role_ids = set(role_check.scalars().all())
                missing_roles = [str(rid) for rid in target_role_uuids if rid not in found_role_ids]
                if missing_roles:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Role(s) not found: {', '.join(missing_roles)}",
                    )

            await db.execute(
                delete(WorkflowRole).where(WorkflowRole.workflow_id == workflow_id)
            )
            now = datetime.now(timezone.utc)
            for role_uuid in target_role_uuids:
                db.add(
                    WorkflowRole(
                        workflow_id=workflow_id,
                        role_id=role_uuid,
                        assigned_by=user.email,
                        assigned_at=now,
                    )
                )
            logger.info(
                f"Replaced role assignments for workflow '{log_safe(workflow.name)}' "
                f"({len(target_role_uuids)} role(s))"
            )
        elif request.clear_roles:
            await db.execute(
                delete(WorkflowRole).where(WorkflowRole.workflow_id == workflow_id)
            )
            # Also set to role_based access level (effectively no access)
            workflow.access_level = "role_based"
            logger.info(f"Cleared all role assignments for workflow '{log_safe(workflow.name)}'")

        # Update display_name if provided
        if "display_name" in request.model_fields_set:
            workflow.display_name = request.display_name

        # Update description if provided
        if "description" in request.model_fields_set:
            workflow.description = request.description

        # Update category if provided (category is non-nullable, fallback to "General")
        if "category" in request.model_fields_set:
            workflow.category = request.category or "General"

        # Update timeout_seconds if provided
        if request.timeout_seconds is not None:
            if request.timeout_seconds < 0 or request.timeout_seconds > 86400:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="timeout_seconds must be between 0 and 86400",
                )
            workflow.timeout_seconds = request.timeout_seconds

        # Update execution_mode if provided
        if request.execution_mode is not None:
            if request.execution_mode not in ("sync", "async"):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="execution_mode must be 'sync' or 'async'",
                )
            workflow.execution_mode = request.execution_mode

        # Update time_saved if provided
        if request.time_saved is not None:
            if request.time_saved < 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="time_saved must be non-negative",
                )
            workflow.time_saved = request.time_saved

        # Update value if provided
        if request.value is not None:
            if request.value < 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="value must be non-negative",
                )
            workflow.value = request.value

        # Update tool_description if provided
        if "tool_description" in request.model_fields_set:
            workflow.tool_description = request.tool_description

        # Update cache_ttl_seconds if provided
        if request.cache_ttl_seconds is not None:
            if request.cache_ttl_seconds < 0 or request.cache_ttl_seconds > 86400:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="cache_ttl_seconds must be between 0 and 86400",
                )
            workflow.cache_ttl_seconds = request.cache_ttl_seconds

        # Update endpoint_enabled if provided
        if request.endpoint_enabled is not None:
            workflow.endpoint_enabled = request.endpoint_enabled

        # Update allowed_methods if provided
        if request.allowed_methods is not None:
            valid_methods = {"GET", "POST", "PUT", "PATCH", "DELETE"}
            for method in request.allowed_methods:
                if method.upper() not in valid_methods:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Invalid HTTP method: {method}. Must be one of: {', '.join(valid_methods)}",
                    )
            workflow.allowed_methods = [m.upper() for m in request.allowed_methods]

        # Update public_endpoint if provided
        if request.public_endpoint is not None:
            workflow.public_endpoint = request.public_endpoint

        # Update disable_global_key if provided
        if request.disable_global_key is not None:
            workflow.disable_global_key = request.disable_global_key

        # Update tags if provided
        if request.tags is not None:
            workflow.tags = request.tags

        await db.commit()
        await db.refresh(workflow)

        # Invalidate Redis caches so changes take effect immediately
        try:
            from src.core.redis_client import get_redis_client
            redis_client = get_redis_client()
            await redis_client.invalidate_endpoint_workflow_cache(str(workflow.id))
            await redis_client.invalidate_workflow_metadata_cache(str(workflow.id))
        except Exception as e:
            logger.warning(f"Failed to invalidate caches for workflow {workflow.name}: {e}")

        # Refresh MCP tool registry so updated signatures appear immediately
        try:
            from src.services.mcp_server.server import refresh_workflow_tools
            await refresh_workflow_tools()
        except Exception as e:
            logger.warning(f"Failed to refresh MCP workflow tools: {e}")

        logger.info(f"Updated workflow '{log_safe(workflow.name)}' organization_id={log_safe(workflow.organization_id)}, access_level={log_safe(workflow.access_level)}")
        return _convert_workflow_orm_to_schema(workflow)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating workflow: {str(e)}", exc_info=True)
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update workflow",
        )


# =============================================================================
# Orphan Management Endpoints
# =============================================================================


@router.get(
    "/orphaned",
    response_model=OrphanedWorkflowsResponse,
    summary="List orphaned workflows",
    description="Get all orphaned workflows with their references",
)
async def list_orphaned_workflows(
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
) -> OrphanedWorkflowsResponse:
    """
    List all orphaned workflows.

    Orphaned workflows are workflows whose backing file has been deleted or
    modified to no longer contain the workflow function.

    Returns:
        OrphanedWorkflowsResponse with list of orphaned workflows
    """
    from src.services.workflow_orphan import WorkflowOrphanService

    try:
        orphan_service = WorkflowOrphanService(db)
        orphans = await orphan_service.get_orphaned_workflows()

        return OrphanedWorkflowsResponse(
            workflows=[
                OrphanedWorkflowInfo(
                    id=o.id,
                    name=o.name,
                    function_name=o.function_name,
                    last_path=o.last_path,
                    code=o.code,
                    used_by=[
                        WorkflowReference(
                            type=r.type,
                            id=r.id,
                            name=r.name,
                        )
                        for r in o.used_by
                    ],
                    orphaned_at=o.orphaned_at,
                )
                for o in orphans
            ]
        )

    except Exception as e:
        logger.error(f"Error listing orphaned workflows: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list orphaned workflows",
        )


@router.get(
    "/{workflow_id}/compatible-replacements",
    response_model=CompatibleReplacementsResponse,
    summary="Get compatible replacements",
    description="Get list of files/functions that could replace an orphaned workflow",
)
async def get_compatible_replacements(
    workflow_id: UUID,
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
) -> CompatibleReplacementsResponse:
    """
    Get compatible replacements for an orphaned workflow.

    Finds functions with compatible signatures that could replace
    the orphaned workflow.

    Args:
        workflow_id: UUID of the orphaned workflow

    Returns:
        CompatibleReplacementsResponse with list of replacements
    """
    from src.services.workflow_orphan import WorkflowOrphanService

    try:
        orphan_service = WorkflowOrphanService(db)
        replacements = await orphan_service.get_compatible_replacements(workflow_id)

        return CompatibleReplacementsResponse(
            replacements=[
                CompatibleReplacement(
                    path=r.path,
                    function_name=r.function_name,
                    signature=r.signature,
                    compatibility=r.compatibility,
                )
                for r in replacements
                if r.compatibility != "incompatible"
            ]
        )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Error getting compatible replacements: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get compatible replacements",
        )


@router.post(
    "/{workflow_id}/replace",
    response_model=ReplaceWorkflowResponse,
    summary="Replace orphaned workflow",
    description="Replace an orphaned workflow with content from an existing file",
)
async def replace_workflow(
    workflow_id: UUID,
    request: ReplaceWorkflowRequest,
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
) -> ReplaceWorkflowResponse:
    """
    Replace an orphaned workflow with content from an existing file.

    Links the orphaned workflow to an existing function in another file,
    updating its path, code, and clearing the orphaned flag.

    Args:
        workflow_id: UUID of the orphaned workflow
        request: Source file and function details

    Returns:
        ReplaceWorkflowResponse with result
    """
    from src.services.workflow_orphan import WorkflowOrphanService

    try:
        orphan_service = WorkflowOrphanService(db)
        workflow = await orphan_service.replace_workflow(
            workflow_id=workflow_id,
            source_path=request.source_path,
            function_name=request.function_name,
            allow_type_change=request.allow_type_change,
        )

        logger.info(
            f"Replaced orphaned workflow {log_safe(workflow_id)} with "
            f"{log_safe(request.source_path)}::{log_safe(request.function_name)}"
        )

        return ReplaceWorkflowResponse(
            success=True,
            workflow_id=str(workflow.id),
            new_path=workflow.path,
        )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Error replacing workflow: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to replace workflow",
        )


@router.post(
    "/{workflow_id}/recreate",
    response_model=RecreateFileResponse,
    summary="Recreate file from orphaned workflow",
    description="Recreate the file from the orphaned workflow's stored code",
)
async def recreate_workflow_file(
    workflow_id: UUID,
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
) -> RecreateFileResponse:
    """
    Recreate the file from orphaned workflow's stored code.

    Writes the workflow's code snapshot back to the filesystem at its
    last known path, then clears the orphaned flag.

    Args:
        workflow_id: UUID of the orphaned workflow

    Returns:
        RecreateFileResponse with result
    """
    from src.services.workflow_orphan import WorkflowOrphanService
    from src.services.file_storage import FileStorageService

    try:
        orphan_service = WorkflowOrphanService(db)

        # Get the workflow and mark as not orphaned
        workflow = await orphan_service.recreate_file(workflow_id)

        # Load code via Redis→S3 cache chain
        from src.core.module_cache import get_module
        cached = await get_module(workflow.path)
        code_content = cached["content"] if cached else None

        if not code_content:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Workflow has no stored code to recreate",
            )
        file_storage = FileStorageService(db)
        await file_storage.write_file(
            path=workflow.path,
            content=code_content.encode("utf-8"),
            updated_by=user.email,
        )

        logger.info(f"Recreated file for workflow {log_safe(workflow_id)} at {log_safe(workflow.path)}")

        return RecreateFileResponse(
            success=True,
            workflow_id=str(workflow.id),
            path=workflow.path,
        )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Error recreating workflow file: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to recreate workflow file",
        )


@router.post(
    "/{workflow_id}/deactivate",
    response_model=DeactivateWorkflowResponse,
    summary="Deactivate orphaned workflow",
    description="Deactivate an orphaned workflow",
)
async def deactivate_workflow(
    workflow_id: UUID,
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
) -> DeactivateWorkflowResponse:
    """
    Deactivate an orphaned workflow.

    Marks the workflow as inactive. Forms and apps using it will need
    to be updated to use a different workflow.

    Args:
        workflow_id: UUID of the workflow

    Returns:
        DeactivateWorkflowResponse with result
    """
    from src.services.workflow_orphan import WorkflowOrphanService

    try:
        orphan_service = WorkflowOrphanService(db)
        workflow, ref_count = await orphan_service.deactivate_workflow(workflow_id)

        warning = None
        if ref_count > 0:
            warning = f"{ref_count} {'form/app still references' if ref_count == 1 else 'forms/apps still reference'} this workflow"

        logger.info(f"Deactivated workflow {log_safe(workflow_id)} (refs: {log_safe(ref_count)})")

        return DeactivateWorkflowResponse(
            success=True,
            workflow_id=str(workflow.id),
            warning=warning,
        )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Error deactivating workflow: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to deactivate workflow",
        )


# =============================================================================
# Workflow Role Endpoints
# =============================================================================


@router.get(
    "/{workflow_id}/roles",
    response_model=WorkflowRolesResponse,
    summary="Get workflow roles",
    description="Get all roles assigned to a workflow (Platform admin only)",
)
async def get_workflow_roles(
    workflow_id: UUID,
    user: CurrentSuperuser,
    db: DbSession,
) -> WorkflowRolesResponse:
    """Get all roles assigned to a workflow.

    Args:
        workflow_id: UUID of the workflow

    Returns:
        WorkflowRolesResponse with list of role IDs
    """
    # Verify workflow exists
    result = await db.execute(
        select(WorkflowORM.id).where(WorkflowORM.id == workflow_id)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow with ID '{workflow_id}' not found",
        )

    # Get role IDs assigned to this workflow
    result = await db.execute(
        select(WorkflowRole.role_id).where(WorkflowRole.workflow_id == workflow_id)
    )
    role_ids = [str(rid) for rid in result.scalars().all()]

    return WorkflowRolesResponse(role_ids=role_ids)


@router.post(
    "/{workflow_id}/roles",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Assign roles to workflow",
    description="Assign roles to a workflow (batch operation, Platform admin only)",
)
async def assign_roles_to_workflow(
    workflow_id: UUID,
    request: AssignRolesToWorkflowRequest,
    user: CurrentSuperuser,
    db: DbSession,
) -> None:
    """Assign roles to a workflow.

    This is a batch operation that adds the specified roles to the workflow.
    Roles that are already assigned will be skipped.

    Args:
        workflow_id: UUID of the workflow
        request: Request containing list of role IDs to assign
    """
    # Verify workflow exists
    result = await db.execute(
        select(WorkflowORM.id).where(WorkflowORM.id == workflow_id)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow with ID '{workflow_id}' not found",
        )

    now = datetime.now(timezone.utc)

    for role_id_str in request.role_ids:
        role_uuid = UUID(role_id_str)

        # Verify role exists
        role_result = await db.execute(
            select(Role.id).where(Role.id == role_uuid)
        )
        if not role_result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Role with ID '{role_id_str}' not found",
            )

        # Check if already assigned
        existing = await db.execute(
            select(WorkflowRole).where(
                WorkflowRole.workflow_id == workflow_id,
                WorkflowRole.role_id == role_uuid,
            )
        )
        if existing.scalar_one_or_none():
            continue

        # Create new assignment
        workflow_role = WorkflowRole(
            workflow_id=workflow_id,
            role_id=role_uuid,
            assigned_by=user.email,
            assigned_at=now,
        )
        db.add(workflow_role)

    await db.flush()
    logger.info(f"Assigned roles to workflow {log_safe(workflow_id)}")


@router.delete(
    "/{workflow_id}/roles/{role_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove role from workflow",
    description="Remove a role from a workflow (Platform admin only)",
)
async def remove_role_from_workflow(
    workflow_id: UUID,
    role_id: UUID,
    user: CurrentSuperuser,
    db: DbSession,
) -> None:
    """Remove a role from a workflow.

    Args:
        workflow_id: UUID of the workflow
        role_id: UUID of the role to remove
    """
    result = await db.execute(
        delete(WorkflowRole).where(
            WorkflowRole.workflow_id == workflow_id,
            WorkflowRole.role_id == role_id,
        )
    )

    if result.rowcount == 0:  # type: ignore[union-attr]
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow-role assignment not found",
        )

    logger.info(f"Removed role {log_safe(role_id)} from workflow {log_safe(workflow_id)}")


@router.delete(
    "/{workflow_id}",
    summary="Delete a workflow",
    description="Delete a workflow by removing its function from the source file. "
                "Returns 409 with deactivation details if the workflow has history or dependencies.",
    responses={
        200: {"description": "Workflow deleted successfully"},
        404: {"description": "Workflow not found"},
        409: {"description": "Workflow has dependencies or history, confirmation required"},
    },
    response_model=None,
)
async def delete_workflow(
    workflow_id: UUID,
    user: CurrentSuperuser,
    db: DbSession,
    request: DeleteWorkflowRequest | None = None,
) -> dict[str, str] | JSONResponse:
    """Delete a workflow by removing its function from the workspace source file.

    Two-phase flow (same pattern as the code editor's deactivation protection):
    1. First call (no flags): checks for dependencies/history and returns 409
       with PendingDeactivation details if any are found.
    2. Second call (with force_deactivation=True or replacements): performs the
       actual deletion — either deleting the file (single-function) or removing
       the function block (multi-function file).
    """
    from fastapi.responses import JSONResponse
    from src.services.file_storage.deactivation import DeactivationProtectionService
    from src.services.file_storage.code_surgery import remove_function_from_source

    if request is None:
        request = DeleteWorkflowRequest()

    # 1. Find the workflow
    result = await db.execute(
        select(WorkflowORM).where(WorkflowORM.id == workflow_id)
    )
    workflow = result.scalar_one_or_none()

    if not workflow:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow with ID '{workflow_id}' not found",
        )

    if not workflow.path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workflow has no source file path — cannot delete",
        )

    # 2. Run deactivation check (unless force or replacements provided)
    if not request.force_deactivation and not request.replacements:
        deactivation_svc = DeactivationProtectionService(db)

        # We're removing this one function, so the "new" function names are
        # all existing functions at this path minus the target
        from src.models import Workflow as WfORM
        path_wf_result = await db.execute(
            select(WfORM).where(
                WfORM.path == workflow.path,
                WfORM.is_active == True,  # noqa: E712
            )
        )
        path_workflows = list(path_wf_result.scalars().all())
        remaining_names = {
            wf.function_name for wf in path_workflows
            if wf.id != workflow_id
        }

        # Build decorator info for remaining functions (for replacement suggestions)
        new_decorator_info: dict[str, tuple[str, str]] = {}
        for wf in path_workflows:
            if wf.id != workflow_id:
                new_decorator_info[wf.function_name] = (
                    wf.type or "workflow",
                    wf.name,
                )

        pending, replacements_available = await deactivation_svc.detect_pending_deactivations(
            path=workflow.path,
            new_function_names=remaining_names,
            new_decorator_info=new_decorator_info,
        )

        # Only return 409 when there are actual entity references (affected_entities).
        # Execution history alone is not a conflict — it's linked by workflow_name
        # and doesn't break when the record is deactivated.
        conflicted = [pd for pd in pending if pd.affected_entities]
        if conflicted:
            from src.models.contracts.editor import (
                PendingDeactivation,
                AvailableReplacement,
                AffectedEntity,
            )

            conflict_response = {
                "reason": "workflows_would_deactivate",
                "message": f"Workflow '{workflow.name}' has dependencies that need resolution.",
                "pending_deactivations": [
                    PendingDeactivation(
                        id=pd.id,
                        name=pd.name,
                        function_name=pd.function_name,
                        path=pd.path,
                        description=pd.description,
                        decorator_type=pd.decorator_type,
                        has_executions=pd.has_executions,
                        last_execution_at=pd.last_execution_at,
                        endpoint_enabled=pd.endpoint_enabled,
                        affected_entities=[
                            AffectedEntity(**e) for e in pd.affected_entities
                        ],
                    ).model_dump()
                    for pd in conflicted
                ],
                "available_replacements": [
                    AvailableReplacement(
                        function_name=r.function_name,
                        name=r.name,
                        decorator_type=r.decorator_type,
                        similarity_score=r.similarity_score,
                    ).model_dump()
                    for r in replacements_available
                ],
            }
            return JSONResponse(status_code=409, content=conflict_response)

    # 3. Apply replacements if provided
    if request.replacements:
        deactivation_svc = DeactivationProtectionService(db)
        await deactivation_svc.apply_workflow_replacements(request.replacements)

    # 4. Perform the actual file surgery
    from src.services.file_storage import FileStorageService

    file_svc = FileStorageService(db)

    # Read the current file content from storage (the authoritative version)
    try:
        content_bytes, _ = await file_svc.read_file(workflow.path)
        source_content = content_bytes.decode("utf-8", errors="replace")
    except FileNotFoundError:
        # File already gone — just deactivate the workflow record
        workflow.is_active = False
        await db.commit()
        return {"status": "deleted", "detail": "Source file not found, workflow deactivated"}

    # Determine: single-function file or multi-function file
    new_source = remove_function_from_source(source_content, workflow.function_name)

    if new_source is None:
        # Only function in file — delete the entire file
        await file_svc.delete_file(workflow.path)
        logger.info(f"Deleted file {workflow.path} (contained only workflow '{workflow.name}')")
    else:
        # Multi-function file — write back without the removed function
        await file_svc.write_file(
            path=workflow.path,
            content=new_source.encode("utf-8"),
            force_deactivation=True,
        )
        logger.info(f"Removed function '{workflow.function_name}' from {workflow.path}")

    await db.commit()

    # Refresh MCP tool registry so deleted tools disappear immediately
    try:
        from src.services.mcp_server.server import refresh_workflow_tools
        await refresh_workflow_tools()
    except Exception as e:
        logger.warning(f"Failed to refresh MCP workflow tools: {e}")

    return {"status": "deleted", "detail": f"Workflow '{workflow.name}' has been removed"}
