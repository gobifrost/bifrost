"""Reach and permission boundaries for shared MCP connection lookups."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from src.core.principal import UserPrincipal
from src.repositories.external_mcp import MCPConnectionRepository
from src.routers import mcp_connections as router
from src.services.authorization.context import AuthorizationContext
from src.services.authorization.enforce import Caller


CONTOSO = UUID("11111111-1111-1111-1111-111111111111")
FABRIKAM = UUID("22222222-2222-2222-2222-222222222222")
_OPERATION = "GET /api/mcp-connections/{connection_id}"


def _caller(*, home_organization_id: UUID, permissions: frozenset[str]) -> Caller:
    user_id = uuid4()
    principal = UserPrincipal(
        user_id=user_id,
        email="reader@example.com",
        organization_id=home_organization_id,
        is_verified=True,
    )
    return Caller(
        principal,
        AuthorizationContext(
            user_id=user_id,
            home_organization_id=home_organization_id,
            base_role_id=uuid4(),
            is_external=False,
            base_permissions=permissions,
        ),
    )


def _stub_connection(organization_id: UUID) -> SimpleNamespace:
    return SimpleNamespace(id=uuid4(), organization_id=organization_id)


@pytest.mark.asyncio
async def test_shared_connection_read_returns_own_reachable_connection(monkeypatch):
    caller = _caller(home_organization_id=FABRIKAM, permissions=frozenset({"mcp.readbasic"}))
    connection = _stub_connection(FABRIKAM)
    get_connection = AsyncMock(return_value=connection)
    monkeypatch.setattr(router, "load_caller", AsyncMock(return_value=caller))
    monkeypatch.setattr(MCPConnectionRepository, "get_connection", get_connection)

    result = await router._get_shared_connection_or_404(
        SimpleNamespace(db=object()), caller.principal, connection.id, _OPERATION
    )

    assert result is connection
    get_connection.assert_awaited_once_with(connection.id)


@pytest.mark.asyncio
async def test_shared_connection_read_masks_foreign_connection_outside_permission_reach(monkeypatch):
    caller = _caller(home_organization_id=FABRIKAM, permissions=frozenset({"mcp.readbasic"}))
    connection = _stub_connection(CONTOSO)
    get_connection = AsyncMock(return_value=connection)
    monkeypatch.setattr(router, "load_caller", AsyncMock(return_value=caller))
    monkeypatch.setattr(MCPConnectionRepository, "get_connection", get_connection)

    with pytest.raises(HTTPException) as exc:
        await router._get_shared_connection_or_404(
            SimpleNamespace(db=object()), caller.principal, connection.id, _OPERATION
        )

    assert exc.value.status_code == 404
    get_connection.assert_awaited_once_with(connection.id)


@pytest.mark.asyncio
async def test_shared_connection_read_without_named_permission_is_forbidden(monkeypatch):
    caller = _caller(home_organization_id=FABRIKAM, permissions=frozenset())
    get_connection = AsyncMock()
    monkeypatch.setattr(router, "load_caller", AsyncMock(return_value=caller))
    monkeypatch.setattr(MCPConnectionRepository, "get_connection", get_connection)

    with pytest.raises(HTTPException) as exc:
        await router._get_shared_connection_or_404(
            SimpleNamespace(db=object()), caller.principal, uuid4(), _OPERATION
        )

    assert exc.value.status_code == 403
    get_connection.assert_not_awaited()
