"""Unit tests for AutonomousAgentExecutor."""
import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from src.services.execution.agent_helpers import find_delegated_agent
from src.services.execution.autonomous_agent_executor import (
    AutonomousAgentExecutor,
    MAX_DELEGATION_DEPTH,
    ToolError,
)
from src.services.llm.base import LLMResponse, ToolCallRequest


@pytest.fixture
def mock_session():
    """Create a mock session factory (async_sessionmaker) for the executor.

    The executor expects a session factory, not a raw session. This fixture
    returns a callable that produces an async context manager yielding a
    mock session.
    """
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.get = AsyncMock(return_value=None)

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    factory = MagicMock(return_value=mock_ctx)
    # Attach session for tests that need to inspect it
    factory._mock_session = session
    return factory


@pytest.fixture
def mock_agent():
    agent = MagicMock()
    agent.id = uuid4()
    agent.name = "Test Agent"
    agent.system_prompt = "You are a test agent."
    agent.tools = []
    agent.system_tools = []
    agent.knowledge_sources = []
    agent.delegated_agents = []
    agent.max_iterations = 10
    agent.max_token_budget = 50000
    agent.llm_model = None
    agent.llm_max_tokens = None
    agent.organization_id = uuid4()
    return agent


class TestAutonomousAgentExecutor:
    @pytest.mark.asyncio
    @patch("src.services.execution.autonomous_agent_executor.get_llm_client")
    @patch("src.services.execution.autonomous_agent_executor.resolve_agent_tools")
    async def test_run_returns_structured_result(self, mock_resolve_tools, mock_get_llm, mock_session, mock_agent):
        """Run returns output, iterations_used, tokens_used, status."""
        mock_resolve_tools.return_value = ([], {})

        mock_llm = AsyncMock()
        mock_llm.complete = AsyncMock(return_value=LLMResponse(
            content="Hello world",
            tool_calls=None,
            finish_reason="end_turn",
            input_tokens=100,
            output_tokens=50,
        ))
        mock_get_llm.return_value = mock_llm

        executor = AutonomousAgentExecutor(mock_session)
        result = await executor.run(
            agent=mock_agent,
            input_data={"message": "hello"},
            run_id=str(uuid4()),
        )

        assert result["status"] == "completed"
        assert result["output"] == "Hello world"
        assert result["iterations_used"] == 1
        assert result["tokens_used"] == 150

    @pytest.mark.asyncio
    @patch("src.services.execution.autonomous_agent_executor.get_llm_client")
    @patch("src.services.execution.autonomous_agent_executor.resolve_agent_tools")
    async def test_run_records_steps(self, mock_resolve_tools, mock_get_llm, mock_session, mock_agent):
        """Run records AgentRunStep entries via session.add."""
        mock_resolve_tools.return_value = ([], {})

        mock_llm = AsyncMock()
        mock_llm.complete = AsyncMock(return_value=LLMResponse(
            content="Response",
            tool_calls=None,
            finish_reason="end_turn",
            input_tokens=100,
            output_tokens=50,
        ))
        mock_get_llm.return_value = mock_llm

        executor = AutonomousAgentExecutor(mock_session)
        await executor.run(
            agent=mock_agent,
            input_data={"task": "analyze"},
            run_id=str(uuid4()),
        )

        # Verify steps were buffered (Redis-first: steps are in _pending_steps, not DB)
        assert len(executor._pending_steps) >= 2  # llm_request + llm_response
        step_types = [s["type"] for s in executor._pending_steps]
        assert "llm_request" in step_types
        assert "llm_response" in step_types

    @pytest.mark.asyncio
    @patch("src.services.execution.autonomous_agent_executor.get_llm_client")
    @patch("src.services.execution.autonomous_agent_executor.resolve_agent_tools")
    async def test_run_with_tool_calls(self, mock_resolve_tools, mock_get_llm, mock_session, mock_agent):
        """Run executes tools and continues the loop until no more tool calls."""
        workflow_id = uuid4()
        mock_resolve_tools.return_value = (
            [MagicMock(name="my_tool")],
            {"my_tool": workflow_id},
        )

        # First call returns a tool call, second call returns final content
        mock_llm = AsyncMock()
        mock_llm.complete = AsyncMock(side_effect=[
            LLMResponse(
                content=None,
                tool_calls=[ToolCallRequest(id="tc1", name="my_tool", arguments={"x": 1})],
                finish_reason="tool_use",
                input_tokens=100,
                output_tokens=50,
            ),
            LLMResponse(
                content="Final answer",
                tool_calls=None,
                finish_reason="end_turn",
                input_tokens=200,
                output_tokens=100,
            ),
        ])
        mock_get_llm.return_value = mock_llm

        # Mock the workflow tool execution (imported inside _execute_tool)
        with patch("src.services.execution.service.execute_tool") as mock_exec_tool:
            mock_exec_tool.return_value = MagicMock(result="tool output", status=MagicMock(value="completed"))

            executor = AutonomousAgentExecutor(mock_session)
            result = await executor.run(
                agent=mock_agent,
                input_data={"task": "do something"},
                run_id=str(uuid4()),
            )

        assert result["status"] == "completed"
        assert result["output"] == "Final answer"
        assert result["iterations_used"] == 2
        assert result["tokens_used"] == 450  # 150 + 300

    @pytest.mark.asyncio
    @patch("src.services.execution.autonomous_agent_executor.get_llm_client")
    @patch("src.services.execution.autonomous_agent_executor.resolve_agent_tools")
    async def test_run_respects_iteration_budget(self, mock_resolve_tools, mock_get_llm, mock_session, mock_agent):
        """Run stops when max_iterations is exceeded."""
        mock_agent.max_iterations = 2
        mock_resolve_tools.return_value = (
            [MagicMock(name="my_tool")],
            {"my_tool": uuid4()},
        )

        # Always return tool calls so the loop never ends naturally
        mock_llm = AsyncMock()
        mock_llm.complete = AsyncMock(return_value=LLMResponse(
            content=None,
            tool_calls=[ToolCallRequest(id="tc1", name="my_tool", arguments={})],
            finish_reason="tool_use",
            input_tokens=10,
            output_tokens=5,
        ))
        mock_get_llm.return_value = mock_llm

        with patch("src.services.execution.service.execute_tool") as mock_exec_tool:
            mock_exec_tool.return_value = MagicMock(result="ok", status=MagicMock(value="completed"))

            executor = AutonomousAgentExecutor(mock_session)
            result = await executor.run(
                agent=mock_agent,
                run_id=str(uuid4()),
            )

        assert result["status"] == "budget_exceeded"
        assert result["iterations_used"] == 2

    @pytest.mark.asyncio
    @patch("src.services.execution.autonomous_agent_executor.get_llm_client")
    @patch("src.services.execution.autonomous_agent_executor.resolve_agent_tools")
    async def test_run_handles_llm_error(self, mock_resolve_tools, mock_get_llm, mock_session, mock_agent):
        """Run returns failed status when LLM call raises."""
        mock_resolve_tools.return_value = ([], {})

        mock_llm = AsyncMock()
        mock_llm.complete = AsyncMock(side_effect=RuntimeError("API timeout"))
        mock_get_llm.return_value = mock_llm

        executor = AutonomousAgentExecutor(mock_session)
        result = await executor.run(
            agent=mock_agent,
            run_id=str(uuid4()),
        )

        assert result["status"] == "failed"
        assert "API timeout" in result["error"]
        assert result["output"] is None

    @pytest.mark.asyncio
    @patch("src.services.execution.autonomous_agent_executor.get_llm_client")
    @patch("src.services.execution.autonomous_agent_executor.resolve_agent_tools")
    async def test_run_parses_json_output_when_schema_given(
        self, mock_resolve_tools, mock_get_llm, mock_session, mock_agent
    ):
        """When output_schema is provided, run attempts to parse JSON from LLM output."""
        mock_resolve_tools.return_value = ([], {})

        mock_llm = AsyncMock()
        mock_llm.complete = AsyncMock(return_value=LLMResponse(
            content='{"result": 42}',
            tool_calls=None,
            finish_reason="end_turn",
            input_tokens=100,
            output_tokens=50,
        ))
        mock_get_llm.return_value = mock_llm

        executor = AutonomousAgentExecutor(mock_session)
        result = await executor.run(
            agent=mock_agent,
            output_schema={"type": "object", "properties": {"result": {"type": "integer"}}},
            run_id=str(uuid4()),
        )

        assert result["status"] == "completed"
        assert result["output"] == {"result": 42}

    @pytest.mark.asyncio
    @patch("src.services.execution.autonomous_agent_executor.get_llm_client")
    @patch("src.services.execution.autonomous_agent_executor.resolve_agent_tools")
    async def test_run_handles_tool_execution_error(
        self, mock_resolve_tools, mock_get_llm, mock_session, mock_agent
    ):
        """Tool execution errors are caught and fed back to the LLM."""
        workflow_id = uuid4()
        mock_resolve_tools.return_value = (
            [MagicMock(name="broken_tool")],
            {"broken_tool": workflow_id},
        )

        mock_llm = AsyncMock()
        mock_llm.complete = AsyncMock(side_effect=[
            LLMResponse(
                content=None,
                tool_calls=[ToolCallRequest(id="tc1", name="broken_tool", arguments={})],
                finish_reason="tool_use",
                input_tokens=50,
                output_tokens=25,
            ),
            LLMResponse(
                content="Recovered from error",
                tool_calls=None,
                finish_reason="end_turn",
                input_tokens=100,
                output_tokens=50,
            ),
        ])
        mock_get_llm.return_value = mock_llm

        with patch("src.services.execution.service.execute_tool") as mock_exec_tool:
            mock_exec_tool.side_effect = RuntimeError("Tool crashed")

            executor = AutonomousAgentExecutor(mock_session)
            result = await executor.run(
                agent=mock_agent,
                run_id=str(uuid4()),
            )

        assert result["status"] == "completed"
        assert result["output"] == "Recovered from error"

    @pytest.mark.asyncio
    @patch("src.services.execution.autonomous_agent_executor.get_llm_client")
    @patch("src.services.execution.autonomous_agent_executor.resolve_agent_tools")
    async def test_delegation_uses_find_delegated_agent(
        self, mock_resolve_tools, mock_get_llm, mock_session, mock_agent
    ):
        """Delegation tool calls use find_delegated_agent to resolve target."""
        # Create a delegated agent
        delegated = MagicMock()
        delegated.id = uuid4()
        delegated.name = "Sub Agent"
        delegated.is_active = True
        delegated.system_prompt = "You are a sub agent."
        delegated.tools = []
        delegated.system_tools = []
        delegated.knowledge_sources = []
        delegated.delegated_agents = []
        delegated.max_iterations = 5
        delegated.max_token_budget = 10000
        delegated.llm_model = None
        delegated.llm_max_tokens = None
        delegated.organization_id = mock_agent.organization_id

        mock_agent.delegated_agents = [delegated]

        # Verify find_delegated_agent resolves correctly
        found = find_delegated_agent(mock_agent, "delegate_to_sub_agent")
        assert found is delegated
        assert find_delegated_agent(mock_agent, "delegate_to_nonexistent") is None

        # Set up the main agent to make a delegation call
        mock_resolve_tools.return_value = (
            [MagicMock(name="delegate_to_sub_agent")],
            {},
        )

        # Mock session.execute to return the re-fetched delegated agent
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = delegated
        mock_session._mock_session.execute = AsyncMock(return_value=mock_result)

        mock_llm = AsyncMock()
        mock_llm.complete = AsyncMock(side_effect=[
            # Main agent delegates
            LLMResponse(
                content=None,
                tool_calls=[ToolCallRequest(
                    id="tc1",
                    name="delegate_to_sub_agent",
                    arguments={"task": "Summarize data"},
                )],
                finish_reason="tool_use",
                input_tokens=100,
                output_tokens=50,
            ),
            # Sub agent responds (called by recursive run)
            LLMResponse(
                content="Sub agent summary",
                tool_calls=None,
                finish_reason="end_turn",
                input_tokens=80,
                output_tokens=40,
            ),
            # Main agent uses sub result
            LLMResponse(
                content="Final: Sub agent summary",
                tool_calls=None,
                finish_reason="end_turn",
                input_tokens=200,
                output_tokens=100,
            ),
        ])
        mock_get_llm.return_value = mock_llm

        executor = AutonomousAgentExecutor(mock_session)
        result = await executor.run(
            agent=mock_agent,
            input_data={"task": "Delegate work"},
            run_id=str(uuid4()),
        )

        assert result["status"] == "completed"
        assert "Sub agent summary" in result["output"]

    @pytest.mark.asyncio
    @patch("src.services.execution.autonomous_agent_executor.get_llm_client")
    @patch("src.services.execution.autonomous_agent_executor.resolve_agent_tools")
    async def test_delegation_refetches_agent_with_relationships(
        self, mock_resolve_tools, mock_get_llm, mock_session, mock_agent
    ):
        """Delegation re-fetches the target agent with eager-loaded relationships.

        This prevents the greenlet_spawn error that occurs when SQLAlchemy
        tries to lazy-load relationships (tools, delegated_agents) on an agent
        that was loaded as a child of another agent's selectinload.
        """
        delegated = MagicMock()
        delegated.id = uuid4()
        delegated.name = "Troubleshooting Agent"
        delegated.is_active = True
        delegated.system_prompt = "You troubleshoot."
        delegated.tools = []
        delegated.system_tools = []
        delegated.knowledge_sources = []
        delegated.delegated_agents = []
        delegated.max_iterations = 5
        delegated.max_token_budget = 10000
        delegated.llm_model = None
        delegated.llm_max_tokens = None
        delegated.organization_id = mock_agent.organization_id

        mock_agent.delegated_agents = [delegated]

        mock_resolve_tools.return_value = (
            [MagicMock(name="delegate_to_troubleshooting_agent")],
            {},
        )

        # Track the re-fetch query
        refetched_agent = MagicMock()
        refetched_agent.id = delegated.id
        refetched_agent.name = "Troubleshooting Agent"
        refetched_agent.system_prompt = "You troubleshoot."
        refetched_agent.tools = []
        refetched_agent.system_tools = []
        refetched_agent.knowledge_sources = []
        refetched_agent.delegated_agents = []
        refetched_agent.max_iterations = 5
        refetched_agent.max_token_budget = 10000
        refetched_agent.llm_model = None
        refetched_agent.llm_max_tokens = None
        refetched_agent.organization_id = mock_agent.organization_id

        mock_result = MagicMock()
        mock_result.scalar_one.return_value = refetched_agent
        mock_session._mock_session.execute = AsyncMock(return_value=mock_result)

        mock_llm = AsyncMock()
        mock_llm.complete = AsyncMock(side_effect=[
            LLMResponse(
                content=None,
                tool_calls=[ToolCallRequest(
                    id="tc1",
                    name="delegate_to_troubleshooting_agent",
                    arguments={"task": "Fix the issue"},
                )],
                finish_reason="tool_use",
                input_tokens=100,
                output_tokens=50,
            ),
            LLMResponse(
                content="Issue resolved",
                tool_calls=None,
                finish_reason="end_turn",
                input_tokens=80,
                output_tokens=40,
            ),
            LLMResponse(
                content="Delegation complete: Issue resolved",
                tool_calls=None,
                finish_reason="end_turn",
                input_tokens=200,
                output_tokens=100,
            ),
        ])
        mock_get_llm.return_value = mock_llm

        executor = AutonomousAgentExecutor(mock_session)
        result = await executor.run(
            agent=mock_agent,
            input_data={"task": "Triage this ticket"},
            run_id=str(uuid4()),
        )

        assert result["status"] == "completed"

        # Verify session.execute was called to re-fetch the delegated agent
        execute_calls = mock_session._mock_session.execute.call_args_list
        assert len(execute_calls) >= 1, "Expected at least one session.execute call for re-fetch"

    @pytest.mark.asyncio
    @patch("src.services.execution.autonomous_agent_executor.get_llm_client")
    @patch("src.services.execution.autonomous_agent_executor.resolve_agent_tools")
    async def test_delegation_passes_redis_client_to_sub_executor(
        self, mock_resolve_tools, mock_get_llm, mock_session, mock_agent
    ):
        """Sub-executor receives the parent's redis_client for pub/sub."""
        delegated = MagicMock()
        delegated.id = uuid4()
        delegated.name = "Sub Agent"
        delegated.is_active = True
        delegated.system_prompt = "You are a sub agent."
        delegated.tools = []
        delegated.system_tools = []
        delegated.knowledge_sources = []
        delegated.delegated_agents = []
        delegated.max_iterations = 5
        delegated.max_token_budget = 10000
        delegated.llm_model = None
        delegated.llm_max_tokens = None
        delegated.organization_id = mock_agent.organization_id

        mock_agent.delegated_agents = [delegated]

        mock_resolve_tools.return_value = (
            [MagicMock(name="delegate_to_sub_agent")],
            {},
        )

        mock_result = MagicMock()
        mock_result.scalar_one.return_value = delegated
        mock_session._mock_session.execute = AsyncMock(return_value=mock_result)

        mock_llm = AsyncMock()
        mock_llm.complete = AsyncMock(side_effect=[
            LLMResponse(
                content=None,
                tool_calls=[ToolCallRequest(
                    id="tc1", name="delegate_to_sub_agent",
                    arguments={"task": "Do work"},
                )],
                finish_reason="tool_use",
                input_tokens=100, output_tokens=50,
            ),
            LLMResponse(
                content="Done",
                tool_calls=None, finish_reason="end_turn",
                input_tokens=80, output_tokens=40,
            ),
            LLMResponse(
                content="All done",
                tool_calls=None, finish_reason="end_turn",
                input_tokens=200, output_tokens=100,
            ),
        ])
        mock_get_llm.return_value = mock_llm

        mock_redis = MagicMock()
        executor = AutonomousAgentExecutor(mock_session, redis_client=mock_redis)

        with patch.object(
            AutonomousAgentExecutor, "__init__", wraps=AutonomousAgentExecutor.__init__
        ) as mock_init:
            # Re-init our executor since we patched __init__
            mock_init.reset_mock()
            result = await executor.run(
                agent=mock_agent,
                input_data={"task": "Delegate"},
                run_id=str(uuid4()),
            )

        assert result["status"] == "completed"
        # Verify the sub-executor was constructed with redis_client
        sub_init_calls = [
            c for c in mock_init.call_args_list
            if c.kwargs.get("redis_client") is mock_redis
            or (len(c.args) > 2 and c.args[2] is mock_redis)
        ]
        assert len(sub_init_calls) >= 1, "Sub-executor should receive parent's redis_client"

    @pytest.mark.asyncio
    @patch("src.services.execution.autonomous_agent_executor.get_llm_client")
    @patch("src.services.execution.autonomous_agent_executor.resolve_agent_tools")
    async def test_delegation_respects_depth_limit(
        self, mock_resolve_tools, mock_get_llm, mock_session, mock_agent
    ):
        """Delegation fails gracefully when depth limit is exceeded."""
        delegated = MagicMock()
        delegated.id = uuid4()
        delegated.name = "Deep Agent"
        delegated.is_active = True
        mock_agent.delegated_agents = [delegated]

        mock_resolve_tools.return_value = (
            [MagicMock(name="delegate_to_deep_agent")],
            {},
        )

        mock_llm = AsyncMock()
        mock_llm.complete = AsyncMock(side_effect=[
            LLMResponse(
                content=None,
                tool_calls=[ToolCallRequest(
                    id="tc1", name="delegate_to_deep_agent",
                    arguments={"task": "Go deeper"},
                )],
                finish_reason="tool_use",
                input_tokens=100, output_tokens=50,
            ),
            LLMResponse(
                content="Hit the limit",
                tool_calls=None, finish_reason="end_turn",
                input_tokens=80, output_tokens=40,
            ),
        ])
        mock_get_llm.return_value = mock_llm

        # Start at max depth — delegation should be rejected immediately
        executor = AutonomousAgentExecutor(
            mock_session, _delegation_depth=MAX_DELEGATION_DEPTH
        )
        result = await executor.run(
            agent=mock_agent,
            input_data={"task": "Deep delegation"},
            run_id=str(uuid4()),
        )

        assert result["status"] == "completed"
        # The LLM should have received the depth limit error as a tool result
        tool_result_messages = [
            m for m in mock_llm.complete.call_args_list
            if any(
                hasattr(msg, "role") and msg.role == "tool"
                for msg in m.kwargs.get("messages", m.args[0] if m.args else [])
            )
        ]
        assert len(tool_result_messages) >= 1

    @pytest.mark.asyncio
    @patch("src.services.execution.autonomous_agent_executor.get_llm_client")
    @patch("src.services.execution.autonomous_agent_executor.resolve_agent_tools")
    async def test_delegation_timeout(
        self, mock_resolve_tools, mock_get_llm, mock_session, mock_agent
    ):
        """Delegation returns timeout error when sub-executor takes too long."""
        delegated = MagicMock()
        delegated.id = uuid4()
        delegated.name = "Slow Agent"
        delegated.is_active = True
        delegated.system_prompt = "You are slow."
        delegated.tools = []
        delegated.system_tools = []
        delegated.knowledge_sources = []
        delegated.delegated_agents = []
        delegated.max_iterations = 5
        delegated.max_token_budget = 10000
        delegated.llm_model = None
        delegated.llm_max_tokens = None
        delegated.organization_id = mock_agent.organization_id

        mock_agent.delegated_agents = [delegated]

        mock_resolve_tools.return_value = (
            [MagicMock(name="delegate_to_slow_agent")],
            {},
        )

        mock_result = MagicMock()
        mock_result.scalar_one.return_value = delegated
        mock_session._mock_session.execute = AsyncMock(return_value=mock_result)

        mock_llm = AsyncMock()
        mock_llm.complete = AsyncMock(side_effect=[
            LLMResponse(
                content=None,
                tool_calls=[ToolCallRequest(
                    id="tc1", name="delegate_to_slow_agent",
                    arguments={"task": "Take forever"},
                )],
                finish_reason="tool_use",
                input_tokens=100, output_tokens=50,
            ),
            # After timeout error, LLM responds
            LLMResponse(
                content="The delegation timed out",
                tool_calls=None, finish_reason="end_turn",
                input_tokens=200, output_tokens=100,
            ),
        ])
        mock_get_llm.return_value = mock_llm

        executor = AutonomousAgentExecutor(mock_session)

        # Patch asyncio.wait_for to simulate timeout
        async def mock_wait_for(coro, *, timeout):  # noqa: ARG001
            coro.close()  # Clean up the coroutine
            raise asyncio.TimeoutError()

        with patch("src.services.execution.autonomous_agent_executor.asyncio.wait_for", mock_wait_for):
            result = await executor.run(
                agent=mock_agent,
                input_data={"task": "Delegate to slow agent"},
                run_id=str(uuid4()),
            )

        assert result["status"] == "completed"
        assert "timed out" in result["output"].lower() or result["output"] == "The delegation timed out"

    @pytest.mark.asyncio
    @patch("src.services.execution.autonomous_agent_executor.get_llm_client")
    @patch("src.services.execution.autonomous_agent_executor.resolve_agent_tools")
    async def test_unknown_tool_raises_tool_error(
        self, mock_resolve_tools, mock_get_llm, mock_session, mock_agent
    ):
        """Unknown tool calls raise ToolError and are recorded as tool_error steps."""
        mock_resolve_tools.return_value = (
            [MagicMock(name="some_tool")],
            {},  # No workflow ID mappings — tool lookup will fail
        )

        mock_llm = AsyncMock()
        mock_llm.complete = AsyncMock(side_effect=[
            LLMResponse(
                content=None,
                tool_calls=[ToolCallRequest(
                    id="tc1", name="nonexistent_tool", arguments={},
                )],
                finish_reason="tool_use",
                input_tokens=100, output_tokens=50,
            ),
            LLMResponse(
                content="Recovered",
                tool_calls=None, finish_reason="end_turn",
                input_tokens=80, output_tokens=40,
            ),
        ])
        mock_get_llm.return_value = mock_llm

        executor = AutonomousAgentExecutor(mock_session)
        result = await executor.run(
            agent=mock_agent,
            run_id=str(uuid4()),
        )

        assert result["status"] == "completed"
        assert result["output"] == "Recovered"

        # The tool error message should have been fed to the LLM
        second_call_messages = mock_llm.complete.call_args_list[1].kwargs.get(
            "messages", mock_llm.complete.call_args_list[1].args[0] if mock_llm.complete.call_args_list[1].args else []
        )
        tool_messages = [m for m in second_call_messages if hasattr(m, "role") and m.role == "tool"]
        assert len(tool_messages) >= 1
        assert "Unknown tool" in tool_messages[0].content

        # Verify a tool_error step was buffered (Redis-first pattern)
        error_steps = [s for s in executor._pending_steps if s["type"] == "tool_error"]
        assert len(error_steps) >= 1

    @pytest.mark.asyncio
    async def test_privileged_agent_management_tool_is_blocked(self, mock_session, mock_agent):
        """Autonomous agents cannot execute privileged agent-management system tools."""
        mock_agent.system_tools = ["create_agent"]

        executor = AutonomousAgentExecutor(mock_session)
        tool_call = ToolCallRequest(id="tc1", name="create_agent", arguments={})

        with patch.object(executor, "_execute_system_tool", new_callable=AsyncMock) as mock_system_tool:
            with pytest.raises(ToolError, match="cannot be executed from autonomous agents"):
                await executor._execute_tool(tool_call, mock_agent)

        mock_system_tool.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("src.services.execution.autonomous_agent_executor.get_llm_client")
    @patch("src.services.execution.autonomous_agent_executor.resolve_agent_tools")
    async def test_delegation_creates_child_agent_run(
        self, mock_resolve_tools, mock_get_llm, mock_session, mock_agent
    ):
        """Delegation creates a child AgentRun with parent_run_id set."""
        delegated = MagicMock()
        delegated.id = uuid4()
        delegated.name = "Child Agent"
        delegated.is_active = True
        delegated.system_prompt = "You are a child."
        delegated.tools = []
        delegated.system_tools = []
        delegated.knowledge_sources = []
        delegated.delegated_agents = []
        delegated.max_iterations = 5
        delegated.max_token_budget = 10000
        delegated.llm_model = None
        delegated.llm_max_tokens = None
        delegated.organization_id = mock_agent.organization_id

        mock_agent.delegated_agents = [delegated]

        mock_resolve_tools.return_value = (
            [MagicMock(name="delegate_to_child_agent")],
            {},
        )

        mock_result = MagicMock()
        mock_result.scalar_one.return_value = delegated
        mock_session._mock_session.execute = AsyncMock(return_value=mock_result)

        mock_llm = AsyncMock()
        mock_llm.complete = AsyncMock(side_effect=[
            LLMResponse(
                content=None,
                tool_calls=[ToolCallRequest(
                    id="tc1", name="delegate_to_child_agent",
                    arguments={"task": "Do work"},
                )],
                finish_reason="tool_use",
                input_tokens=100, output_tokens=50,
            ),
            # Sub agent responds
            LLMResponse(
                content="Child done",
                tool_calls=None, finish_reason="end_turn",
                input_tokens=80, output_tokens=40,
            ),
            # Main agent finishes
            LLMResponse(
                content="All done",
                tool_calls=None, finish_reason="end_turn",
                input_tokens=200, output_tokens=100,
            ),
        ])
        mock_get_llm.return_value = mock_llm

        parent_run_id = str(uuid4())
        executor = AutonomousAgentExecutor(mock_session)
        result = await executor.run(
            agent=mock_agent,
            input_data={"task": "Delegate"},
            run_id=parent_run_id,
        )

        assert result["status"] == "completed"

        # Find AgentRun objects added to session (not AgentRunStep)
        from src.models.orm.agent_runs import AgentRun
        add_calls = mock_session._mock_session.add.call_args_list
        agent_run_adds = [
            c[0][0] for c in add_calls
            if isinstance(c[0][0], AgentRun)
        ]
        assert len(agent_run_adds) >= 1, "Should create a child AgentRun"

        child_run = agent_run_adds[0]
        assert child_run.trigger_type == "delegation"
        assert child_run.parent_run_id is not None
        assert str(child_run.parent_run_id) == parent_run_id
        assert child_run.agent_id == delegated.id

    @pytest.mark.asyncio
    @patch("src.services.execution.autonomous_agent_executor.get_llm_client")
    @patch("src.services.execution.autonomous_agent_executor.resolve_agent_tools")
    async def test_cancellation_check_between_iterations(
        self, mock_resolve_tools, mock_get_llm, mock_session, mock_agent
    ):
        """Executor stops when Redis cancel flag is set between iterations."""
        mock_resolve_tools.return_value = (
            [MagicMock(name="my_tool")],
            {"my_tool": uuid4()},
        )

        # LLM returns tool calls (would loop), but cancel flag stops it
        mock_llm = AsyncMock()
        mock_llm.complete = AsyncMock(side_effect=[
            LLMResponse(
                content=None,
                tool_calls=[ToolCallRequest(id="tc1", name="my_tool", arguments={})],
                finish_reason="tool_use",
                input_tokens=100, output_tokens=50,
            ),
            # Should never reach this — cancelled before second iteration
            LLMResponse(
                content="Should not reach",
                tool_calls=None, finish_reason="end_turn",
                input_tokens=80, output_tokens=40,
            ),
        ])
        mock_get_llm.return_value = mock_llm

        # Mock Redis client to return cancel flag after first iteration
        mock_redis = AsyncMock()
        call_count = 0

        async def mock_get(key):
            nonlocal call_count
            call_count += 1
            # First call: not cancelled (before first iteration)
            # Second call: not cancelled (between tool calls in first iteration)
            # Third call: cancelled (before second iteration)
            if call_count >= 3:
                return "1"
            return None

        mock_redis.get = mock_get

        with patch("src.services.execution.service.execute_tool") as mock_exec_tool:
            mock_exec_tool.return_value = MagicMock(result="ok", status=MagicMock(value="completed"))

            executor = AutonomousAgentExecutor(mock_session, redis_client=mock_redis)
            result = await executor.run(
                agent=mock_agent,
                run_id=str(uuid4()),
            )

        assert result["status"] == "cancelled"
        # Should have only called LLM once (second iteration was cancelled)
        assert mock_llm.complete.call_count == 1

    @pytest.mark.asyncio
    @patch("src.services.execution.autonomous_agent_executor.get_llm_client")
    @patch("src.services.execution.autonomous_agent_executor.resolve_agent_tools")
    async def test_cancellation_without_redis_does_nothing(
        self, mock_resolve_tools, mock_get_llm, mock_session, mock_agent
    ):
        """Without redis_client, cancellation checks return False and execution continues."""
        mock_resolve_tools.return_value = ([], {})

        mock_llm = AsyncMock()
        mock_llm.complete = AsyncMock(return_value=LLMResponse(
            content="Done",
            tool_calls=None, finish_reason="end_turn",
            input_tokens=100, output_tokens=50,
        ))
        mock_get_llm.return_value = mock_llm

        # No redis_client — cancellation checks should be no-ops
        executor = AutonomousAgentExecutor(mock_session, redis_client=None)
        result = await executor.run(
            agent=mock_agent,
            run_id=str(uuid4()),
        )

        assert result["status"] == "completed"
