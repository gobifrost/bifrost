"""MCP OAuth delegates human credential minting to the shared session service."""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.services.mcp_server.auth import BifrostAuthProvider, _seal_auth_code_record


@asynccontextmanager
async def _fake_db_ctx(db):
    yield db


def _session_tokens() -> SimpleNamespace:
    return SimpleNamespace(
        access_token="minted.jwt",
        refresh_token="refresh.jwt",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    )


@pytest.mark.asyncio
async def test_auth_code_grant_issues_a_shared_human_session():
    user = SimpleNamespace(id=uuid4(), is_active=True, identity_kind=None)
    verifier = "verifier"
    import base64
    import hashlib

    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    code_data = {
        "user_id": str(user.id),
        "email": "guest@portal.example",
        "name": "Guest",
        "code_challenge": challenge,
        "redirect_uri": "http://cb",
        "client_id": "c",
        "scope": "mcp:access",
    }
    redis = AsyncMock()
    redis.getdel = AsyncMock(return_value=_seal_auth_code_record("thecode", code_data))
    redis.delete = AsyncMock()
    request = MagicMock()
    request.form = AsyncMock(return_value={
        "grant_type": "authorization_code",
        "code": "thecode",
        "redirect_uri": "http://cb",
        "code_verifier": verifier,
        "client_id": "c",
    })
    db = AsyncMock()
    user_repo = MagicMock()
    user_repo.get_by_id = AsyncMock(return_value=user)
    issue = AsyncMock(return_value=_session_tokens())

    with (
        patch("src.core.cache.get_shared_redis", new=AsyncMock(return_value=redis)),
        patch("src.core.database.get_db_context", lambda: _fake_db_ctx(db)),
        patch("src.repositories.users.UserRepository", return_value=user_repo),
        patch("src.services.mcp_server.auth.issue_human_session", issue),
    ):
        response = await BifrostAuthProvider(base_url="http://test")._token(request)

    assert response.status_code == 200
    issue.assert_awaited_once_with(user.id)


@pytest.mark.asyncio
async def test_refresh_grant_returns_invalid_grant_when_shared_rotation_refuses():
    request = MagicMock()
    request.form = AsyncMock(return_value={"grant_type": "refresh_token", "refresh_token": "legacy"})

    from shared.human_sessions import HumanSessionUnavailable

    with patch(
        "src.services.mcp_server.auth.rotate_human_session",
        new=AsyncMock(side_effect=HumanSessionUnavailable("reauthentication is required")),
    ):
        response = await BifrostAuthProvider(base_url="http://test")._token(request)

    assert response.status_code == 400
    assert b'"error":"invalid_grant"' in response.body
