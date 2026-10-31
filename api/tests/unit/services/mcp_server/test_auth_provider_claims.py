"""Tests for claims preserved at the FastMCP authentication boundary."""

from uuid import uuid4

import pytest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from pydantic import ValidationError

from src.services.mcp_server.auth import BifrostAuthProvider, MCPAccessToken
from src.services.mcp_server.server import MCPContext
from src.services.mcp_server.tools._http_bridge import _human_subject


def test_mcp_access_token_rejects_missing_claims():
    with pytest.raises(ValidationError, match="claims"):
        MCPAccessToken(
            token="encoded.jwt",
            client_id="user-1",
            scopes=["mcp:access"],
        )


def test_mcp_access_token_rejects_null_claims():
    with pytest.raises(ValidationError, match="claims"):
        MCPAccessToken(
            token="encoded.jwt",
            client_id="user-1",
            scopes=["mcp:access"],
            claims=None,
        )


def test_mcp_access_token_preserves_policy_claims():
    claims = {
        "user_id": "user-1",
        "is_superuser": False,
        "is_provider_org": True,
        "runtime_credential_kind": "agent",
    }

    token = MCPAccessToken(
        token="encoded.jwt",
        client_id="user-1",
        scopes=["mcp:access"],
        claims=claims,
    )

    assert token.claims == claims


def test_http_bridge_human_subject_ignores_copied_context_flags():
    from src.services.authorization.tokens import HumanSubject
    context = MCPContext(user_id=uuid4(), org_id=uuid4(), is_provider_org=True, is_platform_admin=True)
    assert _human_subject(context) == HumanSubject(context.user_id)


async def test_mcp_human_verification_preserves_flags_roles_and_challenges_before_a_tool_effect():
    from httpx import ASGITransport, AsyncClient
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route
    from src.core.auth import REFRESH_REQUIRED_CHALLENGE
    from src.services.authorization.tokens import _encode_claims
    from tests.helpers.human_tokens import human_claims

    claims = human_claims(organization_id=uuid4(), provider=True, external=True)
    token = _encode_claims(claims)
    db = AsyncMock()
    rows = MagicMock()
    rows.scalar_one_or_none.return_value = int(claims.av)
    db.execute.return_value = rows

    @asynccontextmanager
    async def db_context():
        yield db

    provider = BifrostAuthProvider(base_url="http://test")
    effects: list[str] = []

    async def tool(_request):
        effects.append("ran")
        return JSONResponse({})

    with patch("src.core.database.get_db_context", db_context), patch.object(
        provider, "_check_mcp_access", AsyncMock(return_value=True)
    ), patch.object(provider, "_get_user_roles", AsyncMock(side_effect=AssertionError("claims supply roles"))):
        verified = await provider.verify_token(token)
        assert verified is not None
        assert verified.claims["is_provider_org"] is True
        assert verified.claims["is_external"] is True
        assert verified.claims["is_superuser"] is False
        assert verified.claims["roles"] == []
        rows.scalar_one_or_none.return_value = int(claims.av) + 1
        app = Starlette(routes=[Route("/tools", tool, methods=["POST"])], middleware=provider.get_middleware())
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
            response = await client.post("/tools", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == REFRESH_REQUIRED_CHALLENGE
        assert effects == []
