"""One real workflow dispatch from CodeMode in the existing autonomous loop.

The production Automatic policy supplies CodeMode without test injection. The single workflow
job proves nested calls still cross the shared PostgreSQL executor boundary;
schema, result-size, sandbox and cancellation cases live in the unit tests.
"""

from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from sqlalchemy import delete, select

from src.models.orm.agent_runs import AgentRun, AgentRunStep
from src.models.orm.executions import Execution
from src.services.agent_runtime.composition import _qualified_tool_name
from src.services.execution.autonomous_agent_executor import AutonomousAgentExecutor
from src.services.llm.base import LLMConfig
from tests.e2e.api.test_agent_capability_composition import (
    _create_agent,
    _load_agent,
    _load_runtime_caller,
)


@pytest.mark.asyncio
async def test_code_mode_dispatches_one_bound_workflow_on_existing_executors(
    e2e_client, platform_admin, async_session_factory, db_session, test_tool_workflow,
):
    tag = uuid4().hex[:8]
    child = _create_agent(
        e2e_client, platform_admin,
        name=f"Code Mode Specialist {tag}", system_prompt="Read the bound test tool.",
        tool_ids=[test_tool_workflow["id"]],
    )
    root = _create_agent(
        e2e_client, platform_admin,
        name=f"Code Mode Owner {tag}", system_prompt="Retain task ownership.",
        delegated_agent_ids=[child["id"]],
    )
    child_id = UUID(child["id"])
    tool_name = _qualified_tool_name(child_id, f"wf_{test_tool_workflow['name']}")
    capability_id = f"bifrost_agent_{child_id.hex}"
    run_id = uuid4()
    model_requests = 0
    final_tool_value = None

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal model_requests, final_tool_value
        model_requests += 1
        if model_requests == 1:
            names = {tool.name for tool in info.function_tools}
            assert "load_capability" in names
            assert "run_code" in names
            assert tool_name not in names
            part = ToolCallPart("load_capability", {"id": capability_id})
        elif model_requests == 2:
            names = {tool.name for tool in info.function_tools}
            assert "run_code" in names
            assert tool_name not in names
            part = ToolCallPart("search_tools", {"queries": [test_tool_workflow["name"]]})
        elif model_requests == 3:
            names = {tool.name for tool in info.function_tools}
            assert "run_code" in names
            assert tool_name not in names
            part = ToolCallPart("run_code", {
                "code": f"receipt = {tool_name}(query='code-mode-boundary')\nreceipt",
            })
        else:
            final_tool_value = next(
                part.content for message in messages if isinstance(message, ModelRequest)
                for part in message.parts
                if isinstance(part, ToolReturnPart) and part.tool_name == "run_code"
            )
            return ModelResponse(parts=[TextPart("Bound workflow verified.")])
        return ModelResponse(parts=[part])

    try:
        root_agent = await _load_agent(db_session, root["id"])
        db_session.add(AgentRun(
            id=run_id, agent_id=root_agent.id, trigger_type="manual", status="running",
            caller_user_id=str(platform_admin.user_id), run_user_id=platform_admin.user_id,
            acting_user_id=platform_admin.user_id,
        ))
        await db_session.commit()
        caller = await _load_runtime_caller(db_session, run_id)
        executor = AutonomousAgentExecutor(async_session_factory)
        with (
            patch(
                "src.services.execution.autonomous_agent_executor.get_llm_configs",
                new=AsyncMock(return_value=[LLMConfig(
                    provider="openai", model="test-code-mode", api_key="test-key",
                )]),
            ),
            patch(
                "src.services.agent_runtime.model_factory.create_agent_model",
                return_value=FunctionModel(respond),
            ),
        ):
            result = await executor.run(
                agent=root_agent, input_data={"task": "Run the bound test workflow."},
                run_id=str(run_id), caller=caller, run_user_id=platform_admin.user_id,
                run_as=None, agent_execution_mode="automatic",
            )
        assert result["status"] == "completed", result
        assert result["output"] == "Bound workflow verified."
        assert "result: code-mode-boundary" in str(final_tool_value)
        await executor.flush_to_db(db_session)
        await db_session.commit()
        steps = list((await db_session.execute(
            select(AgentRunStep).where(AgentRunStep.run_id == run_id)
        )).scalars())
        nested = [step for step in steps if (step.content or {}).get("tool_name") == tool_name]
        assert sorted(step.type for step in nested) == ["tool_call", "tool_result"]
        assert all(step.content["source_agent_id"] == child["id"] for step in nested)
        result_step = next(step for step in nested if step.type == "tool_result")
        assert result_step.content["execution_id"]
        execution = await db_session.get(Execution, UUID(result_step.content["execution_id"]))
        assert execution is not None
        assert str(execution.workflow_id) == test_tool_workflow["id"]
        assert execution.result == "result: code-mode-boundary"
    finally:
        # This fixture's workflow belongs only to this test. Remove its job even
        # if an assertion failed before the execution receipt was inspected.
        await db_session.execute(delete(Execution).where(
            Execution.workflow_id == UUID(test_tool_workflow["id"])
        ))
        await db_session.execute(delete(AgentRun).where(AgentRun.id == run_id))
        await db_session.commit()
        for agent in (root, child):
            e2e_client.delete(f"/api/agents/{agent['id']}", headers=platform_admin.headers)
