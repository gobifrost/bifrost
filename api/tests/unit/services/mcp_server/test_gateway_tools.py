from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.services.mcp_server.server import MCPContext
from src.services.mcp_server.tools import gateway


@pytest.mark.asyncio
async def test_execute_tool_calls_agent_path_when_agent_selected():
    context = MCPContext(user_id=uuid4(), org_id=uuid4())
    agent_id = str(uuid4())
    tool_ref = str(uuid4())

    with patch(
        "src.services.mcp_server.tools.gateway.call_rest",
        new=AsyncMock(return_value=(200, {"result": {"ok": True}})),
    ) as call_rest:
        await gateway.bifrost_execute_tool(
            context,
            tool_ref,
            {"message": "hi"},
            agent_id=agent_id,
        )

    call_rest.assert_awaited_once_with(
        context,
        "POST",
        f"/api/mcp/gateway/agents/{agent_id}/tools/{tool_ref}/execute",
        json_body={"arguments": {"message": "hi"}, "async": None},
    )


@pytest.mark.asyncio
async def test_execute_tool_calls_builder_session_path_when_builder_selected():
    context = MCPContext(user_id=uuid4(), org_id=uuid4())
    builder_session_id = str(uuid4())
    tool_ref = str(uuid4())

    with patch(
        "src.services.mcp_server.tools.gateway.call_rest",
        new=AsyncMock(return_value=(200, {"result": {"ok": True}})),
    ) as call_rest:
        await gateway.bifrost_execute_tool(
            context,
            tool_ref,
            {"path": "README.md"},
            builder_session_id=builder_session_id,
            async_=False,
        )

    call_rest.assert_awaited_once_with(
        context,
        "POST",
        "/api/mcp/gateway/builder-sessions/"
        f"{builder_session_id}/tools/{tool_ref}/execute",
        json_body={"arguments": {"path": "README.md"}, "async": False},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("agent_id", "builder_session_id"),
    [(None, None), (str(uuid4()), str(uuid4()))],
)
async def test_execute_tool_requires_exactly_one_selector(
    agent_id: str | None,
    builder_session_id: str | None,
):
    context = MCPContext(user_id=uuid4(), org_id=uuid4())

    with patch(
        "src.services.mcp_server.tools.gateway.call_rest",
        new=AsyncMock(),
    ) as call_rest:
        result = await gateway.bifrost_execute_tool(
            context,
            str(uuid4()),
            {},
            agent_id=agent_id,
            builder_session_id=builder_session_id,
        )

    call_rest.assert_not_called()
    assert result.structured_content is not None
    assert "exactly one selector" in result.structured_content["error"]
