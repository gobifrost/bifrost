"""Shared execution boundary for workflow-backed agent tools."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from src.models.contracts.executions import WorkflowExecutionResponse


@dataclass(frozen=True)
class AgentWorkflowCaller:
    """Caller identity propagated into an agent-originated workflow run."""

    user_id: str
    email: str
    name: str
    is_platform_admin: bool = False


async def execute_agent_workflow_tool(
    *,
    workflow_id: UUID | str,
    workflow_name: str,
    parameters: dict[str, Any],
    caller: AgentWorkflowCaller,
    organization_id: UUID | str | None,
    execution_id: str | None = None,
    artifact_workspace_id: str | None = None,
    sync: bool = True,
) -> WorkflowExecutionResponse:
    """Execute a workflow tool with one canonical agent execution context."""
    from src.services.execution.service import execute_tool

    return await execute_tool(
        workflow_id=str(workflow_id),
        workflow_name=workflow_name,
        parameters=parameters,
        user_id=caller.user_id,
        user_email=caller.email,
        user_name=caller.name,
        org_id=str(organization_id) if organization_id else None,
        is_platform_admin=caller.is_platform_admin,
        is_agent=True,
        execution_id=execution_id,
        artifact_workspace_id=artifact_workspace_id,
        sync=sync,
    )
