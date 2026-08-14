"""Unit tests for the unscoped MCP agent gateway."""

from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.models.orm.agents import Agent
from src.services.llm import ToolDefinition
from src.services.mcp_server.config_service import MCPConfig
from src.services.mcp_server.gateway import (
    AgentToolSnapshot,
    GatewayError,
    MCPAgentGatewayService,
    ResolvedGatewayTool,
)
from src.services.mcp_server.server import MCPContext


def _context() -> MCPContext:
    return MCPContext(
        user_id=uuid4(),
        org_id=uuid4(),
        user_email="robot@example.com",
        user_name="Robot",
    )


def _agent() -> MagicMock:
    agent = MagicMock(spec=Agent)
    agent.id = uuid4()
    agent.name = "Operations Agent"
    agent.description = "Handles operational tasks"
    agent.system_prompt = "Use the tools carefully."
    agent.system_tools = ["list_workflows"]
    agent.knowledge_sources = ["runbooks"]
    agent.delegated_agents = []
    agent.organization_id = uuid4()
    return agent


def _resolved_tool(
    *,
    name: str = "lookup_ticket",
    description: str = "Look up a ticket",
    parameters: dict | None = None,
) -> ResolvedGatewayTool:
    return ResolvedGatewayTool(
        tool_ref=str(uuid4()),
        definition=ToolDefinition(
            name=name,
            description=description,
            parameters=parameters
            or {
                "type": "object",
                "properties": {"ticket_id": {"type": "integer"}},
                "required": ["ticket_id"],
                "additionalProperties": False,
            },
        ),
        source="workflow",
        source_identity=f"workflow:{uuid4()}",
        source_id=uuid4(),
    )


def test_workflow_reference_is_stable_across_display_name_change():
    service = MCPAgentGatewayService(_context())
    agent = _agent()
    workflow_id = uuid4()

    first = service._resolve_gateway_tools(
        agent,
        [
            ToolDefinition(
                name="old_name",
                description="Old",
                parameters={"type": "object"},
            )
        ],
        {"old_name": workflow_id},
        MCPConfig(),
    )
    second = service._resolve_gateway_tools(
        agent,
        [
            ToolDefinition(
                name="new_name",
                description="New",
                parameters={"type": "object"},
            )
        ],
        {"new_name": workflow_id},
        MCPConfig(),
    )

    assert first[0].tool_ref == second[0].tool_ref
    assert first[0].source_identity == f"workflow:{workflow_id}"


def test_live_config_filters_underlying_tools_by_name_or_source_id():
    service = MCPAgentGatewayService(_context())
    agent = _agent()
    workflow_id = uuid4()
    definitions = [
        ToolDefinition(
            name="list_workflows",
            description="List",
            parameters={"type": "object"},
        ),
        ToolDefinition(
            name="lookup_ticket",
            description="Lookup",
            parameters={"type": "object"},
        ),
    ]

    tools = service._resolve_gateway_tools(
        agent,
        definitions,
        {"lookup_ticket": workflow_id},
        MCPConfig(allowed_tool_ids=[str(workflow_id)]),
    )

    assert [tool.definition.name for tool in tools] == ["lookup_ticket"]


def test_validation_error_is_model_repairable():
    tool = _resolved_tool()

    with pytest.raises(GatewayError) as exc_info:
        MCPAgentGatewayService.validate_arguments(
            tool,
            {"ticket_id": "not-an-integer", "surprise": True},
        )

    error = exc_info.value
    assert error.code == "INVALID_ARGUMENTS"
    assert error.retryable is True
    assert error.details["input_schema"] == tool.definition.parameters
    assert {issue["path"] for issue in error.details["issues"]} == {
        "/",
        "/ticket_id",
    }


def test_unknown_reference_does_not_fall_back_to_tool_name():
    agent = _agent()
    snapshot = AgentToolSnapshot(agent=agent, tools=[_resolved_tool()])

    with pytest.raises(GatewayError) as exc_info:
        MCPAgentGatewayService.find_tool(snapshot, "lookup_ticket")

    assert exc_info.value.code == "TOOL_NOT_FOUND_OR_FORBIDDEN"
    assert exc_info.value.retryable is True


def test_agent_hydration_never_implies_zero_matches_is_a_full_catalog():
    service = MCPAgentGatewayService(_context())
    agent = _agent()
    snapshot = AgentToolSnapshot(
        agent=agent,
        tools=[_resolved_tool(name="lookup_ticket"), _resolved_tool(name="close_ticket")],
    )

    result = service._search_agent_snapshot(
        snapshot,
        query=None,
        tool_ref=None,
        limit=10,
    )

    found = result["agents"][0]
    assert found["instructions"] == agent.system_prompt
    assert found["instructions_included"] is True
    assert found["matching_tools"] == []
    assert found["total_tools"] == 2
    assert found["returned_tools"] == 0
    assert found["complete"] is False
    assert "not the agent's full tool catalog" in found["search_again"]


def test_scoped_search_returns_only_matching_tools_with_disclosure_counts():
    service = MCPAgentGatewayService(_context())
    snapshot = AgentToolSnapshot(
        agent=_agent(),
        tools=[
            _resolved_tool(name="lookup_ticket"),
            _resolved_tool(
                name="send_invoice",
                description="Send an invoice",
                parameters={
                    "type": "object",
                    "properties": {"invoice_id": {"type": "integer"}},
                },
            ),
        ],
    )

    result = service._search_agent_snapshot(
        snapshot,
        query="ticket",
        tool_ref=None,
        limit=10,
    )

    found = result["agents"][0]
    assert [tool["name"] for tool in found["matching_tools"]] == ["lookup_ticket"]
    assert found["total_tools"] == 2
    assert found["returned_tools"] == 1
    assert found["complete"] is False
    assert found["total_matching_tools"] == 1
    assert found["has_more_matches"] is False
    assert result["response_complete"] is True


def test_exact_tool_hydration_includes_only_the_selected_live_schema():
    service = MCPAgentGatewayService(_context())
    tool = _resolved_tool()
    snapshot = AgentToolSnapshot(agent=_agent(), tools=[tool])

    result = service._search_agent_snapshot(
        snapshot,
        query=None,
        tool_ref=tool.tool_ref,
        limit=10,
    )

    found_tool = result["agents"][0]["matching_tools"][0]
    assert found_tool["tool_ref"] == tool.tool_ref
    assert found_tool["schema_included"] is True
    assert found_tool["input_schema"] == tool.definition.parameters
    assert found_tool["supports_async"] is True
    assert found_tool["default_async"] is False


def test_delegation_capability_declares_async_default():
    tool = replace(_resolved_tool(), source="delegation")

    compact = tool.compact()

    assert compact["source"] == "delegation"
    assert compact["supports_async"] is True
    assert compact["default_async"] is True


def test_execution_result_pages_large_values_without_invalid_json_truncation():
    result, page = MCPAgentGatewayService._page_execution_result(
        {
            "small": 42,
            "huge": {"rows": ["x" * 30_000]},
            "after": True,
        },
        result_path="",
        offset=0,
        limit=20,
    )

    assert result["small"] == 42
    assert result["huge"]["$omitted"] is True
    assert result["huge"]["path"] == "/huge"
    assert result["after"] is True
    assert page["has_more"] is False


def test_execution_result_path_can_hydrate_an_omitted_value():
    result, page = MCPAgentGatewayService._page_execution_result(
        {"huge": {"rows": list(range(50))}},
        result_path="/huge/rows",
        offset=10,
        limit=5,
    )

    assert result == [10, 11, 12, 13, 14]
    assert page["next_offset"] == 15
    assert page["has_more"] is True


@pytest.mark.parametrize(
    ("agent_status", "gateway_status"),
    [
        ("queued", "Pending"),
        ("running", "Running"),
        ("completed", "Success"),
        ("failed", "Failed"),
        ("timeout", "Timeout"),
        ("cancelled", "Cancelled"),
        ("budget_exceeded", "BudgetExceeded"),
    ],
)
def test_agent_run_status_uses_gateway_execution_vocabulary(
    agent_status,
    gateway_status,
):
    assert (
        MCPAgentGatewayService._agent_run_gateway_status(agent_status)
        == gateway_status
    )


@pytest.mark.asyncio
async def test_get_execution_authorizes_redis_only_pending_receipt():
    context = _context()
    service = MCPAgentGatewayService(context)
    execution_id = str(uuid4())
    db = AsyncMock()
    db_result = MagicMock()
    db_result.scalar_one_or_none.return_value = None
    db.execute.return_value = db_result
    db_context = MagicMock()
    db_context.__aenter__ = AsyncMock(return_value=db)
    db_context.__aexit__ = AsyncMock(return_value=None)
    redis = MagicMock()
    redis.get_pending_execution = AsyncMock(
        return_value={
            "execution_id": execution_id,
            "workflow_id": str(uuid4()),
            "script_name": None,
            "user_id": str(context.user_id),
            "created_at": "2026-08-13T12:00:00+00:00",
        }
    )

    with (
        patch("src.core.database.get_db_context", return_value=db_context),
        patch("src.core.redis_client.get_redis_client", return_value=redis),
    ):
        result = await service.get_execution(execution_id)

    assert result["execution_id"] == execution_id
    assert result["status"] == "Pending"
    assert result["result_available"] is False


@pytest.mark.asyncio
async def test_get_execution_hides_another_users_redis_only_receipt():
    service = MCPAgentGatewayService(_context())
    execution_id = str(uuid4())
    db = AsyncMock()
    db_result = MagicMock()
    db_result.scalar_one_or_none.return_value = None
    db.execute.return_value = db_result
    db_context = MagicMock()
    db_context.__aenter__ = AsyncMock(return_value=db)
    db_context.__aexit__ = AsyncMock(return_value=None)
    redis = MagicMock()
    redis.get_pending_execution = AsyncMock(
        return_value={"user_id": str(uuid4())}
    )

    with (
        patch("src.core.database.get_db_context", return_value=db_context),
        patch("src.core.redis_client.get_redis_client", return_value=redis),
    ):
        with pytest.raises(GatewayError) as exc_info:
            await service.get_execution(execution_id)

    assert exc_info.value.code == "EXECUTION_NOT_FOUND_OR_FORBIDDEN"


@pytest.mark.asyncio
async def test_get_execution_returns_owned_pending_agent_run_receipt():
    context = _context()
    service = MCPAgentGatewayService(context)
    execution_id = str(uuid4())
    db = AsyncMock()
    missing = MagicMock()
    missing.scalar_one_or_none.return_value = None
    db.execute.return_value = missing
    db_context = MagicMock()
    db_context.__aenter__ = AsyncMock(return_value=db)
    db_context.__aexit__ = AsyncMock(return_value=None)
    redis = MagicMock()
    redis.get_pending_execution = AsyncMock(return_value=None)

    with (
        patch("src.core.database.get_db_context", return_value=db_context),
        patch("src.core.redis_client.get_redis_client", return_value=redis),
        patch(
            "src.services.execution.agent_run_service.get_pending_agent_run_context",
            new=AsyncMock(
                return_value={
                    "agent_id": str(uuid4()),
                    "caller": {"user_id": str(context.user_id)},
                }
            ),
        ),
    ):
        result = await service.get_execution(execution_id)

    assert result["execution_id"] == execution_id
    assert result["execution_type"] == "agent_run"
    assert result["status"] == "Pending"
    assert result["result_available"] is False


@pytest.mark.asyncio
async def test_get_execution_returns_completed_owned_agent_run():
    context = _context()
    service = MCPAgentGatewayService(context)
    execution_id = uuid4()
    workflow_result = MagicMock()
    workflow_result.scalar_one_or_none.return_value = None
    agent_run_result = MagicMock()
    agent_run = MagicMock()
    agent_run.id = execution_id
    agent_run.agent_id = uuid4()
    agent_run.agent.name = "Process Agent"
    agent_run.caller_user_id = str(context.user_id)
    agent_run.status = "completed"
    agent_run.output = {"text": "Done"}
    agent_run.created_at = None
    agent_run.started_at = None
    agent_run.completed_at = None
    agent_run.duration_ms = 125
    agent_run.error = None
    agent_run_result.scalar_one_or_none.return_value = agent_run
    db = AsyncMock()
    db.execute.side_effect = [workflow_result, agent_run_result]
    db_context = MagicMock()
    db_context.__aenter__ = AsyncMock(return_value=db)
    db_context.__aexit__ = AsyncMock(return_value=None)

    with patch("src.core.database.get_db_context", return_value=db_context):
        result = await service.get_execution(str(execution_id))

    assert result["execution_type"] == "agent_run"
    assert result["agent_id"] == str(agent_run.agent_id)
    assert result["agent_name"] == "Process Agent"
    assert result["status"] == "Success"
    assert result["result"] == {"text": "Done"}


@pytest.mark.asyncio
async def test_get_execution_hides_another_users_agent_run():
    service = MCPAgentGatewayService(_context())
    workflow_result = MagicMock()
    workflow_result.scalar_one_or_none.return_value = None
    agent_run_result = MagicMock()
    agent_run = MagicMock()
    agent_run.caller_user_id = str(uuid4())
    agent_run_result.scalar_one_or_none.return_value = agent_run
    db = AsyncMock()
    db.execute.side_effect = [workflow_result, agent_run_result]
    db_context = MagicMock()
    db_context.__aenter__ = AsyncMock(return_value=db)
    db_context.__aexit__ = AsyncMock(return_value=None)

    with patch("src.core.database.get_db_context", return_value=db_context):
        with pytest.raises(GatewayError) as exc_info:
            await service.get_execution(str(uuid4()))

    assert exc_info.value.code == "EXECUTION_NOT_FOUND_OR_FORBIDDEN"


@pytest.mark.asyncio
async def test_capability_search_stops_before_the_serialized_response_budget(
    monkeypatch,
):
    service = MCPAgentGatewayService(_context())
    agents = []
    snapshots = {}
    for index in range(8):
        agent = _agent()
        agent.id = uuid4()
        agent.name = f"Ticket Agent {index}"
        agent.description = "ticket operations " * 100
        agents.append(agent)
        snapshots[str(agent.id)] = AgentToolSnapshot(
            agent=agent,
            tools=[
                _resolved_tool(
                    name=f"lookup_ticket_{index}",
                    description="ticket lookup " * 100,
                )
            ],
        )

    monkeypatch.setattr(
        "src.services.mcp_server.gateway.MAX_CAPABILITY_RESPONSE_BYTES",
        4_000,
    )
    with (
        patch.object(
            service,
            "_list_accessible_agents",
            new=AsyncMock(return_value=agents),
        ),
        patch.object(
            service,
            "get_agent_snapshot",
            new=AsyncMock(side_effect=lambda agent_id: snapshots[agent_id]),
        ),
    ):
        result = await service.search_capabilities(query="ticket", limit=20)

    from src.services.mcp_server.gateway import _serialized_size

    assert _serialized_size(result) <= 4_000
    assert result["has_more_matches"] is True
    assert result["response_complete"] is False
    assert result["returned_matches"] < result["total_matches"]


@pytest.mark.asyncio
async def test_execute_validates_before_dispatch():
    service = MCPAgentGatewayService(_context())
    agent = _agent()
    tool = _resolved_tool()
    snapshot = AgentToolSnapshot(agent=agent, tools=[tool])

    with patch.object(service, "_dispatch", new_callable=AsyncMock) as dispatch:
        with pytest.raises(GatewayError) as exc_info:
            await service.execute_tool(
                snapshot,
                tool,
                {"ticket_id": "invalid"},
            )

    assert exc_info.value.code == "INVALID_ARGUMENTS"
    assert exc_info.value.details["agent_id"] == str(agent.id)
    dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_returns_auditable_envelope():
    service = MCPAgentGatewayService(_context())
    agent = _agent()
    tool = _resolved_tool()
    snapshot = AgentToolSnapshot(agent=agent, tools=[tool])

    with patch.object(
        service,
        "_dispatch",
        new=AsyncMock(return_value={"ticket": 42}),
    ):
        result = await service.execute_tool(
            snapshot,
            tool,
            {"ticket_id": 42},
        )

    assert result["agent_id"] == str(agent.id)
    assert result["tool_ref"] == tool.tool_ref
    assert result["tool_name"] == "lookup_ticket"
    assert result["source"] == "workflow"
    assert result["async"] is False
    assert result["result"] == {"ticket": 42}
    assert isinstance(result["duration_ms"], int)


@pytest.mark.asyncio
async def test_delegation_defaults_to_async_execution():
    service = MCPAgentGatewayService(_context())
    agent = _agent()
    tool = replace(_resolved_tool(), source="delegation")
    snapshot = AgentToolSnapshot(agent=agent, tools=[tool])
    execution_id = str(uuid4())

    with patch.object(
        service,
        "_dispatch",
        new=AsyncMock(
            return_value={
                "execution_id": execution_id,
                "execution_type": "agent_run",
                "status": "Pending",
            }
        ),
    ) as dispatch:
        result = await service.execute_tool(
            snapshot,
            tool,
            {"ticket_id": 42},
        )

    assert dispatch.await_args.kwargs["async_execution"] is True
    assert result["async"] is True
    assert result["execution_id"] == execution_id
    assert result["execution_type"] == "agent_run"
    assert result["result"] is None


@pytest.mark.asyncio
async def test_delegation_allows_explicit_synchronous_execution():
    service = MCPAgentGatewayService(_context())
    agent = _agent()
    tool = replace(_resolved_tool(), source="delegation")
    snapshot = AgentToolSnapshot(agent=agent, tools=[tool])

    with patch.object(
        service,
        "_dispatch",
        new=AsyncMock(return_value={"text": "Done"}),
    ) as dispatch:
        result = await service.execute_tool(
            snapshot,
            tool,
            {"ticket_id": 42},
            async_execution=False,
        )

    assert dispatch.await_args.kwargs["async_execution"] is False
    assert result["async"] is False
    assert result["result"] == {"text": "Done"}


@pytest.mark.asyncio
async def test_workflow_dispatch_returns_only_the_workflow_result():
    service = MCPAgentGatewayService(_context())
    tool = _resolved_tool()
    response = MagicMock()
    response.execution_id = str(uuid4())
    response.status.value = "Success"
    response.duration_ms = 125
    response.result = [{"ticket": 42}]
    response.error = None
    response.error_type = None

    with patch(
        "src.services.execution.service.execute_tool",
        new=AsyncMock(return_value=response),
    ):
        result = await service._dispatch_workflow(tool, {"ticket_id": 42})

    assert result == [{"ticket": 42}]


@pytest.mark.asyncio
async def test_async_workflow_dispatch_returns_immediate_execution_receipt():
    service = MCPAgentGatewayService(_context())
    tool = _resolved_tool()
    execution_id = str(uuid4())
    response = MagicMock()
    response.execution_id = execution_id
    response.status.value = "Pending"
    response.duration_ms = None
    response.result = None
    response.error = None
    response.error_type = None

    with patch(
        "src.services.execution.service.execute_tool",
        new=AsyncMock(return_value=response),
    ) as execute:
        result = await service._dispatch_workflow(
            tool,
            {"ticket_id": 42},
            async_execution=True,
        )

    assert result == {
        "execution_id": execution_id,
        "execution_type": "workflow",
        "status": "Pending",
    }
    assert execute.await_args.kwargs["sync"] is False


@pytest.mark.asyncio
async def test_async_rejects_unsupported_sources_without_dispatching():
    service = MCPAgentGatewayService(_context())
    agent = _agent()
    tool = replace(_resolved_tool(), source="system")
    snapshot = AgentToolSnapshot(agent=agent, tools=[tool])

    with patch.object(service, "_dispatch", new_callable=AsyncMock) as dispatch:
        with pytest.raises(GatewayError) as exc_info:
            await service.execute_tool(
                snapshot,
                tool,
                {"ticket_id": 42},
                async_execution=True,
            )

    assert exc_info.value.code == "ASYNC_NOT_SUPPORTED"
    dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_async_delegation_enqueues_agent_run():
    context = _context()
    service = MCPAgentGatewayService(context)
    parent = _agent()
    delegated = _agent()
    delegated.name = "Process Agent"
    delegated.is_active = True
    tool = replace(
        _resolved_tool(
            name="delegate_to_process_agent",
            parameters={
                "type": "object",
                "properties": {"task": {"type": "string"}},
                "required": ["task"],
            },
        ),
        source="delegation",
        source_id=delegated.id,
    )
    db_context = MagicMock()
    db_context.__aenter__ = AsyncMock(return_value=AsyncMock())
    db_context.__aexit__ = AsyncMock(return_value=None)
    repository = MagicMock()
    repository.get_agent = AsyncMock(return_value=delegated)
    execution_id = str(uuid4())

    with (
        patch("src.core.database.get_db_context", return_value=db_context),
        patch(
            "src.services.mcp_server.gateway.AgentRepository",
            return_value=repository,
        ),
        patch(
            "src.services.execution.agent_run_service.enqueue_agent_run",
            new=AsyncMock(return_value=execution_id),
        ) as enqueue,
    ):
        result = await service._dispatch_delegation(
            parent,
            tool,
            {"task": "Assemble the customer report"},
            async_execution=True,
        )

    assert result == {
        "execution_id": execution_id,
        "execution_type": "agent_run",
        "status": "Pending",
    }
    assert enqueue.await_args.kwargs["sync"] is False
    assert enqueue.await_args.kwargs["trigger_type"] == "delegation"
    assert enqueue.await_args.kwargs["caller_user_id"] == str(context.user_id)


@pytest.mark.asyncio
async def test_sync_delegation_waits_for_queued_agent_run_result():
    service = MCPAgentGatewayService(_context())
    parent = _agent()
    delegated = _agent()
    delegated.name = "Process Agent"
    delegated.is_active = True
    delegated.max_run_timeout = 240
    tool = replace(
        _resolved_tool(
            name="delegate_to_process_agent",
            parameters={
                "type": "object",
                "properties": {"task": {"type": "string"}},
                "required": ["task"],
            },
        ),
        source="delegation",
        source_id=delegated.id,
    )
    db_context = MagicMock()
    db_context.__aenter__ = AsyncMock(return_value=AsyncMock())
    db_context.__aexit__ = AsyncMock(return_value=None)
    repository = MagicMock()
    repository.get_agent = AsyncMock(return_value=delegated)
    execution_id = str(uuid4())

    with (
        patch("src.core.database.get_db_context", return_value=db_context),
        patch(
            "src.services.mcp_server.gateway.AgentRepository",
            return_value=repository,
        ),
        patch(
            "src.services.execution.agent_run_service.enqueue_agent_run",
            new=AsyncMock(return_value=execution_id),
        ) as enqueue,
        patch(
            "src.services.execution.agent_run_service.wait_for_agent_run_result",
            new=AsyncMock(
                return_value={
                    "status": "completed",
                    "output": {"text": "Done"},
                    "iterations_used": 1,
                }
            ),
        ) as wait_for_result,
    ):
        result = await service._dispatch_delegation(
            parent,
            tool,
            {"task": "Assemble the customer report"},
            async_execution=False,
        )

    assert result == {
        "status": "completed",
        "output": {"text": "Done"},
        "iterations_used": 1,
    }
    assert enqueue.await_args.kwargs["sync"] is True
    wait_for_result.assert_awaited_once_with(execution_id, timeout=240)


@pytest.mark.asyncio
async def test_workflow_dispatch_keeps_execution_details_on_failure():
    service = MCPAgentGatewayService(_context())
    tool = _resolved_tool()
    execution_id = str(uuid4())
    response = MagicMock()
    response.execution_id = execution_id
    response.status.value = "Failed"
    response.duration_ms = 125
    response.result = None
    response.error = "HaloPSA rejected the query"
    response.error_type = "UserError"

    with patch(
        "src.services.execution.service.execute_tool",
        new=AsyncMock(return_value=response),
    ):
        with pytest.raises(GatewayError) as exc_info:
            await service._dispatch_workflow(tool, {"ticket_id": 42})

    error = exc_info.value
    assert error.message == "HaloPSA rejected the query"
    assert error.details["underlying_result"] == {
        "execution_id": execution_id,
        "status": "Failed",
        "duration_ms": 125,
        "result": None,
        "error": "HaloPSA rejected the query",
        "error_type": "UserError",
    }


def test_external_dispatch_prefers_the_structured_tool_payload():
    result = MCPAgentGatewayService.unwrap_external_result(
        {
            "content": [{"type": "text", "text": "fallback"}],
            "structured_content": {"tickets": [42]},
            "is_error": False,
            "_resolution_path": "user_token",
        }
    )

    assert result == {"tickets": [42]}


def test_external_dispatch_uses_content_without_structured_payload():
    content = [{"type": "text", "text": "plain result"}]

    result = MCPAgentGatewayService.unwrap_external_result(
        {
            "content": content,
            "structured_content": None,
            "is_error": False,
            "_resolution_path": "service_token",
        }
    )

    assert result == content


def test_external_dispatch_preserves_structured_error_details():
    underlying = {
        "content": [{"type": "text", "text": "vendor rejected request"}],
        "structured_content": {"error": "Invalid project"},
        "is_error": True,
        "_resolution_path": "user_token",
    }

    with pytest.raises(GatewayError) as exc_info:
        MCPAgentGatewayService.unwrap_external_result(underlying)

    error = exc_info.value
    assert error.message == "Invalid project"
    assert error.retryable is True
    assert error.details["underlying_result"] == underlying
