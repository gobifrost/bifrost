"""Failed workflows retain evidence needed for an informed next agent step."""

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.models.contracts.agents import ToolResult
from src.models.contracts.executions import WorkflowExecutionResponse
from src.models.enums import ExecutionStatus
from src.services.agent_executor import AgentExecutor, _serialize_tool_result_for_history
from src.services.execution.agent_workflow_tools import workflow_failure_receipt
from src.services.execution.autonomous_agent_executor import AutonomousAgentExecutor
from src.services.llm.base import ToolCallRequest


def _failed_response() -> WorkflowExecutionResponse:
    return WorkflowExecutionResponse(
        execution_id=str(uuid4()),
        status=ExecutionStatus.FAILED,
        error="Finalization failed after accepted identity work",
        error_type="UserError",
        result={
            "completed_effects": ["sessions_revoked"],
            "uncertain_effects": ["ticket_completion"],
            "verification_receipts": [{"id": "identity-readback", "verified": True}],
        },
        details={"failure_phase": "ticket_finalization"},
        variables={"private_runtime_value": "excluded"},
        logs=[{"message": "excluded debug output"}],
    )


def test_failed_workflow_receipt_preserves_only_returned_tool_evidence():
    response = _failed_response()
    receipt = workflow_failure_receipt(response)

    assert receipt["execution_id"] == response.execution_id
    assert receipt["result"] == response.result
    assert receipt["details"] == {"failure_phase": "ticket_finalization"}
    assert "variables" not in receipt
    assert "logs" not in receipt


def test_chat_failed_result_history_retains_receipt_and_partial_effects():
    response = _failed_response()
    result = ToolResult(
        tool_call_id="finish-call",
        tool_name="finish",
        error=response.error,
        result=response.result,
        metadata=workflow_failure_receipt(response),
    )

    history = json.loads(_serialize_tool_result_for_history(result))

    assert history["error"] == response.error
    assert history["result"]["completed_effects"] == ["sessions_revoked"]
    assert history["result"]["uncertain_effects"] == ["ticket_completion"]
    assert history["details"]["failure_phase"] == "ticket_finalization"
    assert history["execution_id"] == response.execution_id


@pytest.mark.asyncio
async def test_autonomous_failure_returns_receipt_without_repeating_workflow():
    agent = SimpleNamespace(
        id=uuid4(),
        knowledge_sources=[],
        system_tools=[],
        delegated_agents=[],
        mcp_connections=[],
    )
    caller = MagicMock()

    @asynccontextmanager
    async def session_factory():
        yield MagicMock()

    executor = AutonomousAgentExecutor(session_factory)
    executor._caller = caller
    workflow_id = uuid4()
    executor._tool_workflow_id_map = {"finish": workflow_id}
    response = _failed_response()
    dispatch = AsyncMock(return_value=response)
    with (
        patch(
            "src.services.execution.autonomous_agent_executor.authorize_agent_execution",
            new=AsyncMock(return_value=(caller, agent)),
        ),
        patch(
            "src.services.execution.autonomous_agent_executor.execute_agent_workflow_tool",
            new=dispatch,
        ),
    ):
        result = await executor._execute_tool(
            ToolCallRequest(id="finish-call", name="finish", arguments={}), agent
        )

    receipt = json.loads(result.removeprefix("Error: "))
    assert receipt == workflow_failure_receipt(response)
    assert dispatch.await_count == 1
    assert dispatch.await_args.kwargs["caller"] is caller
    assert executor._last_workflow_execution_id == response.execution_id
    assert executor._last_workflow_execution_is_error is True


@pytest.mark.asyncio
@pytest.mark.parametrize("reported_error", [True, False])
async def test_chat_workflow_failure_retains_the_actual_execution_receipt(reported_error):
    agent = SimpleNamespace(
        id=uuid4(),
        knowledge_sources=[],
        system_tools=[],
        delegated_agents=[],
        mcp_connections=[],
    )
    caller = MagicMock()

    @asynccontextmanager
    async def session_factory():
        yield AsyncMock()

    executor = AgentExecutor(session_factory)
    workflow_id = uuid4()
    response = _failed_response()
    if not reported_error:
        response.error = None
    dispatch = AsyncMock(return_value=response)
    with (
        patch("src.services.agent_executor.authorize_agent_execution", new=AsyncMock(return_value=(caller, agent))),
        patch("src.services.agent_executor.execute_agent_workflow_tool", new=dispatch),
    ):
        result = await executor._execute_tool(
            ToolCallRequest(id="finish-call", name="finish", arguments={}),
            agent, None, caller=caller, workflow_ids={"finish": workflow_id},
        )

    assert result.error == workflow_failure_receipt(response)["error"]
    assert result.result == response.result
    assert result.metadata == workflow_failure_receipt(response)
    dispatch.assert_awaited_once()
    assert dispatch.await_args.kwargs["caller"] is caller
