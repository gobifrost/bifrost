"""Canonical workflow-tool launch boundary for agent runtimes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import select

from shared.scope_resolver import shared_read_organization
from src.models.orm.agents import Agent, AgentTool
from src.models.orm.workflows import Workflow
from src.services.authorization.enforce import Caller, require_permission, user_reach
from src.services.authorization.callers import CallerUnavailable, refresh_caller

if TYPE_CHECKING:
    from src.models.contracts.executions import WorkflowExecutionResponse


class AgentWorkflowAuthorizationError(CallerUnavailable):
    """The caller can no longer launch an agent workflow tool."""


def workflow_failure_receipt(response: "WorkflowExecutionResponse") -> dict[str, Any]:
    """Retain returned execution evidence without inferring unreported effects.

    Workflow result/details may carry failure phase, completed effects, uncertain
    effects, and verification receipts. A failed status must not discard them.
    Logs and runtime variables are intentionally outside the tool result contract.
    """
    return {
        "execution_id": response.execution_id,
        "status": response.status.value,
        "duration_ms": response.duration_ms,
        "error": response.error or f"Tool execution failed with status: {response.status.value}",
        "error_type": response.error_type,
        "result": response.result,
        "details": response.details,
    }


async def execute_agent_workflow_tool(
    *,
    agent_id: UUID | str,
    workflow_id: UUID | str,
    parameters: dict[str, Any],
    caller: Caller,
    execution_id: str | None = None,
    artifact_workspace_id: str | None = None,
    sync: bool = True,
) -> "WorkflowExecutionResponse":
    """Authorize and enqueue one explicitly bound workflow tool.

    The parent Agent is the resource gate. A workflow role ACL is deliberately
    not consulted for a tool already bound to that Agent.
    """
    from src.services.agent_write_policy import can_read_agent
    from src.services.execution.service import execute_tool
    from src.services.solution_scope import check_inbound_allowed, solution_allows_global

    try:
        agent_uuid = UUID(str(agent_id))
        workflow_uuid = UUID(str(workflow_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise AgentWorkflowAuthorizationError("Workflow tool identity is invalid") from exc

    from shared.run_lineage import principal_lineage
    from src.core.database import get_session_factory

    async with get_session_factory()() as db:
        refreshed = await refresh_caller(db, caller)
        row = (
            await db.execute(
                select(Agent, Workflow)
                .join(AgentTool, AgentTool.agent_id == Agent.id)
                .join(Workflow, Workflow.id == AgentTool.workflow_id)
                .where(Agent.id == agent_uuid)
                .where(Workflow.id == workflow_uuid)
                .where(Agent.is_active.is_(True))
                .where(Workflow.is_active.is_(True))
                .where(Workflow.type == "tool")
            )
        ).one_or_none()
        if row is None:
            raise AgentWorkflowAuthorizationError("Workflow tool is no longer available")
        agent, workflow = row

        selected_organization_id = (
            refreshed.principal.runtime_authority.organization_id
            if refreshed.principal.runtime_authority is not None
            else refreshed.ctx.home_organization_id
        )
        agent_permission_org = shared_read_organization(
            refreshed,
            agent.organization_id,
            selected_organization_id=selected_organization_id,
        )
        if not user_reach(refreshed).covers(agent.organization_id):
            raise AgentWorkflowAuthorizationError("Agent is outside the caller's organization reach")
        if not await can_read_agent(
            db,
            refreshed,
            agent,
            selected_organization_id=selected_organization_id,
        ):
            raise AgentWorkflowAuthorizationError("Agent is no longer available")
        require_permission(refreshed, "agents.execute", agent_permission_org)

        workflow_permission_org = shared_read_organization(
            refreshed,
            workflow.organization_id,
            selected_organization_id=selected_organization_id,
        )
        if not user_reach(refreshed).covers(workflow.organization_id):
            raise AgentWorkflowAuthorizationError("Workflow is outside the caller's organization reach")
        require_permission(refreshed, "workflows.execute", workflow_permission_org)

        caller_solution_id = agent.solution_id
        if workflow.solution_id is not None:
            if not await check_inbound_allowed(db, workflow.solution_id, caller_solution_id):
                raise AgentWorkflowAuthorizationError("Workflow tool is not available to this Solution")
        elif caller_solution_id is not None and not await solution_allows_global(db, caller_solution_id):
            raise AgentWorkflowAuthorizationError("Workflow tool is not available to this Solution")

        response = await execute_tool(
            workflow_id=str(workflow.id),
            workflow_name=workflow.name,
            parameters=parameters,
            user_id=str(refreshed.principal.user_id),
            user_email=refreshed.principal.email,
            user_name=refreshed.principal.name,
            org_id=None if workflow_permission_org is None else str(workflow_permission_org),
            is_platform_admin=refreshed.principal.is_superuser,
            is_agent=True,
            execution_id=execution_id,
            artifact_workspace_id=artifact_workspace_id,
            sync=sync,
            lineage=await principal_lineage(db, refreshed.principal),
        )
        await db.commit()
        return response
