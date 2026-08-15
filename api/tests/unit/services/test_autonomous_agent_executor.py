"""Unit tests for AutonomousAgentExecutor."""
import asyncio
import json

import pytest
from pydantic_ai.usage import RunUsage
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from tests.unit.services.agent_runtime_fakes import LegacyMockModel
from src.core.constants import SYSTEM_USER_EMAIL, SYSTEM_USER_ID
from src.models.contracts.executions import WorkflowExecutionResponse
from src.models.enums import ExecutionStatus
from src.models.orm.agent_runs import AgentRun
from src.repositories.knowledge import KnowledgeDocument
from src.services.agent_runtime import AgentRunBudget
from src.services.execution.agent_helpers import find_delegated_agent
from src.services.execution.autonomous_agent_executor import (
    AutonomousAgentExecutor,
    DelegationOutcome,
    MAX_DELEGATION_DEPTH,
    ToolError,
)
from src.services.llm.base import LLMConfig, LLMResponse, ToolCallRequest, ToolDefinition


def _tool(name: str) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=f"Test tool {name}",
        parameters={"type": "object", "properties": {}},
    )


@pytest.fixture(autouse=True)
def mock_runtime_config():
    with patch(
        "src.services.execution.autonomous_agent_executor.get_llm_config",
        new_callable=AsyncMock,
        return_value=LLMConfig(
            provider="openai",
            model="test-model",
            api_key="test-key",
        ),
    ):
        yield


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
    async def test_knowledge_search_deduplicates_evidence_across_queries(
        self,
        mock_session,
        mock_agent,
    ):
        mock_agent.knowledge_sources = ["halo_kb"]
        embedding_client = MagicMock()
        embedding_client.embed_single = AsyncMock(return_value=[0.1, 0.2])
        document = KnowledgeDocument(
            id="chunk-1",
            content="Configure Technical POC with Contact Types.",
            namespace="halo_kb",
            score=0.9,
            key="contact-types",
            metadata={"title": "Contact Types"},
        )
        repository = MagicMock()
        repository.search = AsyncMock(return_value=[document])
        executor = AutonomousAgentExecutor(mock_session)

        with patch(
            "src.services.embeddings.get_embedding_client",
            new_callable=AsyncMock,
            return_value=embedding_client,
        ), patch(
            "src.repositories.knowledge.KnowledgeRepository",
            return_value=repository,
        ):
            first = json.loads(
                await executor._execute_knowledge_search(
                    ToolCallRequest(
                        id="first",
                        name="search_knowledge",
                        arguments={"query": "technical poc"},
                    ),
                    mock_agent,
                )
            )
            second = json.loads(
                await executor._execute_knowledge_search(
                    ToolCallRequest(
                        id="second",
                        name="search_knowledge",
                        arguments={"query": "billing contact role"},
                    ),
                    mock_agent,
                )
            )

        assert first["count"] == 1
        assert second["count"] == 0
        assert second["omitted_duplicate_evidence"] == 1
        assert repository.search.call_count == 2

    @pytest.mark.asyncio
    @patch("src.services.execution.autonomous_agent_executor.create_agent_model")
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
        mock_get_llm.return_value = LegacyMockModel(mock_llm)

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
    async def test_run_delegation_persists_chat_child_and_caller(
        self, mock_session, mock_agent
    ):
        delegated = MagicMock()
        delegated.id = uuid4()
        delegated.name = "Chat Specialist"
        delegated.description = "Handles chat work"
        delegated.is_active = True
        delegated.system_prompt = "Do specialist work."
        delegated.tools = []
        delegated.system_tools = []
        delegated.knowledge_sources = []
        delegated.delegated_agents = []
        delegated.max_iterations = 5
        delegated.max_token_budget = 1000
        delegated.llm_model = "cheap-model"
        delegated.llm_max_tokens = 200
        # Global specialists remain valid delegates for an org-scoped parent.
        delegated.organization_id = None
        mock_agent.delegated_agents = [delegated]

        query_result = MagicMock()
        query_result.scalar_one_or_none.return_value = delegated
        mock_session._mock_session.execute = AsyncMock(return_value=query_result)

        created_runs: list[AgentRun] = []

        def capture_add(value):
            if isinstance(value, AgentRun):
                created_runs.append(value)

        mock_session._mock_session.add.side_effect = capture_add

        async def get_created(model, run_id):
            if model is AgentRun and created_runs and created_runs[0].id == run_id:
                return created_runs[0]
            return None

        mock_session._mock_session.get.side_effect = get_created

        conversation_id = uuid4()
        caller = {
            "user_id": str(uuid4()),
            "email": "person@example.com",
            "name": "Person",
        }
        tool_call = ToolCallRequest(
            id="tc1",
            name="delegate_to_chat_specialist",
            arguments={"task": "Investigate the request"},
        )
        executor = AutonomousAgentExecutor(mock_session)
        shared_usage = RunUsage()
        shared_budget = AgentRunBudget(max_requests=10, max_total_tokens=20_000)

        with (
            patch.object(
                AutonomousAgentExecutor,
                "run",
                new_callable=AsyncMock,
                return_value={
                    "output": "Investigation complete",
                    "status": "completed",
                    "iterations_used": 2,
                    "tokens_used": 123,
                    "llm_model": "cheap-model",
                },
            ) as mock_child_run,
            patch(
                "src.services.execution.run_summarizer.enqueue_summarize",
                new_callable=AsyncMock,
            ) as mock_enqueue_summarize,
        ):
            outcome = await executor.run_delegation(
                parent_agent=mock_agent,
                tool_call=tool_call,
                conversation_id=conversation_id,
                caller=caller,
                _shared_usage=shared_usage,
                _shared_budget=shared_budget,
            )

        assert outcome.status == "completed"
        assert outcome.output == "Investigation complete"
        assert len(created_runs) == 1
        child_run = created_runs[0]
        assert child_run.trigger_type == "delegation"
        assert child_run.parent_run_id is None
        assert child_run.conversation_id == conversation_id
        assert child_run.caller_user_id == caller["user_id"]
        assert child_run.caller_email == caller["email"]
        assert child_run.caller_name == caller["name"]
        assert child_run.status == "completed"
        assert child_run.completed_at is not None
        mock_enqueue_summarize.assert_awaited_once_with(child_run.id)
        mock_child_run.assert_awaited_once_with(
            agent=delegated,
            input_data={
                "task": "Investigate the request",
                "_delegated_from": mock_agent.name,
            },
            run_id=str(child_run.id),
            _caller=caller,
            _shared_usage=shared_usage,
            _shared_budget=shared_budget,
        )

    @pytest.mark.asyncio
    async def test_delegated_workflow_inherits_caller_identity(
        self,
        mock_session,
        mock_agent,
    ):
        workflow_id = uuid4()
        caller_user_id = uuid4()
        executor = AutonomousAgentExecutor(mock_session)
        executor._tool_workflow_id_map = {"specialist_tool": workflow_id}
        executor._caller_user_id = caller_user_id
        executor._caller = {
            "user_id": str(caller_user_id),
            "email": "person@example.com",
            "name": "Person",
            "is_platform_admin": True,
        }

        with patch(
            "src.services.execution.service.execute_tool",
            new_callable=AsyncMock,
            return_value=MagicMock(
                execution_id=str(uuid4()),
                status=ExecutionStatus.SUCCESS,
                result={"ok": True},
            ),
        ) as mock_execute_tool:
            result = await executor._execute_tool(
                ToolCallRequest(
                    id="tc1",
                    name="specialist_tool",
                    arguments={"value": 1},
                ),
                mock_agent,
            )

        assert result == '{"ok": true}'
        mock_execute_tool.assert_awaited_once_with(
            workflow_id=str(workflow_id),
            workflow_name="specialist_tool",
            parameters={"value": 1},
            user_id=str(caller_user_id),
            user_email="person@example.com",
            user_name="Person",
            org_id=str(mock_agent.organization_id),
            is_platform_admin=True,
            is_agent=True,
        )

    @pytest.mark.asyncio
    async def test_autonomous_workflow_without_caller_uses_system_identity(
        self,
        mock_session,
        mock_agent,
    ):
        workflow_id = uuid4()
        executor = AutonomousAgentExecutor(mock_session)
        executor._tool_workflow_id_map = {"scheduled_tool": workflow_id}

        with patch(
            "src.services.execution.service.execute_tool",
            new_callable=AsyncMock,
            return_value=MagicMock(
                execution_id=str(uuid4()),
                status=ExecutionStatus.SUCCESS,
                result={"ok": True},
            ),
        ) as mock_execute_tool:
            result = await executor._execute_tool(
                ToolCallRequest(
                    id="tc1",
                    name="scheduled_tool",
                    arguments={"value": 1},
                ),
                mock_agent,
            )

        assert result == '{"ok": true}'
        mock_execute_tool.assert_awaited_once_with(
            workflow_id=str(workflow_id),
            workflow_name="scheduled_tool",
            parameters={"value": 1},
            user_id=SYSTEM_USER_ID,
            user_email=SYSTEM_USER_EMAIL,
            user_name=mock_agent.name,
            org_id=str(mock_agent.organization_id),
            is_platform_admin=False,
            is_agent=True,
        )

    @pytest.mark.asyncio
    async def test_delegated_system_tool_inherits_caller_identity(
        self,
        mock_session,
        mock_agent,
    ):
        caller_user_id = uuid4()
        captured_context = None

        async def system_tool(context, **_arguments):
            nonlocal captured_context
            captured_context = context
            return "ok"

        executor = AutonomousAgentExecutor(mock_session)
        executor._caller_user_id = caller_user_id
        executor._caller = {
            "user_id": str(caller_user_id),
            "email": "person@example.com",
            "name": "Person",
            "is_platform_admin": True,
        }

        with patch(
            "src.services.mcp_server.server.get_system_tool_function",
            return_value=system_tool,
        ):
            await executor._execute_system_tool(
                ToolCallRequest(
                    id="tc1",
                    name="specialist_system_tool",
                    arguments={},
                ),
                mock_agent,
            )

        assert captured_context is not None
        assert captured_context.user_id == caller_user_id
        assert captured_context.user_email == "person@example.com"
        assert captured_context.user_name == "Person"
        assert captured_context.is_platform_admin is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("child_result", "expected_status", "expected_error"),
        [
            (
                {
                    "output": None,
                    "status": "failed",
                    "error": "Provider unavailable",
                },
                "failed",
                "Provider unavailable",
            ),
            (
                {
                    "output": None,
                    "status": "cancelled",
                },
                "cancelled",
                "was cancelled",
            ),
            (
                {
                    "output": None,
                    "status": "paused",
                    "message": "Specialist is paused",
                },
                "paused",
                "Specialist is paused",
            ),
            (
                {
                    "output": None,
                    "status": "budget_exceeded",
                },
                "budget_exceeded",
                "exceeded its budget",
            ),
        ],
    )
    async def test_run_delegation_terminalizes_non_success_statuses(
        self,
        mock_session,
        mock_agent,
        child_result,
        expected_status,
        expected_error,
    ):
        delegated = MagicMock()
        delegated.id = uuid4()
        delegated.name = "Lifecycle Specialist"
        delegated.is_active = True
        delegated.tools = []
        delegated.delegated_agents = []
        delegated.max_iterations = 5
        delegated.max_token_budget = 1000
        delegated.organization_id = mock_agent.organization_id
        mock_agent.delegated_agents = [delegated]

        query_result = MagicMock()
        query_result.scalar_one_or_none.return_value = delegated
        mock_session._mock_session.execute = AsyncMock(return_value=query_result)

        created_runs: list[AgentRun] = []
        mock_session._mock_session.add.side_effect = (
            lambda value: created_runs.append(value)
            if isinstance(value, AgentRun)
            else None
        )
        mock_session._mock_session.get.side_effect = (
            lambda model, _run_id: created_runs[0]
            if model is AgentRun and created_runs
            else None
        )

        executor = AutonomousAgentExecutor(mock_session)
        tool_call = ToolCallRequest(
            id="tc1",
            name="delegate_to_lifecycle_specialist",
            arguments={"task": "Exercise lifecycle"},
        )

        with patch.object(
            AutonomousAgentExecutor,
            "run",
            new_callable=AsyncMock,
            return_value={
                **child_result,
                "iterations_used": 1,
                "tokens_used": 10,
                "llm_model": "cheap-model",
            },
        ):
            outcome = await executor.run_delegation(
                parent_agent=mock_agent,
                tool_call=tool_call,
                parent_run_id=str(uuid4()),
            )

        assert outcome.status == expected_status
        assert expected_error in (outcome.error or "")
        assert created_runs[0].status == expected_status
        assert expected_error in (created_runs[0].error or "")
        assert created_runs[0].completed_at is not None

    @pytest.mark.asyncio
    async def test_run_delegation_terminalizes_unexpected_exception(
        self, mock_session, mock_agent
    ):
        delegated = MagicMock()
        delegated.id = uuid4()
        delegated.name = "Crasher"
        delegated.is_active = True
        delegated.tools = []
        delegated.delegated_agents = []
        delegated.max_iterations = 5
        delegated.max_token_budget = 1000
        delegated.organization_id = mock_agent.organization_id
        mock_agent.delegated_agents = [delegated]

        query_result = MagicMock()
        query_result.scalar_one_or_none.return_value = delegated
        mock_session._mock_session.execute = AsyncMock(return_value=query_result)
        created_runs: list[AgentRun] = []
        mock_session._mock_session.add.side_effect = (
            lambda value: created_runs.append(value)
            if isinstance(value, AgentRun)
            else None
        )
        mock_session._mock_session.get.side_effect = (
            lambda model, _run_id: created_runs[0]
            if model is AgentRun and created_runs
            else None
        )

        executor = AutonomousAgentExecutor(mock_session)
        with patch.object(
            AutonomousAgentExecutor,
            "run",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Connection lost"),
        ):
            outcome = await executor.run_delegation(
                parent_agent=mock_agent,
                tool_call=ToolCallRequest(
                    id="tc1",
                    name="delegate_to_crasher",
                    arguments={"task": "Crash"},
                ),
                parent_run_id=str(uuid4()),
            )

        assert outcome.status == "failed"
        assert outcome.error == "Connection lost"
        assert created_runs[0].status == "failed"
        assert created_runs[0].error == "Connection lost"
        assert created_runs[0].completed_at is not None

    @pytest.mark.asyncio
    async def test_run_delegation_rejects_cross_org_target(
        self, mock_session, mock_agent
    ):
        delegated = MagicMock()
        delegated.id = uuid4()
        delegated.name = "Other Org Specialist"
        delegated.is_active = True
        delegated.organization_id = uuid4()
        mock_agent.delegated_agents = [delegated]

        executor = AutonomousAgentExecutor(mock_session)
        with pytest.raises(ToolError, match="outside the parent agent's organization"):
            await executor.run_delegation(
                parent_agent=mock_agent,
                tool_call=ToolCallRequest(
                    id="tc1",
                    name="delegate_to_other_org_specialist",
                    arguments={"task": "Cross a boundary"},
                ),
                parent_run_id=str(uuid4()),
            )

        mock_session._mock_session.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_delegation_revalidates_current_link_and_scope(
        self,
        mock_session,
        mock_agent,
    ):
        delegated = MagicMock()
        delegated.id = uuid4()
        delegated.name = "Former Specialist"
        delegated.is_active = True
        delegated.organization_id = mock_agent.organization_id
        mock_agent.delegated_agents = [delegated]

        query_result = MagicMock()
        query_result.scalar_one_or_none.return_value = None
        mock_session._mock_session.execute = AsyncMock(return_value=query_result)

        executor = AutonomousAgentExecutor(mock_session)
        with pytest.raises(
            ToolError,
            match="no longer active, authorized, or in scope",
        ):
            await executor.run_delegation(
                parent_agent=mock_agent,
                tool_call=ToolCallRequest(
                    id="tc1",
                    name="delegate_to_former_specialist",
                    arguments={"task": "Use a stale parent relationship"},
                ),
                parent_run_id=str(uuid4()),
            )

        statement = mock_session._mock_session.execute.await_args.args[0]
        compiled = str(statement)
        assert "JOIN agent_delegations" in compiled
        assert "agent_delegations.parent_agent_id" in compiled
        assert "FOR UPDATE" in compiled
        mock_session._mock_session.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_delegation_terminalizes_then_reraises_parent_cancellation(
        self, mock_session, mock_agent
    ):
        delegated = MagicMock()
        delegated.id = uuid4()
        delegated.name = "Cancelled Specialist"
        delegated.is_active = True
        delegated.tools = []
        delegated.delegated_agents = []
        delegated.max_iterations = 5
        delegated.max_token_budget = 1000
        delegated.organization_id = mock_agent.organization_id
        mock_agent.delegated_agents = [delegated]

        query_result = MagicMock()
        query_result.scalar_one_or_none.return_value = delegated
        mock_session._mock_session.execute = AsyncMock(return_value=query_result)
        created_runs: list[AgentRun] = []
        mock_session._mock_session.add.side_effect = (
            lambda value: created_runs.append(value)
            if isinstance(value, AgentRun)
            else None
        )
        mock_session._mock_session.get.side_effect = (
            lambda model, _run_id: created_runs[0]
            if model is AgentRun and created_runs
            else None
        )

        executor = AutonomousAgentExecutor(mock_session)
        with patch.object(
            AutonomousAgentExecutor,
            "run",
            new_callable=AsyncMock,
            side_effect=asyncio.CancelledError(),
        ), pytest.raises(asyncio.CancelledError):
            await executor.run_delegation(
                parent_agent=mock_agent,
                tool_call=ToolCallRequest(
                    id="tc1",
                    name="delegate_to_cancelled_specialist",
                    arguments={"task": "Wait"},
                ),
                parent_run_id=str(uuid4()),
            )

        assert created_runs[0].status == "cancelled"
        assert created_runs[0].completed_at is not None

    @pytest.mark.asyncio
    async def test_child_checks_ancestor_cancel_flags(self, mock_session):
        child_run_id = str(uuid4())
        root_run_id = str(uuid4())
        redis = AsyncMock()
        redis.get = AsyncMock(side_effect=[None, b"1"])
        executor = AutonomousAgentExecutor(
            mock_session,
            redis_client=redis,
            _ancestor_run_ids=(root_run_id,),
        )

        assert await executor._check_cancelled(child_run_id) is True
        assert redis.get.await_args_list == [
            ((f"bifrost:agent_run:{child_run_id}:cancel",),),
            ((f"bifrost:agent_run:{root_run_id}:cancel",),),
        ]

    @pytest.mark.asyncio
    async def test_autonomous_delegation_raises_tool_error_for_failed_child(
        self, mock_session, mock_agent
    ):
        executor = AutonomousAgentExecutor(mock_session)
        executor._current_run_id = str(uuid4())
        executor._caller = {"user_id": str(uuid4())}
        failed = DelegationOutcome(
            child_run_id=uuid4(),
            agent_name="Broken Specialist",
            status="failed",
            output=None,
            error="Provider unavailable",
            duration_ms=12,
        )
        executor.run_delegation = AsyncMock(return_value=failed)

        with pytest.raises(ToolError, match="Provider unavailable"):
            await executor._execute_delegation(
                ToolCallRequest(
                    id="tc1",
                    name="delegate_to_broken_specialist",
                    arguments={"task": "Do work"},
                ),
                mock_agent,
            )

    @pytest.mark.asyncio
    @patch("src.services.execution.autonomous_agent_executor.create_agent_model")
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
        mock_get_llm.return_value = LegacyMockModel(mock_llm)

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
    @patch("src.services.execution.autonomous_agent_executor.create_agent_model")
    @patch("src.services.execution.autonomous_agent_executor.resolve_agent_tools")
    async def test_run_with_tool_calls(self, mock_resolve_tools, mock_get_llm, mock_session, mock_agent):
        """Run executes tools and continues the loop until no more tool calls."""
        workflow_id = uuid4()
        workflow_execution_id = str(uuid4())
        mock_agent.system_tools = ["system_tool"]
        mock_resolve_tools.return_value = (
            [_tool("my_tool"), _tool("system_tool")],
            {"my_tool": workflow_id},
        )

        # First call returns a tool call, second call returns final content
        mock_llm = AsyncMock()
        mock_llm.complete = AsyncMock(side_effect=[
            LLMResponse(
                content=None,
                tool_calls=[
                    ToolCallRequest(id="tc1", name="my_tool", arguments={"x": 1}),
                    ToolCallRequest(id="tc2", name="system_tool", arguments={}),
                ],
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
        mock_get_llm.return_value = LegacyMockModel(mock_llm)

        # Mock the workflow tool execution (imported inside _execute_tool)
        with patch("src.services.execution.service.execute_tool") as mock_exec_tool:
            mock_exec_tool.return_value = WorkflowExecutionResponse(
                execution_id=workflow_execution_id,
                status=ExecutionStatus.SUCCESS,
                result="tool output",
            )

            executor = AutonomousAgentExecutor(mock_session)
            executor._execute_system_tool = AsyncMock(return_value="system output")
            result = await executor.run(
                agent=mock_agent,
                input_data={"task": "do something"},
                run_id=str(uuid4()),
            )

        assert result["status"] == "completed"
        assert result["output"] == "Final answer"
        assert result["iterations_used"] == 2
        assert result["tokens_used"] == 450  # 150 + 300
        tool_result_steps = {
            step["content"]["tool_name"]: step["content"]
            for step in executor._pending_steps
            if step["type"] == "tool_result"
        }
        assert tool_result_steps["my_tool"]["execution_id"] == workflow_execution_id
        assert tool_result_steps["my_tool"]["is_error"] is False
        assert "execution_id" not in tool_result_steps["system_tool"]
        assert tool_result_steps["system_tool"]["is_error"] is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "workflow_status",
        [
            ExecutionStatus.FAILED,
            ExecutionStatus.TIMEOUT,
            ExecutionStatus.CANCELLED,
            ExecutionStatus.COMPLETED_WITH_ERRORS,
        ],
    )
    @patch("src.services.execution.autonomous_agent_executor.create_agent_model")
    @patch("src.services.execution.autonomous_agent_executor.resolve_agent_tools")
    async def test_run_marks_terminal_workflow_failures_as_errored_results(
        self,
        mock_resolve_tools,
        mock_get_llm,
        mock_session,
        mock_agent,
        workflow_status,
    ):
        """Terminal non-success workflow responses retain their ID and record an error."""
        workflow_execution_id = str(uuid4())
        mock_resolve_tools.return_value = (
            [_tool("my_tool")],
            {"my_tool": uuid4()},
        )

        mock_llm = AsyncMock()
        mock_llm.complete = AsyncMock(
            side_effect=[
                LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCallRequest(
                            id="tc1",
                            name="my_tool",
                            arguments={},
                        )
                    ],
                    finish_reason="tool_use",
                    input_tokens=10,
                    output_tokens=5,
                ),
                LLMResponse(
                    content="Handled failure",
                    tool_calls=None,
                    finish_reason="end_turn",
                    input_tokens=10,
                    output_tokens=5,
                ),
            ]
        )
        mock_get_llm.return_value = LegacyMockModel(mock_llm)

        with patch("src.services.execution.service.execute_tool") as mock_exec_tool:
            mock_exec_tool.return_value = WorkflowExecutionResponse(
                execution_id=workflow_execution_id,
                status=workflow_status,
                error=f"Workflow ended with {workflow_status.value}",
            )

            executor = AutonomousAgentExecutor(mock_session)
            await executor.run(agent=mock_agent, run_id=str(uuid4()))

        tool_result = next(
            step["content"]
            for step in executor._pending_steps
            if step["type"] == "tool_result"
        )
        assert tool_result["execution_id"] == workflow_execution_id
        assert tool_result["is_error"] is True
        assert tool_result["result"].startswith("Error:")

    @pytest.mark.asyncio
    @patch("src.services.execution.autonomous_agent_executor.create_agent_model")
    @patch("src.services.execution.autonomous_agent_executor.resolve_agent_tools")
    async def test_run_reserves_final_iteration_for_handoff(self, mock_resolve_tools, mock_get_llm, mock_session, mock_agent):
        """The final allowed request completes without executing another tool."""
        mock_agent.max_iterations = 2
        mock_resolve_tools.return_value = (
            [_tool("my_tool")],
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
        mock_get_llm.return_value = LegacyMockModel(mock_llm)

        with patch("src.services.execution.service.execute_tool") as mock_exec_tool:
            mock_exec_tool.return_value = MagicMock(result="ok", status=MagicMock(value="completed"))

            executor = AutonomousAgentExecutor(mock_session)
            result = await executor.run(
                agent=mock_agent,
                run_id=str(uuid4()),
            )

        assert result["status"] == "completed"
        assert result["iterations_used"] == 2
        assert "configured run budget" in str(result["output"])

    @pytest.mark.asyncio
    @patch("src.services.execution.autonomous_agent_executor.create_agent_model")
    @patch("src.services.execution.autonomous_agent_executor.resolve_agent_tools")
    async def test_run_handles_llm_error(self, mock_resolve_tools, mock_get_llm, mock_session, mock_agent):
        """Run returns failed status when LLM call raises."""
        mock_resolve_tools.return_value = ([], {})

        mock_llm = AsyncMock()
        mock_llm.complete = AsyncMock(side_effect=RuntimeError("API timeout"))
        mock_get_llm.return_value = LegacyMockModel(mock_llm)

        executor = AutonomousAgentExecutor(mock_session)
        result = await executor.run(
            agent=mock_agent,
            run_id=str(uuid4()),
        )

        assert result["status"] == "failed"
        assert "API timeout" in result["error"]
        assert result["output"] is None

    @pytest.mark.asyncio
    @patch("src.services.execution.autonomous_agent_executor.create_agent_model")
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
        mock_get_llm.return_value = LegacyMockModel(mock_llm)

        executor = AutonomousAgentExecutor(mock_session)
        result = await executor.run(
            agent=mock_agent,
            output_schema={"type": "object", "properties": {"result": {"type": "integer"}}},
            run_id=str(uuid4()),
        )

        assert result["status"] == "completed"
        assert result["output"] == {"result": 42}

    @pytest.mark.asyncio
    @patch("src.services.execution.autonomous_agent_executor.create_agent_model")
    @patch("src.services.execution.autonomous_agent_executor.resolve_agent_tools")
    async def test_run_handles_tool_execution_error(
        self, mock_resolve_tools, mock_get_llm, mock_session, mock_agent
    ):
        """Tool execution errors are caught and fed back to the LLM."""
        workflow_id = uuid4()
        mock_resolve_tools.return_value = (
            [_tool("broken_tool")],
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
        mock_get_llm.return_value = LegacyMockModel(mock_llm)

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
    @patch("src.services.execution.autonomous_agent_executor.create_agent_model")
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
            [_tool("delegate_to_sub_agent")],
            {},
        )

        # Mock session.execute to return the re-fetched delegated agent
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = delegated
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
        mock_get_llm.return_value = LegacyMockModel(mock_llm)

        executor = AutonomousAgentExecutor(mock_session)
        result = await executor.run(
            agent=mock_agent,
            input_data={"task": "Delegate work"},
            run_id=str(uuid4()),
        )

        assert result["status"] == "completed"
        assert "Sub agent summary" in result["output"]

    @pytest.mark.asyncio
    @patch("src.services.execution.autonomous_agent_executor.create_agent_model")
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
            [_tool("delegate_to_troubleshooting_agent")],
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
        mock_get_llm.return_value = LegacyMockModel(mock_llm)

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
    @patch("src.services.execution.autonomous_agent_executor.create_agent_model")
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
            [_tool("delegate_to_sub_agent")],
            {},
        )

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = delegated
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
        mock_get_llm.return_value = LegacyMockModel(mock_llm)

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
    @patch("src.services.execution.autonomous_agent_executor.create_agent_model")
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
            [_tool("delegate_to_deep_agent")],
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
        mock_get_llm.return_value = LegacyMockModel(mock_llm)

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
        # The runtime returned the depth-limit result to the model, which then
        # made the bounded follow-up request instead of spawning a child.
        assert mock_llm.complete.call_count == 2

    @pytest.mark.asyncio
    @patch("src.services.execution.autonomous_agent_executor.create_agent_model")
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
            [_tool("delegate_to_slow_agent")],
            {},
        )

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = delegated
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
        mock_get_llm.return_value = LegacyMockModel(mock_llm)

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
    @patch("src.services.execution.autonomous_agent_executor.create_agent_model")
    @patch("src.services.execution.autonomous_agent_executor.resolve_agent_tools")
    async def test_unknown_tool_gets_one_bounded_model_retry(
        self, mock_resolve_tools, mock_get_llm, mock_session, mock_agent
    ):
        """A hallucinated tool name gets one budgeted correction request."""
        mock_resolve_tools.return_value = (
            [_tool("some_tool")],
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
        mock_get_llm.return_value = LegacyMockModel(mock_llm)

        executor = AutonomousAgentExecutor(mock_session)
        result = await executor.run(
            agent=mock_agent,
            run_id=str(uuid4()),
        )

        assert result["status"] == "completed"
        assert result["output"] == "Recovered"
        assert mock_llm.complete.call_count == 2
        assert not any(
            step["type"] in {"tool_call", "tool_error"}
            for step in executor._pending_steps
        )

    @pytest.mark.asyncio
    @patch("src.services.execution.autonomous_agent_executor.create_agent_model")
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
            [_tool("delegate_to_child_agent")],
            {},
        )

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = delegated
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
        mock_get_llm.return_value = LegacyMockModel(mock_llm)

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
    @patch("src.services.execution.autonomous_agent_executor.create_agent_model")
    @patch("src.services.execution.autonomous_agent_executor.resolve_agent_tools")
    async def test_cancellation_check_between_iterations(
        self, mock_resolve_tools, mock_get_llm, mock_session, mock_agent
    ):
        """Executor stops when Redis cancel flag is set between iterations."""
        mock_resolve_tools.return_value = (
            [_tool("my_tool")],
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
        mock_get_llm.return_value = LegacyMockModel(mock_llm)

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
    @patch("src.services.execution.autonomous_agent_executor.create_agent_model")
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
        mock_get_llm.return_value = LegacyMockModel(mock_llm)

        # No redis_client — cancellation checks should be no-ops
        executor = AutonomousAgentExecutor(mock_session, redis_client=None)
        result = await executor.run(
            agent=mock_agent,
            run_id=str(uuid4()),
        )

        assert result["status"] == "completed"
