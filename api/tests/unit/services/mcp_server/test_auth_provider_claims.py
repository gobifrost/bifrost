"""Tests for claims preserved at the FastMCP authentication boundary."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.services.mcp_server.auth import BifrostAuthProvider


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
