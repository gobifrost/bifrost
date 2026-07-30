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

from src.models.contracts.agents import ToolResult
from src.repositories.knowledge import KnowledgeDocument
from src.services.agent_executor import (
    AgentExecutor,
    _serialize_for_json,
    _serialize_tool_result_for_history,
)
from src.services.execution.autonomous_agent_executor import DelegationOutcome
from src.services.llm import ToolCallRequest, ToolDefinition


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


class TestKnowledgeSearchBudget:
    """Knowledge searches stay bounded within one chat turn."""

    @pytest.mark.asyncio
    async def test_duplicate_query_returns_short_skip_without_embedding(
        self, executor, mock_agent
    ):
        mock_agent.knowledge_sources = ["halo_kb"]
        executor._knowledge_search_budget.reserve("contact roles")
        tool_call = ToolCallRequest(
            id="duplicate",
            name="search_knowledge",
            arguments={"query": "  CONTACT   ROLES ", "limit": 10},
        )

        result = await executor._execute_knowledge_search(tool_call, mock_agent)

        assert result.error is None
        assert result.result["search_skipped"] is True
        assert result.result["reason"] == "duplicate_query"
        assert result.result["documents"] == []

    @pytest.mark.asyncio
    async def test_result_limit_is_clamped_before_repository_search(
        self, executor, mock_agent
    ):
        mock_agent.knowledge_sources = ["halo_kb"]
        mock_agent.organization_id = uuid4()
        tool_call = ToolCallRequest(
            id="oversized",
            name="search_knowledge",
            arguments={"query": "contact roles", "limit": 1000},
        )
        embedding_client = MagicMock()
        embedding_client.embed_single = AsyncMock(return_value=[0.1, 0.2])
        repository = MagicMock()
        repository.search = AsyncMock(return_value=[])

        with patch(
            "src.services.embeddings.get_embedding_client",
            new_callable=AsyncMock,
            return_value=embedding_client,
        ), patch(
            "src.repositories.knowledge.KnowledgeRepository",
            return_value=repository,
        ):
            result = await executor._execute_knowledge_search(tool_call, mock_agent)

        assert result.error is None
        repository.search.assert_awaited_once()
        assert repository.search.await_args.kwargs["limit"] == 5
        assert result.result["searches_used"] == 1
        assert result.result["searches_remaining"] == 7
        assert repository.search.await_args.kwargs["query_text"] == "contact roles"

    @pytest.mark.asyncio
    async def test_distinct_queries_do_not_return_the_same_chunk_twice(
        self, executor, mock_agent
    ):
        mock_agent.knowledge_sources = ["halo_kb"]
        mock_agent.organization_id = uuid4()
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

        with patch(
            "src.services.embeddings.get_embedding_client",
            new_callable=AsyncMock,
            return_value=embedding_client,
        ), patch(
            "src.repositories.knowledge.KnowledgeRepository",
            return_value=repository,
        ):
            first = await executor._execute_knowledge_search(
                ToolCallRequest(
                    id="first",
                    name="search_knowledge",
                    arguments={"query": "technical poc"},
                ),
                mock_agent,
            )
            second = await executor._execute_knowledge_search(
                ToolCallRequest(
                    id="second",
                    name="search_knowledge",
                    arguments={"query": "billing contact role"},
                ),
                mock_agent,
            )

        assert first.result["count"] == 1
        assert second.result["count"] == 0
        assert second.result["omitted_duplicate_evidence"] == 1
        assert repository.search.call_count == 2

    @pytest.mark.asyncio
    async def test_agent_evidence_omits_bulky_image_metadata(
        self, executor, mock_agent
    ):
        mock_agent.knowledge_sources = ["halo_kb"]
        mock_agent.organization_id = uuid4()
        embedding_client = MagicMock()
        embedding_client.embed_single = AsyncMock(return_value=[0.1, 0.2])
        repository = MagicMock()
        repository.search = AsyncMock(
            return_value=[
                KnowledgeDocument(
                    id="chunk-1",
                    content="Use Site Contact Types.",
                    namespace="halo_kb",
                    score=0.9,
                    key="site-contact-types",
                    metadata={
                        "title": "Site Contact Types",
                        "image_uuids": ["image-id"] * 1_000,
                    },
                )
            ]
        )

        with patch(
            "src.services.embeddings.get_embedding_client",
            new_callable=AsyncMock,
            return_value=embedding_client,
        ), patch(
            "src.repositories.knowledge.KnowledgeRepository",
            return_value=repository,
        ):
            result = await executor._execute_knowledge_search(
                ToolCallRequest(
                    id="metadata",
                    name="search_knowledge",
                    arguments={"query": "site contact types"},
                ),
                mock_agent,
            )

        assert result.result["documents"][0]["metadata"] == {
            "title": "Site Contact Types"
        }


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
        """Chat delegation dispatches through the shared durable runner."""
        from src.services.llm.base import ToolCallRequest

        delegated = MagicMock()
        delegated.id = uuid4()
        delegated.name = "Data Analyst"
        delegated.is_active = True

        agent = MagicMock()
        agent.name = "Coordinator"
        agent.delegated_agents = [delegated]
        conversation = MagicMock()
        conversation.id = uuid4()
        caller = {
            "user_id": str(uuid4()),
            "email": "person@example.com",
            "name": "Person",
        }

        tool_call = ToolCallRequest(
            id="tc1",
            name="delegate_to_data_analyst",
            arguments={"task": "Analyze revenue trends"},
        )
        child_run_id = uuid4()

        with patch(
            "src.services.agent_executor.AutonomousAgentExecutor"
        ) as MockExecutorClass, patch(
            "src.core.cache.get_shared_redis", new_callable=AsyncMock
        ) as mock_get_redis:
            mock_get_redis.return_value = MagicMock()
            mock_sub = AsyncMock()
            mock_sub.run_delegation = AsyncMock(
                return_value=DelegationOutcome(
                    child_run_id=child_run_id,
                    agent_name="Data Analyst",
                    status="completed",
                    output="Revenue is up 15%",
                    error=None,
                    duration_ms=15,
                )
            )
            MockExecutorClass.return_value = mock_sub

            result = await executor._execute_delegation(
                tool_call,
                agent,
                conversation=conversation,
                caller=caller,
            )

        assert result.error is None
        assert result.result["response"] == "Revenue is up 15%"
        assert result.result["agent"] == "Data Analyst"
        assert result.result["status"] == "completed"
        assert result.result["child_run_id"] == str(child_run_id)
        assert result.metadata == {
            "child_run_id": str(child_run_id),
            "agent": "Data Analyst",
            "status": "completed",
        }
        mock_sub.run_delegation.assert_awaited_once_with(
            parent_agent=agent,
            tool_call=tool_call,
            conversation_id=conversation.id,
            caller=caller,
        )

    def test_parent_history_prefers_error_over_partial_result(self):
        """A failed child must not look successful merely because it returned data."""
        tool_result = ToolResult(
            tool_call_id="tc1",
            tool_name="delegate_to_specialist",
            result={"response": "partial answer"},
            error="Specialist failed after producing a partial answer",
        )

        assert _serialize_tool_result_for_history(tool_result) == (
            "Specialist failed after producing a partial answer"
        )

    @pytest.mark.asyncio
    async def test_delegation_without_conversation_still_uses_shared_runner(
        self, executor
    ):
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

        with patch(
            "src.services.agent_executor.AutonomousAgentExecutor"
        ) as MockExecutorClass, patch(
            "src.core.cache.get_shared_redis", new_callable=AsyncMock
        ) as mock_get_redis:
            mock_get_redis.return_value = MagicMock()
            mock_sub = AsyncMock()
            mock_sub.run_delegation = AsyncMock(
                return_value=DelegationOutcome(
                    child_run_id=uuid4(),
                    agent_name="Troubleshooting Agent",
                    status="completed",
                    output="Fixed",
                    error=None,
                    duration_ms=10,
                )
            )
            MockExecutorClass.return_value = mock_sub

            result = await executor._execute_delegation(tool_call, agent)

        mock_sub.run_delegation.assert_awaited_once_with(
            parent_agent=agent,
            tool_call=tool_call,
            conversation_id=None,
            caller=None,
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

        with patch(
            "src.services.agent_executor.AutonomousAgentExecutor"
        ) as MockExecutorClass, patch(
            "src.core.cache.get_shared_redis", new_callable=AsyncMock
        ) as mock_get_redis:
            mock_get_redis.return_value = MagicMock()
            mock_sub = AsyncMock()
            child_run_id = uuid4()
            mock_sub.run_delegation = AsyncMock(
                return_value=DelegationOutcome(
                    child_run_id=child_run_id,
                    agent_name="Broken Agent",
                    status="failed",
                    output=None,
                    error="LLM call failed",
                    duration_ms=10,
                )
            )
            MockExecutorClass.return_value = mock_sub

            result = await executor._execute_delegation(tool_call, agent)

        assert result.error == "LLM call failed"
        assert result.result is None
        assert result.metadata == {
            "child_run_id": str(child_run_id),
            "agent": "Broken Agent",
            "status": "failed",
        }

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("status", "error"),
        [
            ("cancelled", "Delegation was cancelled"),
            ("paused", "Delegated agent is paused"),
            ("budget_exceeded", "Delegated agent exceeded its budget"),
            ("timeout", "Delegation timed out"),
        ],
    )
    async def test_delegation_propagates_every_non_success_status(
        self, executor, status, error
    ):
        from src.services.llm.base import ToolCallRequest

        delegated = MagicMock()
        delegated.id = uuid4()
        delegated.name = "Lifecycle Agent"
        delegated.is_active = True
        agent = MagicMock()
        agent.delegated_agents = [delegated]
        child_run_id = uuid4()

        with patch(
            "src.services.agent_executor.AutonomousAgentExecutor"
        ) as MockExecutorClass, patch(
            "src.core.cache.get_shared_redis", new_callable=AsyncMock
        ) as mock_get_redis:
            mock_get_redis.return_value = MagicMock()
            mock_sub = AsyncMock()
            mock_sub.run_delegation = AsyncMock(
                return_value=DelegationOutcome(
                    child_run_id=child_run_id,
                    agent_name="Lifecycle Agent",
                    status=status,
                    output=None,
                    error=error,
                    duration_ms=10,
                )
            )
            MockExecutorClass.return_value = mock_sub

            result = await executor._execute_delegation(
                ToolCallRequest(
                    id="tc1",
                    name="delegate_to_lifecycle_agent",
                    arguments={"task": "Exercise lifecycle"},
                ),
                agent,
            )

        assert result.result is None
        assert result.error == error
        assert result.metadata == {
            "child_run_id": str(child_run_id),
            "agent": "Lifecycle Agent",
            "status": status,
        }

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

        with patch(
            "src.services.agent_executor.AutonomousAgentExecutor"
        ) as MockExecutorClass, patch(
            "src.core.cache.get_shared_redis", new_callable=AsyncMock
        ) as mock_get_redis:
            mock_get_redis.return_value = MagicMock()
            mock_sub = AsyncMock()
            mock_sub.run_delegation = AsyncMock(
                side_effect=RuntimeError("Connection lost")
            )
            MockExecutorClass.return_value = mock_sub

            result = await executor._execute_delegation(tool_call, agent)

        assert result.error is not None
        assert "Connection lost" in result.error

    @pytest.mark.asyncio
    async def test_delegation_timeout_returns_error(self, executor, mock_session):
        """Delegation returns timeout error when sub-executor takes too long."""
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

        with patch(
            "src.services.agent_executor.AutonomousAgentExecutor"
        ) as MockExecutorClass, patch(
            "src.core.cache.get_shared_redis", new_callable=AsyncMock
        ) as mock_get_redis:
            mock_get_redis.return_value = MagicMock()
            child_run_id = uuid4()
            mock_sub = AsyncMock()
            mock_sub.run_delegation = AsyncMock(
                return_value=DelegationOutcome(
                    child_run_id=child_run_id,
                    agent_name="Slow Agent",
                    status="timeout",
                    output=None,
                    error="Delegation to Slow Agent timed out after 600s",
                    duration_ms=600_000,
                )
            )
            MockExecutorClass.return_value = mock_sub

            result = await executor._execute_delegation(tool_call, agent)

        assert result.error is not None
        assert "timed out" in result.error
