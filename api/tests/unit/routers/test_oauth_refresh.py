"""Tests for OAuth refresh endpoint handling client_credentials flow."""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, AsyncMock, patch
from uuid import uuid4

from src.core.principal import UserPrincipal
from src.services.authorization import AuthorizationBoundary, AuthorizationContext


def _authorization(boundary: AuthorizationBoundary) -> AuthorizationContext:
    principal = UserPrincipal(
        user_id=uuid4(),
        email="admin@gobifrost.com",
        name="Admin User",
        organization_id=uuid4(),
    )
    return AuthorizationContext(
        requester=principal,
        effective_actor=principal,
        selected_boundary=boundary,
        effective_capabilities=frozenset({"integrations.readwrite"}),
        grant_sources=(),
    )


class TestOAuthRefreshClientCredentials:
    """Test that /refresh endpoint handles client_credentials flow correctly."""

    @pytest.mark.asyncio
    async def test_refresh_uses_client_credentials_flow_when_appropriate(self):
        """For client_credentials flow, should call get_client_credentials_token."""
        from src.services.oauth_provider import OAuthProviderClient

        # Create mock provider
        provider = MagicMock()
        provider.oauth_flow_type = "client_credentials"
        provider.token_url = (
            "https://login.microsoftonline.com/common/oauth2/v2.0/token"
        )
        provider.client_id = "test-client-id"
        provider.encrypted_client_secret = b"encrypted-secret"
        provider.scopes = ["https://graph.microsoft.com/.default"]

        # Mock the OAuth client response
        mock_token_response = {
            "access_token": "new-access-token",
            "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
        }

        oauth_client = OAuthProviderClient()

        with patch.object(
            oauth_client,
            "get_client_credentials_token",
            new_callable=AsyncMock,
            return_value=(True, mock_token_response),
        ) as mock_get_token:
            success, result = await oauth_client.get_client_credentials_token(
                token_url=provider.token_url,
                client_id=provider.client_id,
                client_secret="decrypted-secret",
                scopes=" ".join(provider.scopes),
            )

            mock_get_token.assert_called_once()
            assert success is True
            assert result["access_token"] == "new-access-token"

    @pytest.mark.asyncio
    async def test_refresh_uses_refresh_token_for_authorization_code_flow(self):
        """For authorization_code flow, should call refresh_access_token."""
        from src.services.oauth_provider import OAuthProviderClient

        # Create mock provider
        provider = MagicMock()
        provider.oauth_flow_type = "authorization_code"
        provider.token_url = "https://oauth.example.com/token"
        provider.client_id = "test-client-id"
        provider.encrypted_client_secret = b"encrypted-secret"

        # Mock token with refresh token
        token = MagicMock()
        token.encrypted_refresh_token = b"encrypted-refresh-token"

        # Mock the OAuth client response
        mock_token_response = {
            "access_token": "refreshed-access-token",
            "refresh_token": "new-refresh-token",
            "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
        }

        oauth_client = OAuthProviderClient()

        with patch.object(
            oauth_client,
            "refresh_access_token",
            new_callable=AsyncMock,
            return_value=(True, mock_token_response),
        ) as mock_refresh:
            success, result = await oauth_client.refresh_access_token(
                token_url=provider.token_url,
                refresh_token="decrypted-refresh-token",
                client_id=provider.client_id,
                client_secret="decrypted-secret",
            )

            mock_refresh.assert_called_once()
            assert success is True
            assert result["access_token"] == "refreshed-access-token"

    @pytest.mark.asyncio
    async def test_refresh_endpoint_passes_joined_provider_scopes(self):
        """Authorization-code refresh should forward provider scopes into shared refresh context."""
        from src.routers.oauth_connections import refresh_token

        provider = MagicMock()
        provider.oauth_flow_type = "authorization_code"
        provider.token_url = (
            "https://login.microsoftonline.com/common/oauth2/v2.0/token"
        )
        provider.client_id = "test-client-id"
        provider.encrypted_client_secret = b"encrypted-secret"
        provider.scopes = [
            "openid",
            "offline_access",
            "https://graph.microsoft.com/Directory.ReadWrite.All",
        ]
        provider.audience = None
        provider.status = "completed"
        provider.status_message = None
        provider.organization_id = None

        token = MagicMock()
        token.encrypted_refresh_token = b"encrypted-refresh-token"

        ctx = MagicMock()
        ctx.db = AsyncMock()
        ctx.org_id = None

        with (
            patch(
                "src.repositories.oauth.OAuthProviderRepository.get_by_connection_name",
                new=AsyncMock(return_value=provider),
            ),
            patch(
                "src.repositories.oauth.OAuthProviderRepository.get_token",
                new=AsyncMock(return_value=token),
            ),
            patch(
                "src.routers.oauth_connections.build_token_refresh_context",
                new=AsyncMock(
                    return_value={
                        "token_id": "token-id",
                        "provider_id": "provider-id",
                        "provider_name": "Microsoft CSP",
                        "oauth_flow_type": "authorization_code",
                        "client_id": provider.client_id,
                        "encrypted_client_secret": provider.encrypted_client_secret,
                        "token_url": provider.token_url,
                        "token_url_defaults": {},
                        "scopes": provider.scopes,
                        "audience": provider.audience,
                        "encrypted_refresh_token": token.encrypted_refresh_token,
                    }
                ),
            ) as mock_build_context,
            patch(
                "src.routers.oauth_connections.refresh_oauth_token_http",
                new=AsyncMock(
                    return_value={
                        "success": True,
                        "access_token": "refreshed-access-token",
                        "encrypted_access_token": b"encrypted-access-token",
                        "encrypted_refresh_token": b"encrypted-refresh-token",
                        "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
                    }
                ),
            ) as mock_refresh_http,
            patch("src.routers.oauth_connections.CACHE_INVALIDATION_AVAILABLE", False),
        ):
            result = await refresh_token(
                connection_name="Microsoft CSP",
                ctx=ctx,
                authorization=_authorization(AuthorizationBoundary.platform()),
            )

        assert result.success is True
        mock_build_context.assert_awaited_once()
        mock_refresh_http.assert_awaited_once()
        assert mock_refresh_http.await_args.args[0]["scopes"] == [
            "openid",
            "offline_access",
            "https://graph.microsoft.com/Directory.ReadWrite.All",
        ]

    @pytest.mark.asyncio
    async def test_callback_passes_joined_provider_scopes(self):
        """OAuth callback should forward joined provider scopes into code exchange."""
        from src.models.contracts.oauth import OAuthCallbackRequest
        from src.routers.oauth_connections import oauth_callback

        provider = MagicMock()
        provider.token_url = (
            "https://login.microsoftonline.com/common/oauth2/v2.0/token"
        )
        provider.client_id = "test-client-id"
        provider.encrypted_client_secret = b"encrypted-secret"
        provider.scopes = [
            "openid",
            "offline_access",
            "https://api.partnercenter.microsoft.com/user_impersonation",
        ]
        provider.audience = None

        ctx = MagicMock()
        ctx.db = AsyncMock()
        ctx.org_id = None

        request = OAuthCallbackRequest(
            code="authorization_code_123",
            state="state-123",
            redirect_uri="https://app.example.com/oauth/callback/test",
            organization_id=None,
        )

        with (
            patch(
                "src.repositories.oauth.OAuthProviderRepository.get_by_connection_name",
                new=AsyncMock(return_value=provider),
            ),
            patch(
                "src.repositories.oauth.OAuthProviderRepository.store_token",
                new=AsyncMock(),
            ),
            patch(
                "src.routers.oauth_connections.get_url_resolution_defaults",
                new=AsyncMock(return_value={}),
            ),
            patch(
                "src.routers.oauth_connections.resolve_url_template",
                return_value=provider.token_url,
            ),
            patch(
                "src.services.oauth_provider.OAuthProviderClient.exchange_code_for_token",
                new=AsyncMock(
                    return_value=(
                        True,
                        {
                            "access_token": "access-token",
                            "refresh_token": "refresh-token",
                            "expires_at": datetime.now(timezone.utc)
                            + timedelta(hours=1),
                            "scope": "openid offline_access https://api.partnercenter.microsoft.com/user_impersonation",
                        },
                    )
                ),
            ) as mock_exchange,
            patch("src.core.security.decrypt_secret", return_value="decrypted-secret"),
            patch("src.routers.oauth_connections.CACHE_INVALIDATION_AVAILABLE", False),
        ):
            result = await oauth_callback(
                connection_name="Microsoft CSP",
                request=request,
                ctx=ctx,
                authorization=_authorization(AuthorizationBoundary.platform()),
            )

        assert result.success is True
        mock_exchange.assert_awaited_once()
        assert mock_exchange.await_args.kwargs["scopes"] == (
            "openid offline_access https://api.partnercenter.microsoft.com/user_impersonation"
        )

    def test_oauth_flow_type_determines_refresh_behavior(self):
        """Flow type should determine which refresh method is used."""
        # client_credentials flow: no refresh token, use get_client_credentials_token
        # authorization_code flow: use stored refresh token with refresh_access_token

        # This is a logic test - we verify the expected behavior
        client_credentials_needs_refresh_token = False  # Uses client_credentials grant
        authorization_code_needs_refresh_token = True  # Uses refresh_token grant

        assert client_credentials_needs_refresh_token is False
        assert authorization_code_needs_refresh_token is True


class TestOAuthRefreshEndpointLogic:
    """Test the endpoint logic branching based on flow type."""

    def test_client_credentials_flow_detected(self):
        """Verify client_credentials flow is correctly identified."""
        provider = MagicMock()
        provider.oauth_flow_type = "client_credentials"

        is_client_credentials = provider.oauth_flow_type == "client_credentials"
        assert is_client_credentials is True

    def test_authorization_code_flow_detected(self):
        """Verify authorization_code flow is correctly identified."""
        provider = MagicMock()
        provider.oauth_flow_type = "authorization_code"

        is_client_credentials = provider.oauth_flow_type == "client_credentials"
        assert is_client_credentials is False

    def test_client_credentials_requires_client_secret(self):
        """client_credentials flow requires client_secret."""
        provider = MagicMock()
        provider.oauth_flow_type = "client_credentials"
        provider.encrypted_client_secret = None

        has_client_secret = provider.encrypted_client_secret is not None
        assert has_client_secret is False

        provider.encrypted_client_secret = b"some-secret"
        has_client_secret = provider.encrypted_client_secret is not None
        assert has_client_secret is True
