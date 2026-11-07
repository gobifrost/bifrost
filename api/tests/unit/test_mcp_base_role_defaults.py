"""Provisioning-default MCP tools preserve the REST boundary and null reset."""

from unittest.mock import AsyncMock, patch

import pytest

from src.services.mcp_server.tools.base_role_defaults import (
    bifrost_base_role_default_get,
    bifrost_base_role_default_update,
)


@pytest.mark.asyncio
async def test_read_forwards_organization_scope():
    with patch("src.services.mcp_server.tools.base_role_defaults.call_rest", new=AsyncMock(return_value=(200, {"source": "organization"}))) as rest:
        context = object()
        result = await bifrost_base_role_default_get(context, "org-id")
    rest.assert_awaited_once_with(context, "GET", "/api/base-role-defaults", params={"organization_id": "org-id"})
    assert result.structured_content == {"source": "organization"}


@pytest.mark.asyncio
async def test_reset_forwards_null_and_preserves_http_failure():
    with patch("src.services.mcp_server.tools.base_role_defaults.call_rest", new=AsyncMock(return_value=(403, {"detail": "Denied"}))) as rest:
        context = object()
        result = await bifrost_base_role_default_update(context, None)
    rest.assert_awaited_once_with(context, "PUT", "/api/base-role-defaults", params={}, json_body={"default_base_role_id": None})
    assert result.is_error
    assert result.structured_content == {
        "error": "Could not update base role default: HTTP 403",
        "body": {"detail": "Denied"},
    }
