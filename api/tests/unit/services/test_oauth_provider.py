"""Unit tests for OAuth Provider Client service

Tests token exchange, refresh, and client credentials flows.
Mocks HTTP requests to OAuth providers.
"""

import pytest
import asyncio
import logging
from datetime import datetime, timezone
from unittest.mock import MagicMock, AsyncMock, patch

from src.services.oauth_provider import OAuthProviderClient


@pytest.fixture
def oauth_client():
    """Create OAuth provider client with test defaults"""
    return OAuthProviderClient(timeout=10, max_retries=3)


@pytest.mark.parametrize(
    ("method_name", "kwargs", "secrets", "expected"),
    [
        (
            "exchange_code_for_token",
            {
                "token_url": "https://oauth.example.com/token?tenant=secret-tenant",
                "code": "authorization-code",
                "client_id": "client-id",
                "client_secret": "super-secret-client-value",
                "redirect_uri": "https://app.example.com/callback",
            },
            ("super-secret-client-value", "secret-tenant", "client_secret"),
            "confidential client",
        ),
        (
            "refresh_access_token",
            {
                "token_url": "https://oauth.example.com/token?tenant=secret-tenant",
                "refresh_token": "refresh-token-secret",
                "client_id": "client-id",
                "client_secret": "client-secret-value",
            },
            (
                "refresh-token-secret",
                "client-secret-value",
                "secret-tenant",
                "client_secret",
            ),
            "confidential client",
        ),
    ],
)
@pytest.mark.asyncio
async def test_oauth_request_logs_do_not_include_secret_material(
    oauth_client, caplog, method_name, kwargs, secrets, expected
):
    """OAuth request logs should describe flow shape without logging secrets."""
    with (
        caplog.at_level(logging.INFO, logger="src.services.oauth_provider"),
        patch.object(
            oauth_client,
            "_make_token_request",
            new=AsyncMock(return_value=(True, {"access_token": "ok"})),
        ),
    ):
        await getattr(oauth_client, method_name)(**kwargs)

    log_text = caplog.text
    for secret in secrets:
        assert secret not in log_text
    assert expected in log_text


class TestOAuthProviderTokenExchange:
    """Test authorization code token exchange"""

    @pytest.mark.asyncio
    async def test_exchange_code_for_token_success(self, oauth_client):
        """Should successfully exchange code for access token"""
        token_url = "https://oauth.example.com/token"
        code = "authorization_code_123"
        client_id = "client-id-456"
        client_secret = "client-secret-789"
        redirect_uri = "https://app.example.com/callback"

        token_response = {
            "access_token": "access_token_123",
            "token_type": "Bearer",
            "expires_in": 3600,
            "refresh_token": "refresh_token_456"
        }

        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value=token_response)

            mock_post_context = MagicMock()
            mock_post_context.__aenter__ = AsyncMock(return_value=mock_response)
            mock_post_context.__aexit__ = AsyncMock(return_value=None)

            mock_session = MagicMock()
            mock_session.post = MagicMock(return_value=mock_post_context)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)

            mock_session_class.return_value = mock_session

            success, result = await oauth_client.exchange_code_for_token(
                token_url=token_url,
                code=code,
                client_id=client_id,
                client_secret=client_secret,
                redirect_uri=redirect_uri
            )

            assert success is True
            assert result["access_token"] == "access_token_123"
            assert result["token_type"] == "Bearer"
            assert result["refresh_token"] == "refresh_token_456"
            assert "expires_at" in result

    @pytest.mark.asyncio
    async def test_exchange_with_pkce(self, oauth_client):
        """Should support PKCE flow without client secret"""
        token_url = "https://oauth.example.com/token"
        code = "authorization_code_pkce"
        client_id = "pkce-client-id"
        redirect_uri = "https://app.example.com/callback"

        token_response = {
            "access_token": "pkce_access_token",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "openid profile email"
        }

        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value=token_response)

            mock_post_context = MagicMock()
            mock_post_context.__aenter__ = AsyncMock(return_value=mock_response)
            mock_post_context.__aexit__ = AsyncMock(return_value=None)

            mock_session = MagicMock()
            mock_session.post = MagicMock(return_value=mock_post_context)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)

            mock_session_class.return_value = mock_session

            success, result = await oauth_client.exchange_code_for_token(
                token_url=token_url,
                code=code,
                client_id=client_id,
                client_secret=None,  # PKCE - no secret
                redirect_uri=redirect_uri
            )

            assert success is True
            assert result["access_token"] == "pkce_access_token"
            # Verify client_secret was NOT included in request
            call_args = mock_session.post.call_args
            data = call_args[1].get("data")
            assert "client_secret" not in data

    @pytest.mark.asyncio
    async def test_exchange_invalid_code(self, oauth_client):
        """Should handle invalid authorization code"""
        token_url = "https://oauth.example.com/token"

        error_response = {
            "error": "invalid_grant",
            "error_description": "The authorization code is invalid or expired"
        }

        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_response = MagicMock()
            mock_response.status = 400
            mock_response.json = AsyncMock(return_value=error_response)

            mock_post_context = MagicMock()
            mock_post_context.__aenter__ = AsyncMock(return_value=mock_response)
            mock_post_context.__aexit__ = AsyncMock(return_value=None)

            mock_session = MagicMock()
            mock_session.post = MagicMock(return_value=mock_post_context)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)

            mock_session_class.return_value = mock_session

            success, result = await oauth_client.exchange_code_for_token(
                token_url=token_url,
                code="invalid_code",
                client_id="client-id",
                client_secret="secret",
                redirect_uri="https://app.example.com/callback"
            )

            assert success is False
            assert result["error"] == "invalid_grant"
            assert "invalid" in result["error_description"].lower()

    @pytest.mark.asyncio
    async def test_exchange_includes_scope_when_provided(self, oauth_client):
        """Should include scope in auth code exchange payload when provided."""
        token_url = "https://oauth.example.com/token"

        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={
                "access_token": "access_token_123",
                "token_type": "Bearer",
                "expires_in": 3600,
            })

            mock_post_context = MagicMock()
            mock_post_context.__aenter__ = AsyncMock(return_value=mock_response)
            mock_post_context.__aexit__ = AsyncMock(return_value=None)

            mock_session = MagicMock()
            mock_session.post = MagicMock(return_value=mock_post_context)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)

            mock_session_class.return_value = mock_session

            success, _result = await oauth_client.exchange_code_for_token(
                token_url=token_url,
                code="authorization_code_123",
                client_id="client-id-456",
                client_secret="client-secret-789",
                redirect_uri="https://app.example.com/callback",
                scopes="openid profile offline_access",
            )

            assert success is True
            data = mock_session.post.call_args[1]["data"]
            assert data["scope"] == "openid profile offline_access"

    @pytest.mark.asyncio
    async def test_exchange_omits_scope_when_not_provided(self, oauth_client):
        """Should omit scope in auth code exchange payload when not provided."""
        token_url = "https://oauth.example.com/token"

        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={
                "access_token": "access_token_123",
                "token_type": "Bearer",
                "expires_in": 3600,
            })

            mock_post_context = MagicMock()
            mock_post_context.__aenter__ = AsyncMock(return_value=mock_response)
            mock_post_context.__aexit__ = AsyncMock(return_value=None)

            mock_session = MagicMock()
            mock_session.post = MagicMock(return_value=mock_post_context)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)

            mock_session_class.return_value = mock_session

            success, _result = await oauth_client.exchange_code_for_token(
                token_url=token_url,
                code="authorization_code_123",
                client_id="client-id-456",
                client_secret="client-secret-789",
                redirect_uri="https://app.example.com/callback",
                scopes=None,
            )

            assert success is True
            data = mock_session.post.call_args[1]["data"]
            assert "scope" not in data


class TestOAuthProviderTokenRefresh:
    """Test access token refresh flow"""

    @pytest.mark.asyncio
    async def test_refresh_access_token_success(self, oauth_client):
        """Should successfully refresh access token"""
        token_url = "https://oauth.example.com/token"
        refresh_token = "refresh_token_123"

        token_response = {
            "access_token": "new_access_token",
            "token_type": "Bearer",
            "expires_in": 3600,
            "refresh_token": "new_refresh_token"
        }

        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value=token_response)

            mock_post_context = MagicMock()
            mock_post_context.__aenter__ = AsyncMock(return_value=mock_response)
            mock_post_context.__aexit__ = AsyncMock(return_value=None)

            mock_session = MagicMock()
            mock_session.post = MagicMock(return_value=mock_post_context)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)

            mock_session_class.return_value = mock_session

            success, result = await oauth_client.refresh_access_token(
                token_url=token_url,
                refresh_token=refresh_token,
                client_id="client-id",
                client_secret="client-secret"
            )

            assert success is True
            assert result["access_token"] == "new_access_token"
            assert result["refresh_token"] == "new_refresh_token"
            assert "expires_at" in result

    @pytest.mark.asyncio
    async def test_refresh_with_expired_token(self, oauth_client):
        """Should handle expired refresh token"""
        token_url = "https://oauth.example.com/token"

        error_response = {
            "error": "invalid_grant",
            "error_description": "The refresh token has expired"
        }

        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_response = MagicMock()
            mock_response.status = 400
            mock_response.json = AsyncMock(return_value=error_response)

            mock_post_context = MagicMock()
            mock_post_context.__aenter__ = AsyncMock(return_value=mock_response)
            mock_post_context.__aexit__ = AsyncMock(return_value=None)

            mock_session = MagicMock()
            mock_session.post = MagicMock(return_value=mock_post_context)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)

            mock_session_class.return_value = mock_session

            success, result = await oauth_client.refresh_access_token(
                token_url=token_url,
                refresh_token="expired_refresh_token",
                client_id="client-id",
                client_secret="client-secret"
            )

            assert success is False
            assert "invalid_grant" in result["error"]

    @pytest.mark.asyncio
    async def test_refresh_includes_scope_when_provided(self, oauth_client):
        """Should include scope in refresh payload when provided."""
        token_url = "https://oauth.example.com/token"

        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={
                "access_token": "new_access_token",
                "token_type": "Bearer",
                "expires_in": 3600,
            })

            mock_post_context = MagicMock()
            mock_post_context.__aenter__ = AsyncMock(return_value=mock_response)
            mock_post_context.__aexit__ = AsyncMock(return_value=None)

            mock_session = MagicMock()
            mock_session.post = MagicMock(return_value=mock_post_context)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)

            mock_session_class.return_value = mock_session

            success, _result = await oauth_client.refresh_access_token(
                token_url=token_url,
                refresh_token="refresh_token_123",
                client_id="client-id",
                client_secret="client-secret",
                scopes="openid profile offline_access",
            )

            assert success is True
            data = mock_session.post.call_args[1]["data"]
            assert data["scope"] == "openid profile offline_access"

    @pytest.mark.asyncio
    async def test_refresh_omits_scope_when_not_provided(self, oauth_client):
        """Should omit scope in refresh payload when not provided."""
        token_url = "https://oauth.example.com/token"

        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={
                "access_token": "new_access_token",
                "token_type": "Bearer",
                "expires_in": 3600,
            })

            mock_post_context = MagicMock()
            mock_post_context.__aenter__ = AsyncMock(return_value=mock_response)
            mock_post_context.__aexit__ = AsyncMock(return_value=None)

            mock_session = MagicMock()
            mock_session.post = MagicMock(return_value=mock_post_context)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)

            mock_session_class.return_value = mock_session

            success, _result = await oauth_client.refresh_access_token(
                token_url=token_url,
                refresh_token="refresh_token_123",
                client_id="client-id",
                client_secret="client-secret",
                scopes=None,
            )

            assert success is True
            data = mock_session.post.call_args[1]["data"]
            assert "scope" not in data


class TestOAuthProviderScopes:
    """Test scope validation and handling"""

    @pytest.mark.asyncio
    async def test_client_credentials_with_scopes(self, oauth_client):
        """Should request scopes with client credentials flow"""
        token_url = "https://oauth.example.com/token"

        token_response = {
            "access_token": "cc_access_token",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "api://app/read api://app/write"
        }

        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value=token_response)

            mock_post_context = MagicMock()
            mock_post_context.__aenter__ = AsyncMock(return_value=mock_response)
            mock_post_context.__aexit__ = AsyncMock(return_value=None)

            mock_session = MagicMock()
            mock_session.post = MagicMock(return_value=mock_post_context)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)

            mock_session_class.return_value = mock_session

            success, result = await oauth_client.get_client_credentials_token(
                token_url=token_url,
                client_id="client-id",
                client_secret="client-secret",
                scopes="api://app/read,api://app/write"
            )

            assert success is True
            assert "scope" in result
            # Verify scopes were normalized to space-separated
            call_args = mock_session.post.call_args
            data = call_args[1].get("data")
            assert "scope" in data
            assert "api://app/read" in data["scope"]


class TestOAuthProviderMetadata:
    """Test OAuth provider metadata and URL building"""

    def test_parse_token_response_with_expires_in(self, oauth_client):
        """Should calculate expires_at from expires_in"""
        response_data = {
            "access_token": "token_123",
            "token_type": "Bearer",
            "expires_in": 3600,
            "refresh_token": "refresh_123"
        }

        before_parse = datetime.now(timezone.utc)
        result = oauth_client._parse_token_response(response_data)
        datetime.now(timezone.utc)

        assert result["access_token"] == "token_123"
        assert result["token_type"] == "Bearer"
        assert result["refresh_token"] == "refresh_123"
        assert "expires_at" in result
        # Verify expiry is approximately 1 hour from now
        expiry_in_minutes = (result["expires_at"] - before_parse).total_seconds() / 60
        assert 59 <= expiry_in_minutes <= 61

    def test_parse_token_response_without_expires_in(self, oauth_client):
        """Should default to 1 hour if expires_in missing"""
        response_data = {
            "access_token": "token_456",
            "token_type": "Bearer",
            "refresh_token": "refresh_456"
            # No expires_in
        }

        before_parse = datetime.now(timezone.utc)
        result = oauth_client._parse_token_response(response_data)
        datetime.now(timezone.utc)

        assert "expires_at" in result
        # Should default to 1 hour
        expiry_in_minutes = (result["expires_at"] - before_parse).total_seconds() / 60
        assert 59 <= expiry_in_minutes <= 61

    def test_parse_token_response_no_refresh_token(self, oauth_client):
        """Should handle missing refresh token"""
        response_data = {
            "access_token": "token_789",
            "token_type": "Bearer",
            "expires_in": 3600
            # No refresh_token
        }

        result = oauth_client._parse_token_response(response_data)

        assert result["access_token"] == "token_789"
        assert result["refresh_token"] is None


class TestOAuthProviderRetry:
    """Test retry logic and error handling"""

    @pytest.mark.asyncio
    async def test_retry_on_server_error(self, oauth_client):
        """Should retry on 5xx errors"""
        token_url = "https://oauth.example.com/token"

        with patch('aiohttp.ClientSession') as mock_session_class:
            # First attempt: 503 error
            mock_response_error = MagicMock()
            mock_response_error.status = 503
            mock_response_error.json = AsyncMock(return_value={"error": "service_unavailable"})

            # Second attempt: success
            mock_response_success = MagicMock()
            mock_response_success.status = 200
            mock_response_success.json = AsyncMock(return_value={
                "access_token": "token_after_retry",
                "token_type": "Bearer",
                "expires_in": 3600
            })

            mock_post_error_context = MagicMock()
            mock_post_error_context.__aenter__ = AsyncMock(return_value=mock_response_error)
            mock_post_error_context.__aexit__ = AsyncMock(return_value=None)

            mock_post_success_context = MagicMock()
            mock_post_success_context.__aenter__ = AsyncMock(return_value=mock_response_success)
            mock_post_success_context.__aexit__ = AsyncMock(return_value=None)

            mock_session = MagicMock()
            mock_session.post = MagicMock(side_effect=[mock_post_error_context, mock_post_success_context])
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)

            mock_session_class.return_value = mock_session

            with patch('asyncio.sleep', new_callable=AsyncMock):  # Speed up test
                success, result = await oauth_client.refresh_access_token(
                    token_url=token_url,
                    refresh_token="refresh_token",
                    client_id="client-id",
                    client_secret="client-secret"
                )

            assert success is True
            assert result["access_token"] == "token_after_retry"

    @pytest.mark.asyncio
    async def test_no_retry_on_client_error(self, oauth_client):
        """Should NOT retry on 4xx errors"""
        token_url = "https://oauth.example.com/token"

        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_response = MagicMock()
            mock_response.status = 401
            mock_response.json = AsyncMock(return_value={
                "error": "unauthorized",
                "error_description": "Invalid credentials"
            })

            mock_post_context = MagicMock()
            mock_post_context.__aenter__ = AsyncMock(return_value=mock_response)
            mock_post_context.__aexit__ = AsyncMock(return_value=None)

            mock_session = MagicMock()
            mock_session.post = MagicMock(return_value=mock_post_context)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)

            mock_session_class.return_value = mock_session

            success, result = await oauth_client.refresh_access_token(
                token_url=token_url,
                refresh_token="refresh_token",
                client_id="client-id",
                client_secret="client-secret"
            )

            assert success is False
            # Should only have tried once
            assert mock_session.post.call_count == 1

    @pytest.mark.asyncio
    async def test_request_timeout_handling(self, oauth_client):
        """Should handle request timeouts with retries"""
        token_url = "https://oauth.example.com/token"

        with patch('aiohttp.ClientSession') as mock_session_class:
            # Create a context manager that always times out
            class TimeoutContext:
                async def __aenter__(self):
                    raise asyncio.TimeoutError("Request timed out")
                async def __aexit__(self, *args):
                    pass

            mock_session = MagicMock()
            mock_session.post = MagicMock(return_value=TimeoutContext())
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)

            mock_session_class.return_value = mock_session

            with patch('asyncio.sleep', new_callable=AsyncMock):  # Speed up test
                success, result = await oauth_client.refresh_access_token(
                    token_url=token_url,
                    refresh_token="refresh_token",
                    client_id="client-id",
                    client_secret="client-secret"
                )

            # After max_retries, should fail
            assert success is False
            assert "max_retries_exceeded" in result["error"]
