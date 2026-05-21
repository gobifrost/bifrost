"""
Unit tests for AgentExecutor tool handling.

Tests cover:
- Tool conflict detection
- Automatic search_knowledge addition
- Notification creation for conflicts
- JSON serialization of tool results
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from pydantic import BaseModel

from src.services.agent_executor import AgentExecutor, _serialize_for_json
from src.services.llm import ToolDefinition


@pytest.fixture
def mock_session():
    """Mock database session used inside the factory context manager."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.get = AsyncMock(return_value=None)
    return session


@pytest.fixture
def mock_session_factory(mock_session):
    """Mock database session factory that yields mock_session."""
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return factory


@pytest.fixture
def executor(mock_session_factory):
    """Create an AgentExecutor instance with mocked session factory."""
    return AgentExecutor(mock_session_factory)


@pytest.fixture
def mock_agent():
    """Create a mock agent with tools."""
    agent = MagicMock()
    agent.id = uuid4()
    agent.name = "Test Agent"
    agent.tools = []
    agent.system_tools = []
    agent.knowledge_sources = []
    return agent


class TestAutoAddSearchKnowledge:
    """Test automatic addition of search_knowledge system tool."""

    @pytest.mark.asyncio
    async def test_search_knowledge_added_when_agent_has_knowledge_sources(
        self, executor, mock_agent
    ):
        """search_knowledge is auto-added when agent has knowledge_sources."""
        mock_agent.knowledge_sources = ["docs", "faq"]
        mock_agent.system_tools = ["list_organizations"]

        mock_tools = [
            ToolDefinition(name="list_organizations", description="List all orgs", parameters={"type": "object", "properties": {}}),
            ToolDefinition(name="search_knowledge", description="Search the knowledge base", parameters={"type": "object", "properties": {}}),
        ]

        with patch("src.services.agent_executor.resolve_agent_tools", new_callable=AsyncMock, return_value=(mock_tools, {})):
            tools = await executor._get_agent_tools(mock_agent)

        tool_names = [t.name for t in tools]
        assert "search_knowledge" in tool_names
        assert "list_organizations" in tool_names

    @pytest.mark.asyncio
    async def test_search_knowledge_not_duplicated_if_already_in_system_tools(
        self, executor, mock_agent
    ):
        """search_knowledge is not added twice if already in system_tools."""
        mock_agent.knowledge_sources = ["docs"]
        mock_agent.system_tools = ["search_knowledge"]

        mock_tools = [
            ToolDefinition(name="search_knowledge", description="Search the knowledge base", parameters={"type": "object", "properties": {}}),
        ]

        with patch("src.services.agent_executor.resolve_agent_tools", new_callable=AsyncMock, return_value=(mock_tools, {})):
            tools = await executor._get_agent_tools(mock_agent)

        tool_names = [t.name for t in tools]
        assert tool_names.count("search_knowledge") == 1

    @pytest.mark.asyncio
    async def test_no_search_knowledge_when_no_knowledge_sources(
        self, executor, mock_agent
    ):
        """search_knowledge is not added when agent has no knowledge_sources."""
        mock_agent.knowledge_sources = []
        mock_agent.system_tools = ["list_organizations"]

        mock_tools = [
            ToolDefinition(name="list_organizations", description="List all orgs", parameters={"type": "object", "properties": {}}),
        ]

        with patch("src.services.agent_executor.resolve_agent_tools", new_callable=AsyncMock, return_value=(mock_tools, {})):
            tools = await executor._get_agent_tools(mock_agent)

        tool_names = [t.name for t in tools]
        assert "search_knowledge" not in tool_names


class TestToolConflictDetection:
    """Test detection and handling of tool name conflicts via resolve_agent_tools."""

    @pytest.mark.asyncio
    async def test_system_tools_win_over_workflow_tools(self, executor, mock_agent):
        """System tools take priority — resolve_agent_tools returns only the system tool."""
        mock_agent.system_tools = ["execute_workflow"]

        # resolve_agent_tools handles the conflict internally and returns only the winner
        mock_tools = [
            ToolDefinition(name="execute_workflow", description="Execute a workflow", parameters={"type": "object", "properties": {}}),
        ]

        with patch("src.services.agent_executor.resolve_agent_tools", new_callable=AsyncMock, return_value=(mock_tools, {})):
            tools = await executor._get_agent_tools(mock_agent)

        tool_names = [t.name for t in tools]
        assert tool_names.count("execute_workflow") == 1

    @pytest.mark.asyncio
    async def test_no_conflict_with_prefixed_workflow_tools(self, executor, mock_agent):
        """Workflow tools with category prefix don't conflict with system tools."""
        mock_agent.system_tools = ["execute_workflow"]

        workflow_id = uuid4()
        mock_tools = [
            ToolDefinition(name="execute_workflow", description="Execute a workflow", parameters={"type": "object", "properties": {}}),
            ToolDefinition(name="halopsa_execute_workflow", description="HaloPSA workflow", parameters={"type": "object", "properties": {}}),
        ]

        with patch("src.services.agent_executor.resolve_agent_tools", new_callable=AsyncMock, return_value=(mock_tools, {"halopsa_execute_workflow": workflow_id})):
            tools = await executor._get_agent_tools(mock_agent)

        tool_names = [t.name for t in tools]
        assert "execute_workflow" in tool_names
        assert "halopsa_execute_workflow" in tool_names


class TestNotifyToolConflicts:
    """Test tool conflict notification creation."""

    @pytest.mark.asyncio
    async def test_notification_created_for_conflicts(self, executor, mock_agent):
        """Notification is created when tools conflict."""
        conflicts = [
            ("search_knowledge", "workflow 'Search Knowledge'", "system tool 'search_knowledge'"),
        ]

        with patch(
            "src.services.notification_service.get_notification_service"
        ) as mock_get_service:
            mock_service = MagicMock()
            mock_service.create_notification = AsyncMock()
            mock_get_service.return_value = mock_service

            await executor._notify_tool_conflicts(mock_agent, conflicts)

            mock_service.create_notification.assert_called_once()
            call_args = mock_service.create_notification.call_args

            # Verify notification properties
            assert call_args.kwargs["user_id"] == "system"
            assert call_args.kwargs["for_admins"] is True

            request = call_args.kwargs["request"]
            assert mock_agent.name in request.title
            assert "search_knowledge" in request.description
            assert request.metadata["agent_id"] == str(mock_agent.id)

    @pytest.mark.asyncio
    async def test_notification_failure_does_not_raise(self, executor, mock_agent):
        """Notification failure doesn't break agent tool loading."""
        conflicts = [
            ("test_tool", "workflow 'Test'", "system tool 'test_tool'"),
        ]

        with patch(
            "src.services.notification_service.get_notification_service"
        ) as mock_get_service:
            mock_service = MagicMock()
            mock_service.create_notification = AsyncMock(
                side_effect=Exception("Redis connection failed")
            )
            mock_get_service.return_value = mock_service

            # Should not raise
            await executor._notify_tool_conflicts(mock_agent, conflicts)

    @pytest.mark.asyncio
    async def test_notification_description_truncated_if_too_long(
        self, executor, mock_agent
    ):
        """Long conflict descriptions are truncated."""
        # Create many conflicts to exceed 500 char limit
        conflicts = [
            (f"tool_{i}", f"workflow 'Very Long Workflow Name {i}'", f"system tool 'tool_{i}'")
            for i in range(20)
        ]

        with patch(
            "src.services.notification_service.get_notification_service"
        ) as mock_get_service:
            mock_service = MagicMock()
            mock_service.create_notification = AsyncMock()
            mock_get_service.return_value = mock_service

            await executor._notify_tool_conflicts(mock_agent, conflicts)

            call_args = mock_service.create_notification.call_args
            request = call_args.kwargs["request"]

            # Description should be truncated to ~500 chars
            assert len(request.description) <= 500
            assert request.description.endswith("...")


class TestWorkflowToolIdResolution:
    """Test that workflow tools with normalized names resolve back to workflows by ID."""

    @pytest.mark.asyncio
    async def test_workflow_id_map_populated_for_workflow_tools(self, executor, mock_agent):
        """_get_agent_tools populates _tool_workflow_id_map for workflow tools."""
        workflow_id = uuid4()
        mock_agent.tools = [MagicMock(id=workflow_id)]
        mock_agent.system_tools = []

        mock_tools = [
            ToolDefinition(name="wf_execute_halopsa_sql", description="Execute HaloPSA SQL query", parameters={"type": "object", "properties": {}}),
        ]
        mock_id_map = {"wf_execute_halopsa_sql": workflow_id}

        with patch("src.services.agent_executor.resolve_agent_tools", new_callable=AsyncMock, return_value=(mock_tools, mock_id_map)):
            tools = await executor._get_agent_tools(mock_agent)

        assert "wf_execute_halopsa_sql" in [t.name for t in tools]
        assert executor._tool_workflow_id_map["wf_execute_halopsa_sql"] == workflow_id

    @pytest.mark.asyncio
    async def test_execute_tool_uses_id_lookup_for_normalized_names(self, executor, mock_session):
        """_execute_tool looks up workflows by ID when name is in _tool_workflow_id_map."""
        from src.services.llm.base import ToolCallRequest

        workflow_id = uuid4()
        executor._tool_workflow_id_map["wf_execute_halopsa_sql"] = workflow_id

        # Mock the DB query to return a workflow
        mock_workflow = MagicMock()
        mock_workflow.id = workflow_id
        mock_workflow.name = "Execute HaloPSA SQL"

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_workflow
        mock_session.execute = AsyncMock(return_value=mock_result)

        tool_call = ToolCallRequest(
            id="call_123",
            name="wf_execute_halopsa_sql",
            arguments={"query": "SELECT 1"},
        )

        with patch("src.services.execution.service.execute_tool", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = MagicMock(
                execution_id="exec_1",
                status="completed",
                result={"data": []},
            )

            mock_conversation = MagicMock()
            mock_conversation.user = MagicMock()
            mock_conversation.user.id = uuid4()
            mock_conversation.user.name = "Test User"

            mock_agent = MagicMock()
            mock_agent.organization_id = uuid4()

            await executor._execute_tool(
                tool_call, agent=mock_agent, conversation=mock_conversation
            )

        # Verify the DB query used Workflow.id, not Workflow.name
        call_args = mock_session.execute.call_args
        query = call_args[0][0]
        # The compiled query should reference the workflow ID, not the normalized name
        compiled = str(query.compile(compile_kwargs={"literal_binds": True}))
        assert workflow_id.hex in compiled
        assert "wf_execute_halopsa_sql" not in compiled

    @pytest.mark.asyncio
    async def test_execute_tool_falls_back_to_name_lookup(self, executor, mock_session):
        """_execute_tool falls back to name-based lookup when tool not in ID map."""
        from src.services.llm.base import ToolCallRequest

        # Don't populate _tool_workflow_id_map — simulate an unknown tool
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        tool_call = ToolCallRequest(
            id="call_456",
            name="some_unknown_tool",
            arguments={},
        )

        result = await executor._execute_tool(tool_call)

        assert result.error == "Tool 'some_unknown_tool' not found"

        # Verify the DB query used Workflow.name (fallback path)
        call_args = mock_session.execute.call_args
        query = call_args[0][0]
        compiled = str(query.compile(compile_kwargs={"literal_binds": True}))
        assert "some_unknown_tool" in compiled


class TestPrivilegedAgentManagementTools:
    """Regression tests for privileged MCP agent-management tools in chat."""

    @pytest.mark.asyncio
    async def test_chat_agent_cannot_execute_privileged_agent_management_tool(
        self, executor, mock_agent
    ):
        """Even if present on agent.system_tools, chat cannot execute create_agent."""
        from src.services.llm.base import ToolCallRequest

        mock_agent.system_tools = ["create_agent"]

        with patch.object(
            executor,
            "_execute_system_tool",
            new_callable=AsyncMock,
        ) as mock_execute_system_tool:
            result = await executor._execute_tool(
                ToolCallRequest(
                    id="call_privileged",
                    name="create_agent",
                    arguments={
                        "name": "Escalated",
                        "system_prompt": "Grant privileged tools",
                    },
                ),
                agent=mock_agent,
                conversation=MagicMock(),
            )

        mock_execute_system_tool.assert_not_awaited()
        assert result.error is not None
        assert "cannot be executed from chat agents" in result.error
        assert result.tool_name == "create_agent"

    @pytest.mark.asyncio
    async def test_chat_agent_can_execute_non_privileged_system_tool(
        self, executor, mock_agent
    ):
        """The chat denylist does not block ordinary assigned system tools."""
        from src.services.llm.base import ToolCallRequest

        mock_agent.system_tools = ["list_workflows"]
        expected = MagicMock()

        with patch.object(
            executor,
            "_execute_system_tool",
            new_callable=AsyncMock,
            return_value=expected,
        ) as mock_execute_system_tool:
            result = await executor._execute_tool(
                ToolCallRequest(
                    id="call_allowed",
                    name="list_workflows",
                    arguments={},
                ),
                agent=mock_agent,
                conversation=MagicMock(),
            )

        mock_execute_system_tool.assert_awaited_once()
        assert result is expected

    @pytest.mark.asyncio
    async def test_system_tool_context_uses_caller_org_not_agent_org(self, executor):
        """System tools inherit caller tenant scope even when the agent is global/foreign."""
        from src.services.llm.base import ToolCallRequest
        from src.services.mcp_server.tool_result import success_result

        caller_org_id = uuid4()
        agent_org_id = uuid4()
        seen_context = None

        async def fake_tool(context, **_kwargs):
            nonlocal seen_context
            seen_context = context
            return success_result("ok", {"ok": True})

        conversation = MagicMock()
        conversation.user = MagicMock()
        conversation.user.id = uuid4()
        conversation.user.organization_id = caller_org_id
        conversation.user.is_superuser = False
        conversation.user.email = "user@example.com"
        conversation.user.name = "User"

        agent = MagicMock()
        agent.organization_id = agent_org_id

        with patch(
            "src.services.mcp_server.server.get_system_tool_function",
            return_value=fake_tool,
        ):
            result = await executor._execute_system_tool(
                ToolCallRequest(
                    id="call_allowed",
                    name="list_workflows",
                    arguments={},
                ),
                agent=agent,
                conversation=conversation,
            )

        assert result.error is None
        assert seen_context is not None
        assert seen_context.org_id == caller_org_id
        assert seen_context.org_id != agent_org_id


class TestSerializeForJson:
    """Test the _serialize_for_json helper function."""

    def test_none_returns_empty_string(self):
        """None values return empty string."""
        assert _serialize_for_json(None) == ""

    def test_string_returns_as_is(self):
        """String values are returned unchanged."""
        assert _serialize_for_json("hello world") == "hello world"
        assert _serialize_for_json('{"key": "value"}') == '{"key": "value"}'

    def test_dict_serializes_to_json(self):
        """Dictionaries are serialized to JSON."""
        result = _serialize_for_json({"key": "value", "number": 42})
        assert '"key":"value"' in result or '"key": "value"' in result
        assert "42" in result

    def test_list_serializes_to_json(self):
        """Lists are serialized to JSON."""
        result = _serialize_for_json([1, 2, 3])
        assert result == "[1,2,3]"

    def test_pydantic_model_serializes(self):
        """Pydantic models are properly serialized."""

        class SampleModel(BaseModel):
            text: str
            count: int

        model = SampleModel(text="hello", count=5)
        result = _serialize_for_json(model)

        assert "hello" in result
        assert "5" in result

    def test_nested_pydantic_models_serialize(self):
        """Nested Pydantic models are properly serialized."""

        class Inner(BaseModel):
            value: str

        class Outer(BaseModel):
            inner: Inner
            name: str

        model = Outer(inner=Inner(value="nested"), name="outer")
        result = _serialize_for_json(model)

        assert "nested" in result
        assert "outer" in result

    def test_list_of_pydantic_models_serializes(self):
        """Lists of Pydantic models are properly serialized."""

        class TextContent(BaseModel):
            type: str
            text: str

        content = [
            TextContent(type="text", text="Hello"),
            TextContent(type="text", text="World"),
        ]
        result = _serialize_for_json(content)

        assert "Hello" in result
        assert "World" in result
        assert "text" in result

    def test_mixed_content_serializes(self):
        """Mixed content with Pydantic and primitives serializes."""

        class Item(BaseModel):
            name: str

        data = {
            "items": [Item(name="first"), Item(name="second")],
            "count": 2,
            "active": True,
        }
        result = _serialize_for_json(data)

        assert "first" in result
        assert "second" in result
        assert "2" in result


class TestChatDelegation:
    """Test that chat _execute_delegation uses AutonomousAgentExecutor."""

    @pytest.mark.asyncio
    async def test_delegation_calls_autonomous_executor(self, executor, mock_session):
        """Chat delegation dispatches to AutonomousAgentExecutor.run()."""
        from src.services.llm.base import ToolCallRequest

        delegated = MagicMock()
        delegated.id = uuid4()
        delegated.name = "Data Analyst"
        delegated.is_active = True

        agent = MagicMock()
        agent.delegated_agents = [delegated]

        tool_call = ToolCallRequest(
            id="tc1",
            name="delegate_to_data_analyst",
            arguments={"task": "Analyze revenue trends"},
        )

        # Mock the re-fetch query that loads the delegated agent with relationships
        refetched = MagicMock()
        refetched.id = delegated.id
        refetched.name = "Data Analyst"
        mock_refetch_result = MagicMock()
        mock_refetch_result.scalar_one.return_value = refetched
        mock_session.execute = AsyncMock(return_value=mock_refetch_result)

        with patch(
            "src.services.agent_executor.AutonomousAgentExecutor"
        ) as MockExecutorClass, patch(
            "src.core.cache.get_shared_redis", new_callable=AsyncMock
        ) as mock_get_redis:
            mock_get_redis.return_value = MagicMock()
            mock_sub = AsyncMock()
            mock_sub.run = AsyncMock(return_value={
                "output": "Revenue is up 15%",
                "status": "completed",
                "iterations_used": 3,
                "tokens_used": 500,
            })
            MockExecutorClass.return_value = mock_sub

            result = await executor._execute_delegation(tool_call, agent)

        assert result.error is None
        assert result.result["response"] == "Revenue is up 15%"
        assert result.result["agent"] == "Data Analyst"
        # The sub-executor receives the re-fetched agent, not the original
        mock_sub.run.assert_awaited_once_with(
            agent=refetched,
            input_data={"task": "Analyze revenue trends", "_delegated_from": agent.name},
        )

    @pytest.mark.asyncio
    async def test_delegation_refetches_agent_with_relationships(self, executor, mock_session):
        """Delegation re-fetches the target agent to ensure relationships are loaded.

        Without this re-fetch, accessing agent.tools or agent.delegated_agents
        on a child loaded via selectinload causes greenlet_spawn errors in async.
        """
        from src.services.llm.base import ToolCallRequest

        delegated = MagicMock()
        delegated.id = uuid4()
        delegated.name = "Troubleshooting Agent"
        delegated.is_active = True

        agent = MagicMock()
        agent.delegated_agents = [delegated]

        tool_call = ToolCallRequest(
            id="tc1",
            name="delegate_to_troubleshooting_agent",
            arguments={"task": "Fix the issue"},
        )

        refetched = MagicMock()
        refetched.id = delegated.id
        refetched.name = "Troubleshooting Agent"
        mock_refetch_result = MagicMock()
        mock_refetch_result.scalar_one.return_value = refetched
        mock_session.execute = AsyncMock(return_value=mock_refetch_result)

        with patch(
            "src.services.agent_executor.AutonomousAgentExecutor"
        ) as MockExecutorClass, patch(
            "src.core.cache.get_shared_redis", new_callable=AsyncMock
        ) as mock_get_redis:
            mock_get_redis.return_value = MagicMock()
            mock_sub = AsyncMock()
            mock_sub.run = AsyncMock(return_value={
                "output": "Fixed",
                "status": "completed",
                "iterations_used": 1,
                "tokens_used": 100,
            })
            MockExecutorClass.return_value = mock_sub

            result = await executor._execute_delegation(tool_call, agent)

        # Verify session.execute was called (the re-fetch query)
        mock_session.execute.assert_awaited()
        # Verify the sub-executor got the re-fetched agent
        mock_sub.run.assert_awaited_once_with(
            agent=refetched,
            input_data={"task": "Fix the issue", "_delegated_from": agent.name},
        )
        assert result.error is None

    @pytest.mark.asyncio
    async def test_delegation_agent_not_found(self, executor):
        """Returns error when delegated agent doesn't match."""
        from src.services.llm.base import ToolCallRequest

        agent = MagicMock()
        agent.delegated_agents = []

        tool_call = ToolCallRequest(
            id="tc1",
            name="delegate_to_nonexistent",
            arguments={"task": "Do something"},
        )

        result = await executor._execute_delegation(tool_call, agent)
        assert result.error is not None
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_delegation_no_task(self, executor):
        """Returns error when no task is provided."""
        from src.services.llm.base import ToolCallRequest

        delegated = MagicMock()
        delegated.name = "Helper"
        delegated.is_active = True

        agent = MagicMock()
        agent.delegated_agents = [delegated]

        tool_call = ToolCallRequest(
            id="tc1",
            name="delegate_to_helper",
            arguments={},
        )

        result = await executor._execute_delegation(tool_call, agent)
        assert result.error is not None
        assert "No task" in result.error

    @pytest.mark.asyncio
    async def test_delegation_propagates_failure_status(self, executor, mock_session):
        """When sub-executor returns failed status, error is propagated."""
        from src.services.llm.base import ToolCallRequest

        delegated = MagicMock()
        delegated.id = uuid4()
        delegated.name = "Broken Agent"
        delegated.is_active = True

        agent = MagicMock()
        agent.delegated_agents = [delegated]

        tool_call = ToolCallRequest(
            id="tc1",
            name="delegate_to_broken_agent",
            arguments={"task": "Do something"},
        )

        mock_refetch_result = MagicMock()
        mock_refetch_result.scalar_one.return_value = delegated
        mock_session.execute = AsyncMock(return_value=mock_refetch_result)

        with patch(
            "src.services.agent_executor.AutonomousAgentExecutor"
        ) as MockExecutorClass, patch(
            "src.core.cache.get_shared_redis", new_callable=AsyncMock
        ) as mock_get_redis:
            mock_get_redis.return_value = MagicMock()
            mock_sub = AsyncMock()
            mock_sub.run = AsyncMock(return_value={
                "output": None,
                "status": "failed",
                "error": "LLM call failed",
                "iterations_used": 0,
                "tokens_used": 0,
            })
            MockExecutorClass.return_value = mock_sub

            result = await executor._execute_delegation(tool_call, agent)

        assert result.error == "LLM call failed"

    @pytest.mark.asyncio
    async def test_delegation_handles_exception(self, executor, mock_session):
        """Exceptions during delegation are caught and returned as errors."""
        from src.services.llm.base import ToolCallRequest

        delegated = MagicMock()
        delegated.id = uuid4()
        delegated.name = "Crasher"
        delegated.is_active = True

        agent = MagicMock()
        agent.delegated_agents = [delegated]

        tool_call = ToolCallRequest(
            id="tc1",
            name="delegate_to_crasher",
            arguments={"task": "Crash please"},
        )

        mock_refetch_result = MagicMock()
        mock_refetch_result.scalar_one.return_value = delegated
        mock_session.execute = AsyncMock(return_value=mock_refetch_result)

        with patch(
            "src.services.agent_executor.AutonomousAgentExecutor"
        ) as MockExecutorClass, patch(
            "src.core.cache.get_shared_redis", new_callable=AsyncMock
        ) as mock_get_redis:
            mock_get_redis.return_value = MagicMock()
            mock_sub = AsyncMock()
            mock_sub.run = AsyncMock(side_effect=RuntimeError("Connection lost"))
            MockExecutorClass.return_value = mock_sub

            result = await executor._execute_delegation(tool_call, agent)

        assert result.error is not None
        assert "Connection lost" in result.error

    @pytest.mark.asyncio
    async def test_delegation_timeout_returns_error(self, executor, mock_session):
        """Delegation returns timeout error when sub-executor takes too long."""
        import asyncio
        from src.services.llm.base import ToolCallRequest

        delegated = MagicMock()
        delegated.id = uuid4()
        delegated.name = "Slow Agent"
        delegated.is_active = True

        agent = MagicMock()
        agent.delegated_agents = [delegated]

        tool_call = ToolCallRequest(
            id="tc1",
            name="delegate_to_slow_agent",
            arguments={"task": "Take forever"},
        )

        mock_refetch_result = MagicMock()
        mock_refetch_result.scalar_one.return_value = delegated
        mock_session.execute = AsyncMock(return_value=mock_refetch_result)

        with patch(
            "src.services.agent_executor.AutonomousAgentExecutor"
        ) as MockExecutorClass, patch(
            "src.core.cache.get_shared_redis", new_callable=AsyncMock
        ) as mock_get_redis, patch(
            "src.services.agent_executor.asyncio.wait_for",
            side_effect=asyncio.TimeoutError(),
        ):
            mock_get_redis.return_value = MagicMock()
            MockExecutorClass.return_value = AsyncMock()

            result = await executor._execute_delegation(tool_call, agent)

        assert result.error is not None
        assert "timed out" in result.error
