"""
Unit tests for user authentication.

Tests that superusers (system accounts) can authenticate without org_id,
while regular users still require org_id in their tokens.

Auth model:
- is_superuser=true, org_id=UUID: Platform admin in an org
- is_superuser=false, org_id=UUID: Regular org user
- is_superuser=true, org_id=None: System account (global scope)
- is_superuser=false, org_id=None: INVALID (rejected at token parsing)
"""

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import Request
from fastapi.security import HTTPAuthorizationCredentials

from src.core.auth import get_current_user_optional, get_current_user_ws
from src.core.security import create_access_token


@pytest.fixture
def mock_request():
    """Create a mock FastAPI request."""
    request = MagicMock(spec=Request)
    request.cookies = {}
    return request


@pytest.fixture
def mock_db():
    """Create a mock database session."""
    return MagicMock()


def create_credentials(token: str) -> HTTPAuthorizationCredentials:
    """Create HTTP credentials from a token."""
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


class TestSystemAccountTokenValidation:
    """Tests for system account (superuser without org_id) token handling."""

    @pytest.mark.asyncio
    async def test_superuser_without_org_id_is_valid(
        self, mock_request, mock_db
    ):
        """System account token (superuser, no org_id) should authenticate successfully."""
        user_id = str(uuid4())

        # Create token with is_superuser=True but no org_id (system account)
        token = create_access_token({
            "sub": user_id,
            "email": "engine@bifrost.internal",
            "name": "Bifrost Engine",
            "is_superuser": True,
            # NO org_id claim - valid for system accounts
        })

        credentials = create_credentials(token)

        user = await get_current_user_optional(mock_request, credentials, mock_db)

        assert user is not None
        assert str(user.user_id) == user_id
        assert user.organization_id is None  # System account has no org
        assert user.is_superuser is True
        assert user.email == "engine@bifrost.internal"

    @pytest.mark.asyncio
    async def test_superuser_with_org_id_is_platform_admin(
        self, mock_request, mock_db
    ):
        """Superuser with org_id should be a platform admin in that org."""
        user_id = str(uuid4())
        org_id = str(uuid4())

        # Platform admin: superuser with org_id
        token = create_access_token({
            "sub": user_id,
            "email": "admin@example.com",
            "name": "Platform Admin",
            "is_superuser": True,
            "org_id": org_id,
        })

        credentials = create_credentials(token)

        user = await get_current_user_optional(mock_request, credentials, mock_db)

        assert user is not None
        assert str(user.organization_id) == org_id
        assert user.is_superuser is True


class TestOrgUserTokenValidation:
    """Tests for regular org user token handling."""

    @pytest.mark.asyncio
    async def test_regular_user_without_org_id_is_rejected(
        self, mock_request, mock_db
    ):
        """Non-superuser token without org_id should be rejected."""
        user_id = str(uuid4())

        # Regular user without org_id - invalid
        token = create_access_token({
            "sub": user_id,
            "email": "user@example.com",
            "name": "Regular User",
            "is_superuser": False,
            # NO org_id claim - invalid for non-superusers
        })

        credentials = create_credentials(token)

        user = await get_current_user_optional(mock_request, credentials, mock_db)

        # Should be rejected (returns None)
        assert user is None

    @pytest.mark.asyncio
    async def test_regular_user_with_org_id_is_valid(
        self, mock_request, mock_db
    ):
        """Regular user token with org_id should authenticate."""
        user_id = str(uuid4())
        org_id = str(uuid4())

        token = create_access_token({
            "sub": user_id,
            "email": "user@example.com",
            "name": "Regular User",
            "is_superuser": False,
            "org_id": org_id,
        })

        credentials = create_credentials(token)

        user = await get_current_user_optional(mock_request, credentials, mock_db)

        assert user is not None
        assert str(user.user_id) == user_id
        assert str(user.organization_id) == org_id
        assert user.is_superuser is False
        assert user.email == "user@example.com"

    @pytest.mark.asyncio
    async def test_user_with_invalid_org_id_is_rejected(
        self, mock_request, mock_db
    ):
        """Token with invalid org_id format should be rejected."""
        user_id = str(uuid4())

        token = create_access_token({
            "sub": user_id,
            "email": "user@example.com",
            "name": "Regular User",
            "is_superuser": False,
            "org_id": "not-a-uuid",
        })

        credentials = create_credentials(token)

        user = await get_current_user_optional(mock_request, credentials, mock_db)

        assert user is None


class TestMissingClaimsHandling:
    """Tests for tokens with missing claims."""

    @pytest.mark.asyncio
    async def test_token_without_is_superuser_defaults_to_false(
        self, mock_request, mock_db
    ):
        """Token without is_superuser should default to False (and require org_id)."""
        user_id = str(uuid4())
        org_id = str(uuid4())

        # Token missing is_superuser but has org_id
        token = create_access_token({
            "sub": user_id,
            "email": "user@example.com",
            "name": "User",
            # NO is_superuser - defaults to False
            "org_id": org_id,
        })

        credentials = create_credentials(token)

        user = await get_current_user_optional(mock_request, credentials, mock_db)

        # Should work because org_id is provided
        assert user is not None
        assert str(user.organization_id) == org_id
        assert user.is_superuser is False


class TestWebSocketCredentialPrecedence:
    @pytest.mark.asyncio
    async def test_explicit_query_token_wins_over_operator_cookie(self) -> None:
        operator_id = uuid4()
        target_id = uuid4()
        org_id = str(uuid4())
        websocket = SimpleNamespace(
            headers={},
            query_params={
                "token": create_access_token(
                    {
                        "sub": str(target_id),
                        "email": "target@example.com",
                        "org_id": org_id,
                    }
                )
            },
            cookies={
                "access_token": create_access_token(
                    {
                        "sub": str(operator_id),
                        "email": "operator@example.com",
                        "org_id": org_id,
                    }
                )
            },
        )

        user = await get_current_user_ws(websocket)

        assert user is not None
        assert user.user_id == target_id
