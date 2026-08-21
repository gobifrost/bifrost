"""
Tools Router

Unified endpoint for listing all available tools (system + workflow).
System tools are auto-discovered from the tool registry.
"""

import logging
from typing import Literal

from fastapi import APIRouter, Query
from sqlalchemy import select

from src.core.auth import CurrentActiveUser
from src.core.db_deps import DbSession
from src.core.org_filter import OrgFilterType
from src.models.contracts.agents import ToolInfo, ToolsResponse
from src.models.orm import Organization
from src.repositories.workflows import WorkflowRepository
from src.services.authorization import AuthorizationBoundaryKind, CurrentAuthorizationContext

from src.services.mcp_server.server import get_system_tools as get_system_tools_from_server

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tools", tags=["Tools"])


# =============================================================================
# System Tools (Auto-generated from Registry)
# =============================================================================


def get_system_tools(*, include_hidden: bool = False) -> list[ToolInfo]:
    """
    Get the list of system tools from the server module.

    Tools are defined in each tool module's TOOLS list. Hidden capabilities
    remain executable but are omitted from the normal author-facing picker.
    """
    return [
        ToolInfo(
            id=tool["id"],
            name=tool["name"],
            description=tool["description"],
            type="system",
        )
        for tool in get_system_tools_from_server()
        if include_hidden or not tool.get("hidden", False)
    ]


def get_system_tool_ids(*, include_hidden: bool = False) -> list[str]:
    """Get list of all system tool IDs."""
    return [tool.id for tool in get_system_tools(include_hidden=include_hidden)]


# =============================================================================
# Tools Endpoint
# =============================================================================


@router.get("")
async def list_tools(
    db: DbSession,
    user: CurrentActiveUser,
    authorization: CurrentAuthorizationContext,
    type: Literal["system", "workflow"] | None = Query(
        default=None,
        description="Filter by tool type: 'system' for built-in tools, 'workflow' for user workflows",
    ),
    scope: str | None = Query(
        default=None,
        description="Filter scope for workflows: omit for all, 'global' for global only, or org UUID",
    ),
    include_inactive: bool = Query(
        default=False,
        description="Include deactivated workflows (for agent editor to show orphaned refs)",
    ),
) -> ToolsResponse:
    """
    List all available tools.

    Returns both system tools (built-in platform tools) and workflow tools
    (user workflows with is_tool=True). Use the `type` parameter to filter.

    System tools are always available. Workflow tools follow organization scoping.
    """
    tools: list[ToolInfo] = []

    # Add system tools (unless filtering to workflow only)
    if type is None or type == "system":
        tools.extend(get_system_tools())

    # Add workflow tools (unless filtering to system only)
    if type is None or type == "workflow":
        authorization.require("workflows.read")
        boundary = authorization.selected_boundary
        if boundary.kind is AuthorizationBoundaryKind.MANAGED_ORGANIZATIONS:
            customer_org_ids = (
                (
                    await db.execute(
                        select(Organization.id)
                        .where(
                            Organization.is_active.is_(True),
                            Organization.is_provider.is_(False),
                        )
                        .order_by(Organization.name)
                    )
                )
                .scalars()
                .all()
            )
            workflows = []
            seen_workflow_ids: set[str] = set()
            for customer_org_id in customer_org_ids:
                workflow_repo = WorkflowRepository(
                    db,
                    org_id=customer_org_id,
                    user_id=user.user_id,
                    bypass_resource_roles=authorization.has_capability("platform.superuser"),
                    is_external=user.is_external,
                )
                for workflow in await workflow_repo.list_tools_for_filter(
                    OrgFilterType.ORG_PLUS_GLOBAL,
                    customer_org_id,
                    active_only=not include_inactive,
                ):
                    workflow_id = str(workflow.id)
                    if workflow_id in seen_workflow_ids:
                        continue
                    seen_workflow_ids.add(workflow_id)
                    workflows.append(workflow)
        elif boundary.kind is AuthorizationBoundaryKind.PLATFORM:
            if scope not in (None, "", "global"):
                # Invalid/conflicting legacy scope - keep the historical
                # "system tools only" behavior for malformed workflow filters.
                return ToolsResponse(tools=tools)
            filter_type = OrgFilterType.GLOBAL_ONLY
            filter_org_id = None
            workflow_repo = WorkflowRepository(
                db,
                org_id=None,
                user_id=user.user_id,
                bypass_resource_roles=authorization.has_capability("platform.superuser"),
                is_external=user.is_external,
            )
            workflows = await workflow_repo.list_tools_for_filter(
                filter_type,
                filter_org_id,
                active_only=not include_inactive,
            )
        elif scope not in (None, "", str(boundary.organization_id)):
            return ToolsResponse(tools=tools)
        else:
            filter_type = OrgFilterType.ORG_PLUS_GLOBAL
            filter_org_id = boundary.organization_id
            workflow_repo = WorkflowRepository(
                db,
                org_id=boundary.organization_id,
                user_id=user.user_id,
                bypass_resource_roles=authorization.has_capability("platform.superuser"),
                is_external=user.is_external,
            )
            workflows = await workflow_repo.list_tools_for_filter(
                filter_type,
                filter_org_id,
                active_only=not include_inactive,
            )

        for workflow in workflows:
            tools.append(
                ToolInfo(
                    id=str(workflow.id),
                    name=workflow.name,
                    description=workflow.tool_description or workflow.description or "",
                    type="workflow",
                    category=workflow.category,
                    default_enabled_for_coding_agent=False,
                    is_active=workflow.is_active,
                    organization_id=str(workflow.organization_id) if workflow.organization_id else None,
                    organization_name=workflow.organization.name if workflow.organization else None,
                )
            )

    return ToolsResponse(tools=tools)


@router.get("/system")
async def list_system_tools_endpoint(
    user: CurrentActiveUser,
) -> ToolsResponse:
    """
    List system tools only.

    Convenience endpoint that returns only built-in platform tools.
    Equivalent to GET /api/tools?type=system
    """
    return ToolsResponse(tools=get_system_tools())
