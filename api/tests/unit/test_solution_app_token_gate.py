"""Default-deny gate for solution_app actor tokens on user auth paths.

A ``solution_app`` token reuses the launching user's ``sub``/``org_id``/
``email`` (the shared principal builder requires those claims), so nothing
except the ``actor_type`` claim distinguishes it from that user's own token.
Until the app-host routes exist, no path may accept one: a generated app that
exfiltrates its token must not be able to replay it against ``/api/tables``
and friends as the user.

These tests mint real tokens through ``mint_app_token`` and assert every
principal-building path refuses them, while an otherwise identical user token
still authenticates.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials

from src.core.auth import get_current_user, get_current_user_optional, get_current_user_ws
from src.core.security import create_access_token
from src.services.builder.app_session import ACTOR_TYPE, AppSession, mint_app_token
from src.services.mcp_server.auth import BifrostAuthProvider

USER_EMAIL = "launcher@example.com"


@pytest.fixture
def session() -> AppSession:
    """An app-host session standing in for a redeemed launch code."""
    return AppSession(
        session_id="sess-" + uuid4().hex,
        user_id=uuid4(),
        solution_id=uuid4(),
        app_id=uuid4(),
        organization_id=uuid4(),
        created_at=datetime.now(timezone.utc),
        path="/",
    )


@pytest.fixture
def app_token(session: AppSession) -> str:
    """A real solution_app token minted the way the app host mints them."""
    return mint_app_token(session, user_email=USER_EMAIL)


@pytest.fixture
def user_token(session: AppSession) -> str:
    """A normal user token for the same user/org as the app token."""
    return create_access_token({
        "sub": str(session.user_id),
        "email": USER_EMAIL,
        "name": "Launching User",
        "is_superuser": False,
        "org_id": str(session.organization_id),
    })


@pytest.fixture
def mock_request() -> MagicMock:
    request = MagicMock(spec=Request)
    request.cookies = {}
    return request


@pytest.fixture
def mock_db() -> MagicMock:
    return MagicMock()


def credentials(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def ws(*, cookies: dict | None = None, headers: dict | None = None) -> MagicMock:
    websocket = MagicMock()
    websocket.cookies = cookies or {}
    websocket.headers = headers or {}
    websocket.query_params = {}
    return websocket


def test_minted_token_carries_the_actor_marker(app_token: str):
    """Guards the premise: the gate is only meaningful if the claim is minted."""
    from src.core.security import decode_token

    payload = decode_token(app_token, expected_type="access")
    assert payload is not None
    assert payload["actor_type"] == ACTOR_TYPE


class TestHttpAuthPath:
    @pytest.mark.asyncio
    async def test_bearer_app_token_is_rejected(self, mock_request, mock_db, app_token):
        user = await get_current_user_optional(mock_request, credentials(app_token), mock_db)
        assert user is None

    @pytest.mark.asyncio
    async def test_cookie_app_token_is_rejected(self, mock_request, mock_db, app_token):
        mock_request.cookies = {"access_token": app_token}
        user = await get_current_user_optional(mock_request, None, mock_db)
        assert user is None

    @pytest.mark.asyncio
    async def test_required_dependency_raises_401(self, mock_request, mock_db, app_token):
        with pytest.raises(HTTPException) as exc:
            await get_current_user(mock_request, credentials(app_token), mock_db)
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_equivalent_user_token_still_authenticates(
        self, mock_request, mock_db, user_token, session
    ):
        user = await get_current_user(mock_request, credentials(user_token), mock_db)
        assert user is not None
        assert user.user_id == session.user_id
        assert user.organization_id == session.organization_id


class TestWebSocketAuthPath:
    @pytest.mark.asyncio
    async def test_cookie_app_token_is_rejected(self, app_token):
        assert await get_current_user_ws(ws(cookies={"access_token": app_token})) is None

    @pytest.mark.asyncio
    async def test_header_app_token_is_rejected(self, app_token):
        websocket = ws(headers={"authorization": f"Bearer {app_token}"})
        assert await get_current_user_ws(websocket) is None

    @pytest.mark.asyncio
    async def test_equivalent_user_token_still_authenticates(self, user_token, session):
        user = await get_current_user_ws(ws(cookies={"access_token": user_token}))
        assert user is not None
        assert user.user_id == session.user_id


class TestMcpAuthPath:
    """MCP decodes the same JWTs and builds its own claims from the payload."""

    @pytest.fixture
    def provider(self) -> BifrostAuthProvider:
        provider = BifrostAuthProvider(base_url="https://test.example.com")
        provider._check_mcp_access = AsyncMock(return_value=True)
        provider._get_user_roles = AsyncMock(return_value=[])
        return provider

    @pytest.mark.asyncio
    async def test_app_token_is_rejected(self, provider, app_token):
        assert await provider.verify_token(app_token) is None

    @pytest.mark.asyncio
    async def test_equivalent_user_token_is_accepted(self, provider, user_token, session):
        access = await provider.verify_token(user_token)
        assert access is not None
        assert access.claims["user_id"] == str(session.user_id)
