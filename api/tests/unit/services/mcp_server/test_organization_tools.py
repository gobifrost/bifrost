"""Unit tests for organization MCP tool authorization."""

from uuid import uuid4

import pytest

from src.services.mcp_server.server import MCPContext


@pytest.fixture
def org_user_context() -> MCPContext:
    """Create an MCPContext for a regular organization user."""
    return MCPContext(
        user_id=str(uuid4()),
        org_id=str(uuid4()),
        is_platform_admin=False,
        user_email="user@example.com",
        user_name="Org User",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "args"),
    [
        ("list_organizations", ()),
        ("get_organization", (str(uuid4()),)),
        ("create_organization", ("Test Org",)),
        ("update_organization", (str(uuid4()),)),
        ("delete_organization", (str(uuid4()),)),
    ],
)
async def test_non_admin_organization_tools_return_error(
    org_user_context: MCPContext,
    tool_name: str,
    args: tuple[object, ...],
) -> None:
    import src.services.mcp_server.tools.organizations as organizations

    result = await getattr(organizations, tool_name)(org_user_context, *args)

    assert result.structured_content is not None
    assert "Platform administrator privileges are required" in result.structured_content["error"]
