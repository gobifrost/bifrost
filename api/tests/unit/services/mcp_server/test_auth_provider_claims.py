"""Tests for claims preserved at the FastMCP authentication boundary."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.services.mcp_server.auth import BifrostAuthProvider, MCPAccessToken
from src.services.mcp_server.server import MCPContext
from src.services.mcp_server.tools._http_bridge import _token_from_context


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


@pytest.mark.asyncio
async def test_verify_token_preserves_canonical_scope_bypass_claims():
    user_id = uuid4()
    org_id = uuid4()
    payload = {
        "sub": str(user_id),
        "email": "provider@example.com",
        "name": "Provider User",
        "is_superuser": False,
        "is_provider_org": True,
        "is_external": False,
        "org_id": str(org_id),
        "exp": 1234567890,
    }
    provider = BifrostAuthProvider(base_url="http://test")

    with (
        patch("src.core.security.decode_token", return_value=payload),
        patch.object(provider, "_check_mcp_access", new=AsyncMock(return_value=True)),
        patch.object(provider, "_get_user_roles", new=AsyncMock(return_value=[])),
    ):
        token = await provider.verify_token("encoded.jwt")

    assert token is not None
    assert token.claims["is_superuser"] is False
    assert token.claims["is_provider_org"] is True


def test_http_bridge_fallback_preserves_provider_org_claim():
    from src.core.security import decode_token

    context = MCPContext(
        user_id=uuid4(),
        org_id=uuid4(),
        is_provider_org=True,
        user_email="provider@example.com",
    )

    payload = decode_token(_token_from_context(context), expected_type="access")

    assert payload is not None
    assert payload["is_provider_org"] is True


def test_agent_bridge_token_preserves_run_id_and_external_account_flag():
    from src.core.security import decode_token

    person, run = uuid4(), uuid4()
    context = MCPContext(
        user_id=person, org_id=uuid4(), run_user_id=person, agent_run_id=str(run),
        is_external=True, is_provider_org=True,
    )
    payload = decode_token(_token_from_context(context), expected_type="access")
    assert payload is not None
    assert payload["sub"] == str(person)
    assert payload["engine_agent_run_id"] == str(run)
    assert payload["engine_run_user_id"] == str(person)
    assert payload["is_external"] is True
