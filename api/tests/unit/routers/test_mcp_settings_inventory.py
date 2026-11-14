"""Admin settings remain usable when runtime MCP access is restricted."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.routers.mcp import list_mcp_tools, mcp_status
from src.core.principal import UserPrincipal
from src.services.authorization.context import AuthorizationContext, Boundary, BoundaryKind, RoleGrant
from src.services.authorization.enforce import Caller
from src.services.mcp_server.tool_access import MCPToolAccessResult
from tests.helpers.authorization import admin_caller


def _runtime_caller() -> Caller:
    org_id = uuid4()
    principal = UserPrincipal(
        user_id=uuid4(), email="mcp-user@example.test", organization_id=org_id,
    )
    return Caller(
        principal,
        AuthorizationContext(
            user_id=principal.user_id,
            home_organization_id=org_id,
            base_role_id=uuid4(),
            is_external=False,
            base_permissions=frozenset(),
            role_grants=(
                RoleGrant(
                    uuid4(),
                    frozenset({"mcp.readbasic"}),
                    (Boundary(BoundaryKind.ORGANIZATION, org_id),),
                ),
            ),
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("is_admin,enabled", [(True, False), (True, True), (False, True), (False, False)])
async def test_settings_inventory_respects_admin_and_runtime_boundaries(is_admin, enabled):
    caller = admin_caller() if is_admin else _runtime_caller()
    session = AsyncMock()
    with (
        patch("src.routers.mcp.MCPConfigService") as config_cls,
        patch("src.services.mcp_server.tool_access.MCPToolAccessService") as tools_cls,
        patch("src.routers.mcp.load_caller", new=AsyncMock(return_value=caller)),
    ):
        config_cls.return_value.get_config = AsyncMock(
            return_value=SimpleNamespace(enabled=enabled),
        )
        inventory = AsyncMock(return_value=MCPToolAccessResult(tools=[]))
        tools_cls.return_value.get_accessible_tools = inventory
        if not is_admin and not enabled:
            with pytest.raises(HTTPException) as raised:
                await list_mcp_tools(caller.principal, session)
            assert raised.value.status_code == 403
            inventory.assert_not_awaited()
        else:
            result = await list_mcp_tools(caller.principal, session)
            assert result.tools == []
            assert inventory.await_args.kwargs["for_configuration"] is is_admin


@pytest.mark.asyncio
@pytest.mark.parametrize("permitted", [False, True])
async def test_mcp_status_matches_permission_enabled_default_tools(permitted):
    from src.services.mcp_server.tools.gateway import GATEWAY_TOOL_NAMES

    caller, session = _runtime_caller(), AsyncMock()
    tools = [SimpleNamespace(id="bifrost_execute_code")] if permitted else []
    with (
        patch("src.routers.mcp.MCPConfigService") as config_cls,
        patch("src.services.mcp_server.tool_access.MCPToolAccessService") as tools_cls,
        patch("src.routers.mcp.load_caller", new=AsyncMock(return_value=caller)),
        patch("src.routers.mcp._gateway_service") as gateway,
    ):
        config_cls.return_value.get_config = AsyncMock(return_value=SimpleNamespace(enabled=True))
        tools_cls.return_value.get_default_tools = AsyncMock(return_value=tools)
        gateway.return_value.accessible_agent_count = AsyncMock(return_value=0)
        result = await mcp_status(caller.principal, session)

    expected = set(GATEWAY_TOOL_NAMES) | ({"bifrost_execute_code"} if permitted else set())
    assert set(result["tools"]) == expected
    assert result["tools_count"] == len(expected)
