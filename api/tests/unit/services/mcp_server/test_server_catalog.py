"""The public MCP catalog is the stable gateway surface."""

from unittest.mock import MagicMock, patch
from uuid import uuid4

from src.services.mcp_server import server as mcp_server
from src.services.mcp_server.server import BifrostMCPServer, MCPContext


def test_fastmcp_server_registers_only_gateway_tools():
    """Neither MCP mount exposes native tools from the process-local registry."""
    fastmcp = MagicMock()
    context = MCPContext(user_id=uuid4(), org_id=uuid4())

    with (
        patch("src.services.mcp_server.server.HAS_FASTMCP", True),
        patch("src.services.mcp_server.server._FastMCP", return_value=fastmcp),
        patch("src.services.mcp_server.server.register_gateway_tools") as register_gateway,
    ):
        result = BifrostMCPServer(context).get_fastmcp_server(auth=MagicMock())

    assert result is fastmcp
    register_gateway.assert_called_once()
    assert not hasattr(mcp_server, "register_all_tools")
