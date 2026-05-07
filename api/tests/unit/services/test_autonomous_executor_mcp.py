"""Unit tests for AutonomousAgentExecutor MCP-tool dispatch.

Phase 3 contract for autonomous runs:

- ``run()`` reads ``_caller["user_id"]`` (if present) and stores it on
  the executor so MCP dispatch can pick it up.
- ``_execute_tool`` routes ``mcp__<connection_id>__<tool>`` names to
  ``_execute_mcp_tool`` BEFORE the workflow-tool fallback.
- Webhook deliveries with a signed user claim get user-token resolution.
- Fully autonomous runs (no ``_caller`` or no ``user_id``) get
  ``caller_user_id=None`` so dispatch routes to the service token.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.services.execution.agent_helpers import MCP_TOOL_PREFIX
from src.services.execution.autonomous_agent_executor import (
    AutonomousAgentExecutor,
    ToolError,
)
from src.services.llm.base import LLMResponse, ToolCallRequest
from src.services.mcp_client.errors import (
    MisconfigError,
    NeedsReauthError,
    ToolDispatchError,
)


@pytest.fixture
def mock_session_factory():
    """Mock async session factory yielding a session whose ``execute()``
    returns a fake MCPConnection."""
    fake_connection = MagicMock()
    fake_connection.id = uuid4()

    fake_result = MagicMock()
    fake_result.scalar_one_or_none = MagicMock(return_value=fake_connection)

    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.execute = AsyncMock(return_value=fake_result)
    session.get = AsyncMock(return_value=None)

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)

    factory = MagicMock(return_value=ctx)
    factory._fake_connection = fake_connection
    factory._fake_session = session
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
    agent.max_iterations = 5
    agent.max_token_budget = 50000
    agent.max_run_timeout = 60
    agent.llm_model = None
    agent.llm_max_tokens = None
    agent.is_active = True
    agent.organization_id = uuid4()
    return agent


@pytest.mark.asyncio
async def test_execute_mcp_tool_uses_threaded_caller(mock_session_factory):
    """When ``_caller_user_id`` is set on the executor (by ``run()``),
    ``_execute_mcp_tool`` forwards it to dispatch."""
    executor = AutonomousAgentExecutor(mock_session_factory)
    caller_user_id = uuid4()
    executor._caller_user_id = caller_user_id

    connection_id = uuid4()
    fake_envelope = {
        "content": [{"type": "text", "text": "ok"}],
        "structured_content": {"a": 1},
        "is_error": False,
    }

    with patch(
        "src.services.execution.autonomous_agent_executor.mcp_dispatch.invoke",
        new=AsyncMock(return_value=fake_envelope),
    ) as mock_invoke:
        result = await executor._execute_mcp_tool(
            ToolCallRequest(
                id="tc-1",
                name=f"{MCP_TOOL_PREFIX}{connection_id}__t",
                arguments={"q": "hi"},
            ),
            connection_id=connection_id,
            remote_tool_name="t",
        )

    assert mock_invoke.await_count == 1
    assert mock_invoke.await_args is not None
    assert mock_invoke.await_args.kwargs["caller_user_id"] == caller_user_id
    assert mock_invoke.await_args.kwargs["tool_name"] == "t"
    # The autonomous path returns a JSON-string envelope (the loop
    # serializes tool results into the conversation as text).
    assert isinstance(result, str)
    assert "structured_content" in result


@pytest.mark.asyncio
async def test_execute_mcp_tool_with_no_caller(mock_session_factory):
    """A fully autonomous run has ``_caller_user_id=None``; dispatch
    sees ``None`` and routes to service-token resolution."""
    executor = AutonomousAgentExecutor(mock_session_factory)
    # Intentionally not setting _caller_user_id — should default to None
    assert executor._caller_user_id is None

    connection_id = uuid4()
    fake_envelope = {"content": [], "is_error": False}

    with patch(
        "src.services.execution.autonomous_agent_executor.mcp_dispatch.invoke",
        new=AsyncMock(return_value=fake_envelope),
    ) as mock_invoke:
        await executor._execute_mcp_tool(
            ToolCallRequest(
                id="tc-2",
                name=f"{MCP_TOOL_PREFIX}{connection_id}__t",
                arguments={},
            ),
            connection_id=connection_id,
            remote_tool_name="t",
        )

    assert mock_invoke.await_args is not None
    assert mock_invoke.await_args.kwargs["caller_user_id"] is None


@pytest.mark.asyncio
async def test_execute_mcp_tool_raises_tool_error_on_needs_reauth(
    mock_session_factory,
):
    """An autonomous run can't prompt a user to reconnect, so
    ``NeedsReauthError`` becomes ``ToolError`` — the loop records it
    in the run's step log and proceeds (or terminates if the LLM gives
    up)."""
    executor = AutonomousAgentExecutor(mock_session_factory)
    connection_id = uuid4()

    with patch(
        "src.services.execution.autonomous_agent_executor.mcp_dispatch.invoke",
        new=AsyncMock(
            side_effect=NeedsReauthError(
                reauth_url=f"/me/connections/{connection_id}/connect",
                connection_id=connection_id,
            )
        ),
    ):
        with pytest.raises(ToolError) as excinfo:
            await executor._execute_mcp_tool(
                ToolCallRequest(
                    id="tc-3",
                    name=f"{MCP_TOOL_PREFIX}{connection_id}__t",
                    arguments={},
                ),
                connection_id=connection_id,
                remote_tool_name="t",
            )

    assert "needs reauth" in str(excinfo.value).lower()


@pytest.mark.asyncio
async def test_execute_mcp_tool_raises_tool_error_on_misconfig(
    mock_session_factory,
):
    executor = AutonomousAgentExecutor(mock_session_factory)
    connection_id = uuid4()

    with patch(
        "src.services.execution.autonomous_agent_executor.mcp_dispatch.invoke",
        new=AsyncMock(
            side_effect=MisconfigError(
                connection_id=connection_id,
                reason="autonomous flag off",
            )
        ),
    ):
        with pytest.raises(ToolError) as excinfo:
            await executor._execute_mcp_tool(
                ToolCallRequest(
                    id="tc-4",
                    name=f"{MCP_TOOL_PREFIX}{connection_id}__t",
                    arguments={},
                ),
                connection_id=connection_id,
                remote_tool_name="t",
            )

    assert "misconfigured" in str(excinfo.value).lower()


@pytest.mark.asyncio
async def test_execute_mcp_tool_raises_tool_error_on_dispatch_error(
    mock_session_factory,
):
    executor = AutonomousAgentExecutor(mock_session_factory)
    connection_id = uuid4()

    with patch(
        "src.services.execution.autonomous_agent_executor.mcp_dispatch.invoke",
        new=AsyncMock(
            side_effect=ToolDispatchError(
                "remote returned 500",
                connection_id=connection_id,
                tool_name="t",
            )
        ),
    ):
        with pytest.raises(ToolError):
            await executor._execute_mcp_tool(
                ToolCallRequest(
                    id="tc-5",
                    name=f"{MCP_TOOL_PREFIX}{connection_id}__t",
                    arguments={},
                ),
                connection_id=connection_id,
                remote_tool_name="t",
            )


@pytest.mark.asyncio
async def test_execute_tool_routes_mcp_prefix(mock_session_factory):
    """The tool dispatcher in _execute_tool routes ``mcp__<uuid>__<tool>``
    to _execute_mcp_tool BEFORE workflow-id lookup."""
    executor = AutonomousAgentExecutor(mock_session_factory)
    executor._caller_user_id = uuid4()

    connection_id = uuid4()
    fake_envelope = {"content": [], "is_error": False}

    agent = MagicMock()
    agent.system_tools = []
    agent.knowledge_sources = []
    agent.organization_id = uuid4()

    with patch(
        "src.services.execution.autonomous_agent_executor.mcp_dispatch.invoke",
        new=AsyncMock(return_value=fake_envelope),
    ) as mock_invoke:
        await executor._execute_tool(
            ToolCallRequest(
                id="tc-6",
                name=f"{MCP_TOOL_PREFIX}{connection_id}__t",
                arguments={},
            ),
            agent,
        )

    assert mock_invoke.await_count == 1
    assert mock_invoke.await_args is not None
    assert (
        mock_invoke.await_args.kwargs["caller_user_id"]
        == executor._caller_user_id
    )


# ---------------------------------------------------------------------------
# run() — _caller threading
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch(
    "src.services.execution.autonomous_agent_executor.get_llm_client"
)
@patch(
    "src.services.execution.autonomous_agent_executor.resolve_agent_tools"
)
async def test_run_threads_user_id_from_caller(
    mock_resolve_tools, mock_get_llm, mock_session_factory, mock_agent
):
    """``run(_caller={'user_id': '...'})`` parses to UUID and stores on
    ``self._caller_user_id`` so dispatch can pick it up."""
    mock_resolve_tools.return_value = ([], {})
    mock_llm = AsyncMock()
    mock_llm.complete = AsyncMock(
        return_value=LLMResponse(
            content="done",
            tool_calls=None,
            finish_reason="end_turn",
            input_tokens=10,
            output_tokens=5,
        )
    )
    mock_llm.provider_name = "openai"
    mock_get_llm.return_value = mock_llm

    user_id = uuid4()
    executor = AutonomousAgentExecutor(mock_session_factory)
    await executor.run(
        agent=mock_agent,
        input_data={"task": "x"},
        run_id=str(uuid4()),
        _caller={"user_id": str(user_id), "email": "a@b"},
    )

    # The executor stored the parsed UUID; resolve_agent_tools saw it
    assert executor._caller_user_id == user_id
    assert mock_resolve_tools.await_count == 1
    assert mock_resolve_tools.await_args is not None
    assert (
        mock_resolve_tools.await_args.kwargs["caller_user_id"] == user_id
    )


@pytest.mark.asyncio
@patch(
    "src.services.execution.autonomous_agent_executor.get_llm_client"
)
@patch(
    "src.services.execution.autonomous_agent_executor.resolve_agent_tools"
)
async def test_run_treats_missing_caller_as_autonomous(
    mock_resolve_tools, mock_get_llm, mock_session_factory, mock_agent
):
    """``run(_caller=None)`` → ``self._caller_user_id`` is None and
    resolve_agent_tools sees None (autonomous-only filtering applies)."""
    mock_resolve_tools.return_value = ([], {})
    mock_llm = AsyncMock()
    mock_llm.complete = AsyncMock(
        return_value=LLMResponse(
            content="done",
            tool_calls=None,
            finish_reason="end_turn",
            input_tokens=1,
            output_tokens=1,
        )
    )
    mock_llm.provider_name = "openai"
    mock_get_llm.return_value = mock_llm

    executor = AutonomousAgentExecutor(mock_session_factory)
    await executor.run(
        agent=mock_agent,
        input_data={"task": "x"},
        run_id=str(uuid4()),
        _caller=None,
    )

    assert executor._caller_user_id is None
    assert mock_resolve_tools.await_args is not None
    assert mock_resolve_tools.await_args.kwargs["caller_user_id"] is None


@pytest.mark.asyncio
@patch(
    "src.services.execution.autonomous_agent_executor.get_llm_client"
)
@patch(
    "src.services.execution.autonomous_agent_executor.resolve_agent_tools"
)
async def test_run_treats_caller_without_user_id_as_autonomous(
    mock_resolve_tools, mock_get_llm, mock_session_factory, mock_agent
):
    """``_caller={'email': '...'}`` (no user_id key) is autonomous —
    e.g. an unauthenticated webhook trigger that still records its
    source in the audit log."""
    mock_resolve_tools.return_value = ([], {})
    mock_llm = AsyncMock()
    mock_llm.complete = AsyncMock(
        return_value=LLMResponse(
            content="done",
            tool_calls=None,
            finish_reason="end_turn",
            input_tokens=1,
            output_tokens=1,
        )
    )
    mock_llm.provider_name = "openai"
    mock_get_llm.return_value = mock_llm

    executor = AutonomousAgentExecutor(mock_session_factory)
    await executor.run(
        agent=mock_agent,
        input_data={"task": "x"},
        run_id=str(uuid4()),
        _caller={"email": "webhook@source.example", "name": "webhook"},
    )

    assert executor._caller_user_id is None
    assert mock_resolve_tools.await_args is not None
    assert mock_resolve_tools.await_args.kwargs["caller_user_id"] is None


@pytest.mark.asyncio
@patch(
    "src.services.execution.autonomous_agent_executor.get_llm_client"
)
@patch(
    "src.services.execution.autonomous_agent_executor.resolve_agent_tools"
)
async def test_run_invalid_user_id_falls_back_to_autonomous(
    mock_resolve_tools, mock_get_llm, mock_session_factory, mock_agent
):
    """A malformed user_id (non-UUID string) is logged as a warning and
    treated as autonomous rather than raising — the run should not crash
    on a malformed audit-log entry."""
    mock_resolve_tools.return_value = ([], {})
    mock_llm = AsyncMock()
    mock_llm.complete = AsyncMock(
        return_value=LLMResponse(
            content="done",
            tool_calls=None,
            finish_reason="end_turn",
            input_tokens=1,
            output_tokens=1,
        )
    )
    mock_llm.provider_name = "openai"
    mock_get_llm.return_value = mock_llm

    executor = AutonomousAgentExecutor(mock_session_factory)
    result = await executor.run(
        agent=mock_agent,
        input_data={"task": "x"},
        run_id=str(uuid4()),
        _caller={"user_id": "not-a-uuid"},
    )

    assert result["status"] == "completed"
    assert executor._caller_user_id is None
    assert mock_resolve_tools.await_args is not None
    assert mock_resolve_tools.await_args.kwargs["caller_user_id"] is None
