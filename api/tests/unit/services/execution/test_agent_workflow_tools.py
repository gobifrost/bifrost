"""Tests for the shared agent workflow-tool execution boundary."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from shared.run_lineage import RunLineage

from src.services.execution.agent_workflow_tools import (
    AgentWorkflowCaller,
    execute_agent_workflow_tool,
)


@pytest.mark.asyncio
async def test_execute_agent_workflow_tool_builds_canonical_agent_context():
    workflow_id = uuid4()
    organization_id = uuid4()
    run_user_id = uuid4()
    response = MagicMock()
    caller = AgentWorkflowCaller(
        user_id=str(uuid4()),
        email="person@example.com",
        name="Person",
        organization_id=organization_id,
        run_user_id=run_user_id,
        is_platform_admin=True,
    )

    with patch(
        "src.services.execution.service.execute_tool",
        new_callable=AsyncMock,
        return_value=response,
    ) as execute_tool:
        result = await execute_agent_workflow_tool(
            workflow_id=workflow_id,
            workflow_name="ticket_lookup",
            parameters={"ticket_id": 42},
            caller=caller,
            execution_id="execution-1",
            artifact_workspace_id="workspace-1",
            sync=False,
        )

    assert result is response
    execute_tool.assert_awaited_once_with(
        workflow_id=str(workflow_id),
        workflow_name="ticket_lookup",
        parameters={"ticket_id": 42},
        user_id=caller.user_id,
        user_email=caller.email,
        user_name=caller.name,
        org_id=str(organization_id),
        is_platform_admin=True,
        is_agent=True,
        execution_id="execution-1",
        artifact_workspace_id="workspace-1",
        sync=False,
        lineage=RunLineage(run_user_id, run_user_id, None),
    )


@pytest.mark.asyncio
async def test_agent_run_without_a_recorded_run_user_starts_tools_without_lineage():
    caller = AgentWorkflowCaller(
        user_id=str(uuid4()),
        email="person@example.com",
        name="Person",
        organization_id=None,
        run_user_id=None,
    )

    with patch(
        "src.services.execution.service.execute_tool",
        new_callable=AsyncMock,
    ) as execute_tool:
        await execute_agent_workflow_tool(
            workflow_id=uuid4(), workflow_name="t", parameters={}, caller=caller
        )

    assert execute_tool.await_args.kwargs["lineage"] is None
