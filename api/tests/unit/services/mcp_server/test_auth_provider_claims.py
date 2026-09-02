"""Tests for claims preserved at the FastMCP authentication boundary."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.services.mcp_server.auth import BifrostAuthProvider
from src.services.mcp_server.server import MCPContext
from src.services.mcp_server.tools._http_bridge import _token_from_context


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
