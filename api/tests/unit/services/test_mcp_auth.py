"""
Unit tests for MCP Authentication.

Tests the BifrostAuthProvider class which implements OAuth 2.1 for MCP:
- OAuth discovery metadata endpoints
- Authorization code flow with PKCE
- Token verification for MCP requests

Uses mocked dependencies for fast, isolated testing.
"""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.security import HTTPAuthorizationCredentials

from src.core.security import create_access_token
from src.services.mcp_server.auth import (
    BifrostAuthProvider,
    create_bifrost_auth_provider,
    _mcp_auth_code_key,
    _mcp_client_key,
    _mcp_state_key,
)


# ==================== Fixtures ====================


@pytest.fixture
def auth_provider() -> BifrostAuthProvider:
    """Create a BifrostAuthProvider with test base URL."""
    return BifrostAuthProvider(base_url="https://test.example.com")


@pytest.fixture
def admin_token_payload() -> dict:
    """Create a payload for a platform admin user."""
    return {
        "sub": str(uuid4()),
        "email": "admin@platform.local",
        "name": "Platform Admin",
        "is_superuser": True,
        "org_id": str(uuid4()),
    }


@pytest.fixture
def regular_user_payload() -> dict:
    """Create a payload for a regular org user."""
    return {
        "sub": str(uuid4()),
        "email": "user@org.local",
        "name": "Regular User",
        "is_superuser": False,
        "org_id": str(uuid4()),
    }


@pytest.fixture
def admin_access_token(admin_token_payload) -> str:
    """Create a valid access token for a platform admin."""
    return create_access_token(admin_token_payload)


@pytest.fixture
def regular_user_access_token(regular_user_payload) -> str:
    """Create a valid access token for a regular user."""
    return create_access_token(regular_user_payload)


@pytest.fixture
def expired_token(admin_token_payload) -> str:
    """Create an expired access token."""
    return create_access_token(
        admin_token_payload,
        expires_delta=timedelta(seconds=-1),  # Already expired
    )


# ==================== BifrostAuthProvider Tests ====================


class TestBifrostAuthProviderInit:
    """Tests for BifrostAuthProvider initialization."""

    def test_uses_provided_base_url(self):
        """Should use the provided base URL."""
        provider = BifrostAuthProvider(base_url="https://custom.example.com")
        assert provider.base_url == "https://custom.example.com"
        assert provider.issuer == "https://custom.example.com"

    def test_strips_trailing_slash(self):
        """Should strip trailing slash from base URL."""
        provider = BifrostAuthProvider(base_url="https://example.com/")
        assert provider.base_url == "https://example.com"

    @patch("src.config.get_settings")
    def test_falls_back_to_settings(self, mock_get_settings):
        """Should fall back to settings.mcp_base_url if no base_url provided."""
        mock_settings = MagicMock()
        mock_settings.public_url = "https://settings.example.com"
        mock_get_settings.return_value = mock_settings

        provider = BifrostAuthProvider()
        assert provider.base_url == "https://settings.example.com"


class TestGetRoutes:
    """Tests for get_routes() method."""

    def test_returns_oauth_routes(self, auth_provider):
        """Should return all required OAuth routes."""
        routes = auth_provider.get_routes()

        # Get route paths
        paths = [route.path for route in routes]

        # Check all required OAuth endpoints
        assert "/.well-known/oauth-authorization-server" in paths
        assert "/.well-known/oauth-protected-resource" in paths
        assert "/authorize" in paths
        assert "/token" in paths
        assert "/register" in paths
        assert "/mcp/callback" in paths


class TestAuthorizationServerMetadata:
    """Tests for OAuth authorization server metadata endpoint."""

    @pytest.mark.asyncio
    async def test_returns_correct_metadata(self, auth_provider):
        """Should return RFC 8414 compliant metadata."""
        mock_request = MagicMock()

        response = await auth_provider._authorization_server_metadata(mock_request)
        data = response.body.decode()

        import json
        metadata = json.loads(data)

        assert metadata["issuer"] == "https://test.example.com"
        assert metadata["authorization_endpoint"] == "https://test.example.com/authorize"
        assert metadata["token_endpoint"] == "https://test.example.com/token"
        assert metadata["registration_endpoint"] == "https://test.example.com/register"
        assert "code" in metadata["response_types_supported"]
        assert "authorization_code" in metadata["grant_types_supported"]
        assert "S256" in metadata["code_challenge_methods_supported"]


class TestProtectedResourceMetadata:
    """Tests for OAuth protected resource metadata endpoint."""

    @pytest.mark.asyncio
    async def test_returns_correct_metadata(self, auth_provider):
        """Should return RFC 9728 compliant metadata."""
        mock_request = MagicMock()

        response = await auth_provider._protected_resource_metadata(mock_request)
        data = response.body.decode()

        import json
        metadata = json.loads(data)

        assert metadata["resource"] == "https://test.example.com/mcp"
        assert "https://test.example.com" in metadata["authorization_servers"]
        assert "mcp:access" in metadata["scopes_supported"]
        assert "header" in metadata["bearer_methods_supported"]


class TestMcpBoundTokens:
    """MCP OAuth tokens must not be reusable as first-party REST API tokens."""

    @pytest.mark.asyncio
    async def test_mcp_access_token_is_rejected_by_rest_auth(self):
        from src.core.auth import get_current_user_optional

        token = create_access_token({
            "sub": str(uuid4()),
            "email": "user@org.local",
            "name": "Regular User",
            "is_superuser": False,
            "org_id": str(uuid4()),
            "mcp": True,
            "scope": "mcp:access",
        })
        request = MagicMock()
        request.cookies = {}
        credentials = HTTPAuthorizationCredentials(
            scheme="Bearer",
            credentials=token,
        )

        user = await get_current_user_optional(request, credentials, MagicMock())

        assert user is None


class TestCheckMcpAccess:
    """Tests for MCP access permission checking.

    Access is gated only on the ``enabled`` master switch. Per-user/tool access
    is handled downstream by ``MCPToolAccessService`` via agent role membership,
    so a non-superuser with no matching roles will connect successfully and
    simply see an empty tool set.
    """

    @pytest.mark.asyncio
    async def test_allows_non_admin_when_enabled(self, auth_provider):
        """Non-admins are allowed through — role-scoped tools are filtered later."""
        mock_config = MagicMock()
        mock_config.enabled = True

        with patch("src.core.database.get_db_context") as mock_db, \
             patch("src.services.mcp_server.config_service.get_mcp_config_cached") as mock_get_config:
            mock_db.return_value.__aenter__ = AsyncMock()
            mock_db.return_value.__aexit__ = AsyncMock()
            mock_get_config.return_value = mock_config

            result = await auth_provider._check_mcp_access({"is_superuser": False})
            assert result is True

    @pytest.mark.asyncio
    async def test_allows_admin_when_enabled(self, auth_provider):
        """Platform admins are allowed through."""
        mock_config = MagicMock()
        mock_config.enabled = True

        with patch("src.core.database.get_db_context") as mock_db, \
             patch("src.services.mcp_server.config_service.get_mcp_config_cached") as mock_get_config:
            mock_db.return_value.__aenter__ = AsyncMock()
            mock_db.return_value.__aexit__ = AsyncMock()
            mock_get_config.return_value = mock_config

            result = await auth_provider._check_mcp_access({"is_superuser": True})
            assert result is True

    @pytest.mark.asyncio
    async def test_denies_when_disabled(self, auth_provider):
        """Should deny all access when MCP is disabled, including admins."""
        mock_config = MagicMock()
        mock_config.enabled = False

        with patch("src.core.database.get_db_context") as mock_db, \
             patch("src.services.mcp_server.config_service.get_mcp_config_cached") as mock_get_config:
            mock_db.return_value.__aenter__ = AsyncMock()
            mock_db.return_value.__aexit__ = AsyncMock()
            mock_get_config.return_value = mock_config

            result = await auth_provider._check_mcp_access({"is_superuser": True})
            assert result is False


# ==================== Redis Key Functions Tests ====================


class TestRedisKeys:
    """Tests for Redis key generation functions."""

    def test_mcp_auth_code_key(self):
        """Should generate correct auth code key."""
        key = _mcp_auth_code_key("test-code-123")
        assert key == "bifrost:mcp:auth_code:test-code-123"

    def test_mcp_client_key(self):
        """Should generate correct client key."""
        key = _mcp_client_key("client-id-456")
        assert key == "bifrost:mcp:client:client-id-456"

    def test_mcp_state_key(self):
        """Should generate correct state key."""
        key = _mcp_state_key("state-789")
        assert key == "bifrost:mcp:state:state-789"


# ==================== Factory Function Tests ====================


class TestCreateBifrostAuthProvider:
    """Tests for the create_bifrost_auth_provider factory function."""

    def test_creates_provider_with_base_url(self):
        """Should create provider with specified base URL."""
        provider = create_bifrost_auth_provider("https://factory.example.com")
        assert isinstance(provider, BifrostAuthProvider)
        assert provider.base_url == "https://factory.example.com"

    @patch("src.config.get_settings")
    def test_creates_provider_with_defaults(self, mock_get_settings):
        """Should create provider with default settings when no base_url provided."""
        mock_settings = MagicMock()
        mock_settings.public_url = "https://default.example.com"
        mock_get_settings.return_value = mock_settings

        provider = create_bifrost_auth_provider()
        assert isinstance(provider, BifrostAuthProvider)
        assert provider.base_url == "https://default.example.com"
