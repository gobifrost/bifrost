from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastmcp.exceptions import ToolError

from shared.authorization_scopes import PLATFORM_SUPERUSER_SCOPE
from src.services.authorization import AuthorizationBoundary, AuthorizationContext
from src.services.mcp_server import server


def _token(*, user_id, home_org_id, is_superuser: bool = False):
    return SimpleNamespace(
        claims={
            "user_id": str(user_id),
            "sub": str(user_id),
            "org_id": str(home_org_id) if home_org_id else None,
            "is_superuser": is_superuser,
            "is_external": False,
            "email": "operator@example.com",
            "name": "Operator",
            "roles": [],
        }
    )


def _authorization(user_id, home_org_id, boundary, *capabilities):
    principal = MagicMock()
    principal.user_id = user_id
    principal.organization_id = home_org_id
    return AuthorizationContext(
        requester=principal,
        effective_actor=principal,
        selected_boundary=boundary,
        effective_capabilities=frozenset(capabilities),
        grant_sources=(),
    )


@pytest.mark.asyncio
async def test_fastmcp_runtime_context_uses_selected_organization_header():
    user_id = uuid4()
    home_org_id = uuid4()
    selected_org_id = uuid4()

    with (
        patch(
            "fastmcp.server.dependencies.get_access_token",
            return_value=_token(
                user_id=user_id,
                home_org_id=home_org_id,
                is_superuser=True,
            ),
        ),
        patch(
            "fastmcp.server.dependencies.get_http_headers",
            return_value={
                "x-bifrost-boundary": f"organization:{selected_org_id}",
            },
        ),
        patch("src.services.mcp_server.server._get_agent_id_from_scope", return_value=None),
        patch(
            "src.core.database.get_db_context",
            return_value=MagicMock(
                __aenter__=AsyncMock(return_value=MagicMock()),
                __aexit__=AsyncMock(return_value=None),
            ),
        ),
        patch(
            "src.services.authorization.resolve_authorization_context",
            new=AsyncMock(
                return_value=_authorization(
                    user_id,
                    home_org_id,
                    AuthorizationBoundary.organization(selected_org_id),
                    PLATFORM_SUPERUSER_SCOPE,
                )
            ),
        ),
    ):
        context = await server._get_runtime_context()

    assert context.user_id == user_id
    assert context.org_id == selected_org_id
    assert context.is_platform_admin is False
    assert context.authorization_boundary == f"organization:{selected_org_id}"
    assert context.resource_gate_bypass is True


@pytest.mark.asyncio
async def test_fastmcp_runtime_context_rejects_managed_organizations_header():
    user_id = uuid4()
    home_org_id = uuid4()

    with (
        patch(
            "fastmcp.server.dependencies.get_access_token",
            return_value=_token(user_id=user_id, home_org_id=home_org_id),
        ),
        patch(
            "fastmcp.server.dependencies.get_http_headers",
            return_value={"x-bifrost-boundary": "managed_organizations"},
        ),
        patch("src.services.mcp_server.server._get_agent_id_from_scope", return_value=None),
    ):
        with pytest.raises(ToolError, match="Managed Organizations"):
            await server._get_runtime_context()
