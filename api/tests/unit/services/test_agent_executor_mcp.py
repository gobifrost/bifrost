"""Unit tests for AgentExecutor MCP-tool dispatch.

We don't run the full chat() flow here — that involves message
persistence, LLM streaming, and conversation lifecycle. The Phase 3
contract we're testing is narrow:

- ``_execute_tool`` routes ``mcp__<connection_id>__<tool>`` names to
  ``_execute_mcp_tool`` BEFORE the workflow-tool fallback.
- ``_execute_mcp_tool`` calls ``mcp_client.dispatch.invoke`` with the
  caller_user_id passed in by the chat loop.
- ``NeedsReauthError`` from dispatch becomes a ``ToolResult`` with
  ``error_type='needs_reauth'`` and a ``metadata`` payload carrying
  ``reauth_url`` / ``connection_id`` / ``tool_name``.

DB session_factory is mocked because the dispatch path does the actual
DB read; for the unit test we patch dispatch.invoke directly.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.models.contracts.agents import ToolResult
from src.services.agent_executor import AgentExecutor
from src.services.execution.agent_helpers import MCP_TOOL_PREFIX
from src.services.llm.base import ToolCallRequest
from src.services.mcp_client.errors import (
    MisconfigError,
    NeedsReauthError,
    ToolDispatchError,
)


@pytest.fixture
def mock_session_factory():
    """A mock async session factory whose ``__aenter__`` returns a session
    that ``select(...).where(MCPConnection.id == ...)`` resolves to a
    fake connection.

    The connection itself is just a placeholder — dispatch.invoke is
    patched in the tests, so the real connection contents don't matter.
    """
    fake_connection = MagicMock()
    fake_connection.id = uuid4()

    fake_result = MagicMock()
    fake_result.scalar_one_or_none = MagicMock(return_value=fake_connection)

    session = AsyncMock()
    session.execute = AsyncMock(return_value=fake_result)
    session.commit = AsyncMock()

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)

    factory = MagicMock(return_value=ctx)
    factory._fake_connection = fake_connection
    return factory


@pytest.mark.asyncio
async def test_execute_mcp_tool_passes_caller_user_id(mock_session_factory):
    """When the chat loop calls _execute_mcp_tool with a caller_user_id,
    that value reaches mcp_client.dispatch.invoke verbatim."""
    executor = AgentExecutor(mock_session_factory)
    connection_id = uuid4()
    caller_user_id = uuid4()

    fake_envelope = {
        "content": [{"type": "text", "text": "ok"}],
        "structured_content": {"answer": 42},
        "is_error": False,
        "_resolution_path": "user_token",
    }

    with patch(
        "src.services.agent_executor.mcp_dispatch.invoke",
        new=AsyncMock(return_value=fake_envelope),
    ) as mock_invoke:
        result = await executor._execute_mcp_tool(
            ToolCallRequest(
                id="tc-1",
                name=f"{MCP_TOOL_PREFIX}{connection_id}__graph_search",
                arguments={"q": "hello"},
            ),
            connection_id=connection_id,
            remote_tool_name="graph_search",
            caller_user_id=caller_user_id,
            start_time=0.0,
        )

    assert mock_invoke.await_count == 1
    assert mock_invoke.await_args is not None
    call_kwargs = mock_invoke.await_args.kwargs
    assert call_kwargs["caller_user_id"] == caller_user_id
    assert call_kwargs["tool_name"] == "graph_search"
    assert call_kwargs["arguments"] == {"q": "hello"}
    # Connection passed through is the one returned by the session execute()
    assert call_kwargs["connection"] is mock_session_factory._fake_connection

    assert isinstance(result, ToolResult)
    assert result.error is None
    assert result.error_type is None
    assert result.result == fake_envelope


@pytest.mark.asyncio
async def test_execute_mcp_tool_passes_none_caller_for_autonomous(
    mock_session_factory,
):
    """``caller_user_id=None`` is passed through (used for chat-trigger
    callers without a user, though chat normally always has one)."""
    executor = AgentExecutor(mock_session_factory)
    connection_id = uuid4()

    with patch(
        "src.services.agent_executor.mcp_dispatch.invoke",
        new=AsyncMock(return_value={"content": [], "is_error": False}),
    ) as mock_invoke:
        await executor._execute_mcp_tool(
            ToolCallRequest(
                id="tc-2",
                name=f"{MCP_TOOL_PREFIX}{connection_id}__t",
                arguments={},
            ),
            connection_id=connection_id,
            remote_tool_name="t",
            caller_user_id=None,
            start_time=0.0,
        )

    assert mock_invoke.await_args is not None
    assert mock_invoke.await_args.kwargs["caller_user_id"] is None


@pytest.mark.asyncio
async def test_execute_mcp_tool_translates_needs_reauth(mock_session_factory):
    """NeedsReauthError → ToolResult with error_type='needs_reauth' and
    a metadata payload the chat surface uses to render an inline
    reconnect button."""
    executor = AgentExecutor(mock_session_factory)
    connection_id = uuid4()
    reauth_url = f"/me/connections/{connection_id}/connect"

    with patch(
        "src.services.agent_executor.mcp_dispatch.invoke",
        new=AsyncMock(
            side_effect=NeedsReauthError(
                reauth_url=reauth_url,
                connection_id=connection_id,
                tool_name="graph_search",
            )
        ),
    ):
        result = await executor._execute_mcp_tool(
            ToolCallRequest(
                id="tc-3",
                name=f"{MCP_TOOL_PREFIX}{connection_id}__graph_search",
                arguments={},
            ),
            connection_id=connection_id,
            remote_tool_name="graph_search",
            caller_user_id=uuid4(),
            start_time=0.0,
        )

    assert result.error_type == "needs_reauth"
    assert result.error is not None
    assert result.metadata == {
        "reauth_url": reauth_url,
        "connection_id": str(connection_id),
        "tool_name": "graph_search",
    }
    assert result.result is None


@pytest.mark.asyncio
async def test_execute_mcp_tool_translates_misconfig(mock_session_factory):
    """MisconfigError → ToolResult with error_type='misconfig' so logs/UI
    can distinguish a planner bug from a generic dispatch failure."""
    executor = AgentExecutor(mock_session_factory)
    connection_id = uuid4()

    with patch(
        "src.services.agent_executor.mcp_dispatch.invoke",
        new=AsyncMock(
            side_effect=MisconfigError(
                connection_id=connection_id,
                reason="autonomous flag off",
            )
        ),
    ):
        result = await executor._execute_mcp_tool(
            ToolCallRequest(
                id="tc-4",
                name=f"{MCP_TOOL_PREFIX}{connection_id}__t",
                arguments={},
            ),
            connection_id=connection_id,
            remote_tool_name="t",
            caller_user_id=None,
            start_time=0.0,
        )

    assert result.error_type == "misconfig"
    assert result.metadata == {"connection_id": str(connection_id)}


@pytest.mark.asyncio
async def test_execute_mcp_tool_translates_dispatch_error(mock_session_factory):
    """ToolDispatchError → ToolResult with plain error message (no
    error_type / metadata — these are ordinary tool failures)."""
    executor = AgentExecutor(mock_session_factory)
    connection_id = uuid4()

    with patch(
        "src.services.agent_executor.mcp_dispatch.invoke",
        new=AsyncMock(
            side_effect=ToolDispatchError(
                "remote returned 500",
                connection_id=connection_id,
                tool_name="t",
            )
        ),
    ):
        result = await executor._execute_mcp_tool(
            ToolCallRequest(
                id="tc-5",
                name=f"{MCP_TOOL_PREFIX}{connection_id}__t",
                arguments={},
            ),
            connection_id=connection_id,
            remote_tool_name="t",
            caller_user_id=uuid4(),
            start_time=0.0,
        )

    assert result.error is not None
    assert "remote returned 500" in result.error
    assert result.error_type is None
    assert result.metadata is None


@pytest.mark.asyncio
async def test_execute_mcp_tool_handles_missing_connection():
    """If the connection row has been deleted between planning and
    dispatch, return a non-error_type ToolResult with a clear message."""
    fake_result = MagicMock()
    fake_result.scalar_one_or_none = MagicMock(return_value=None)
    session = AsyncMock()
    session.execute = AsyncMock(return_value=fake_result)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=ctx)

    executor = AgentExecutor(factory)
    connection_id = uuid4()

    with patch(
        "src.services.agent_executor.mcp_dispatch.invoke",
        new=AsyncMock(),
    ) as mock_invoke:
        result = await executor._execute_mcp_tool(
            ToolCallRequest(
                id="tc-6",
                name=f"{MCP_TOOL_PREFIX}{connection_id}__t",
                arguments={},
            ),
            connection_id=connection_id,
            remote_tool_name="t",
            caller_user_id=uuid4(),
            start_time=0.0,
        )

    assert mock_invoke.await_count == 0
    assert result.error is not None
    assert "not found" in result.error.lower()


@pytest.mark.asyncio
async def test_execute_tool_routes_mcp_prefix_to_mcp_dispatch(
    mock_session_factory,
):
    """The tool-name dispatcher in _execute_tool routes the
    ``mcp__<uuid>__<tool>`` pattern to _execute_mcp_tool BEFORE falling
    through to the workflow lookup. We assert this by patching
    dispatch.invoke and confirming it ran for an agent with no MCP tools
    in its workflow id_map."""
    executor = AgentExecutor(mock_session_factory)
    connection_id = uuid4()
    caller_user_id = uuid4()

    # Build a minimal mock agent — _execute_tool checks
    # ``agent.system_tools`` and uses ``startswith('delegate_to_')`` so a
    # bare MagicMock works.
    agent = MagicMock()
    agent.system_tools = []
    agent.knowledge_sources = []
    agent.delegated_agents = []

    fake_envelope = {"content": [], "is_error": False}

    with patch(
        "src.services.agent_executor.mcp_dispatch.invoke",
        new=AsyncMock(return_value=fake_envelope),
    ) as mock_invoke:
        await executor._execute_tool(
            ToolCallRequest(
                id="tc-7",
                name=f"{MCP_TOOL_PREFIX}{connection_id}__graph_search",
                arguments={"q": "hi"},
            ),
            agent=agent,
            conversation=None,
            execution_id=None,
            caller_user_id=caller_user_id,
        )

    assert mock_invoke.await_count == 1
    assert mock_invoke.await_args is not None
    assert (
        mock_invoke.await_args.kwargs["caller_user_id"] == caller_user_id
    )
    assert mock_invoke.await_args.kwargs["tool_name"] == "graph_search"
