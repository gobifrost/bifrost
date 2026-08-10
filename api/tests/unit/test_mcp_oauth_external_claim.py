"""
LEAK #1 failing-first proof: the MCP OAuth token endpoint minted access tokens
WITHOUT the is_external claim, so every MCP-OAuth session was is_external=False
(the verifier reads payload.get("is_external", False)). Both the
authorization_code and refresh_token grants must stamp
``is_external = resolve_external_claim(db, user)``.
"""

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.services.mcp_server.auth import BifrostAuthProvider


def _external_user():
    return SimpleNamespace(
        id=uuid4(),
        email="guest@portal.example",
        name="Guest",
        is_superuser=False,
        is_external=True,
        organization_id=uuid4(),
    )


@asynccontextmanager
async def _fake_db_ctx(_db):
    yield _db


def _patches(captured: dict, user):
    """Patch every external dependency of _token at its source module."""
    db = AsyncMock()

    def _capture_access(data=None, **_kw):
        captured["token_data"] = data
        return "minted.jwt"

    user_repo = MagicMock()
    user_repo.get_by_id = AsyncMock(return_value=user)

    return [
        patch("src.core.security.create_access_token", side_effect=_capture_access),
        patch(
            "src.core.security.create_refresh_token",
            return_value=("refresh.jwt", "jti"),
        ),
        patch(
            "src.core.database.get_db_context",
            lambda: _fake_db_ctx(db),
        ),
        patch(
            "src.repositories.users.UserRepository",
            return_value=user_repo,
        ),
        patch(
            "src.services.mcp_server.auth.resolve_external_claim",
            new=AsyncMock(return_value=user.is_external),
        ),
    ]


@pytest.mark.asyncio
async def test_auth_code_grant_stamps_is_external():
    user = _external_user()
    captured: dict = {}

    provider = BifrostAuthProvider(base_url="http://test")

    # Redis: return the stored auth-code data, then accept delete.
    auth_code_data = {
        "user_id": str(user.id),
        "email": user.email,
        "name": user.name,
        "is_superuser": False,
        "org_id": str(user.organization_id),
        "code_challenge": "chal",
        "redirect_uri": "http://cb",
        "client_id": "c",
        "scope": "mcp:access",
    }
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=json.dumps(auth_code_data))
    redis.delete = AsyncMock()

    form = {
        "grant_type": "authorization_code",
        "code": "thecode",
        "redirect_uri": "http://cb",
        "code_verifier": "verifier",
        "client_id": "c",
    }
    request = MagicMock()
    request.form = AsyncMock(return_value=form)

    ctx = _patches(captured, user)
    # PKCE check compares sha256(verifier) to the stored challenge; patch the
    # stored challenge to match the verifier so we exercise the mint path.
    import base64
    import hashlib

    expected = base64.urlsafe_b64encode(
        hashlib.sha256("verifier".encode()).digest()
    ).rstrip(b"=").decode()
    auth_code_data["code_challenge"] = expected
    redis.get = AsyncMock(return_value=json.dumps(auth_code_data))

    with patch("src.core.cache.get_shared_redis", new=AsyncMock(return_value=redis)):
        for p in ctx:
            p.start()
        try:
            await provider._token(request)
        finally:
            for p in ctx:
                p.stop()

    assert "token_data" in captured, "create_access_token was never called"
    assert captured["token_data"].get("is_external") is True


@pytest.mark.asyncio
async def test_refresh_grant_stamps_is_external():
    user = _external_user()
    captured: dict = {}

    provider = BifrostAuthProvider(base_url="http://test")

    form = {"grant_type": "refresh_token", "refresh_token": "rt"}
    request = MagicMock()
    request.form = AsyncMock(return_value=form)

    ctx = _patches(captured, user)
    with patch(
        "src.core.security.decode_token", return_value={"sub": str(user.id)}
    ):
        for p in ctx:
            p.start()
        try:
            await provider._token(request)
        finally:
            for p in ctx:
                p.stop()

    assert "token_data" in captured, "create_access_token was never called"
    assert captured["token_data"].get("is_external") is True
