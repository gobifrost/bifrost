"""Tests for auto-refresh token behavior when token_url contains {entity_id}."""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, AsyncMock, patch

# Note: should_auto_refresh_token is imported inside each test to ensure
# proper import from the cli module after all fixtures are loaded


class TestAutoRefreshTokenForTemplatedUrl:
    """Test that integrations.get() auto-fetches token when URL has {entity_id}."""

    @pytest.mark.asyncio
    async def test_fetches_fresh_token_when_url_has_entity_id_placeholder(self):
        """When token_url contains {entity_id}, should fetch fresh client_credentials token."""
        from src.routers.cli import should_auto_refresh_token

        # Mock provider with templated URL
        provider = MagicMock()
        provider.token_url = "https://login.microsoftonline.com/{entity_id}/oauth2/v2.0/token"
        provider.oauth_flow_type = "client_credentials"

        entity_id = "customer-tenant-123"

        result = should_auto_refresh_token(provider, entity_id)

        assert result is True

    @pytest.mark.asyncio
    async def test_no_auto_refresh_when_url_has_no_placeholder(self):
        """When token_url has no {entity_id}, should use stored token."""
        from src.routers.cli import should_auto_refresh_token

        provider = MagicMock()
        provider.token_url = "https://oauth.example.com/token"
        provider.oauth_flow_type = "client_credentials"

        result = should_auto_refresh_token(provider, "some-entity")

        assert result is False

    @pytest.mark.asyncio
    async def test_no_auto_refresh_for_authorization_code_flow(self):
        """Authorization code flow should never auto-refresh (uses stored refresh token)."""
        from src.routers.cli import should_auto_refresh_token

        provider = MagicMock()
        provider.token_url = "https://login.microsoftonline.com/{entity_id}/oauth2/v2.0/token"
        provider.oauth_flow_type = "authorization_code"

        result = should_auto_refresh_token(provider, "some-entity")

        assert result is False

    @pytest.mark.asyncio
    async def test_no_auto_refresh_when_no_entity_id_and_no_oauth_scope(self):
        """Should not auto-refresh when neither entity_id nor oauth_scope is provided."""
        from src.routers.cli import should_auto_refresh_token

        provider = MagicMock()
        provider.token_url = "https://login.microsoftonline.com/{entity_id}/oauth2/v2.0/token"
        provider.oauth_flow_type = "client_credentials"

        result = should_auto_refresh_token(provider, None)

        assert result is False

    @pytest.mark.asyncio
    async def test_auto_refresh_when_oauth_scope_provided(self):
        """Should auto-refresh when oauth_scope is provided, even without entity_id."""
        from src.routers.cli import should_auto_refresh_token

        provider = MagicMock()
        provider.token_url = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
        provider.oauth_flow_type = "client_credentials"

        # No entity_id but oauth_scope is provided
        result = should_auto_refresh_token(provider, None, oauth_scope="https://outlook.office365.com/.default")

        assert result is True

    @pytest.mark.asyncio
    async def test_auto_refresh_when_oauth_scope_and_entity_id_provided(self):
        """Should auto-refresh when both oauth_scope and entity_id are provided."""
        from src.routers.cli import should_auto_refresh_token

        provider = MagicMock()
        provider.token_url = "https://login.microsoftonline.com/{entity_id}/oauth2/v2.0/token"
        provider.oauth_flow_type = "client_credentials"

        result = should_auto_refresh_token(provider, "customer-tenant-123", oauth_scope="https://outlook.office365.com/.default")

        assert result is True

    @pytest.mark.asyncio
    async def test_no_auto_refresh_with_oauth_scope_for_authorization_code_flow(self):
        """oauth_scope should not trigger auto-refresh for authorization_code flow."""
        from src.routers.cli import should_auto_refresh_token

        provider = MagicMock()
        provider.token_url = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
        provider.oauth_flow_type = "authorization_code"

        result = should_auto_refresh_token(provider, None, oauth_scope="https://outlook.office365.com/.default")

        assert result is False

    @pytest.mark.asyncio
    async def test_no_auto_refresh_when_no_provider(self):
        """Should not auto-refresh when provider is None."""
        from src.routers.cli import should_auto_refresh_token

        result = should_auto_refresh_token(None, "some-entity")

        assert result is False

    @pytest.mark.asyncio
    async def test_no_auto_refresh_when_no_token_url(self):
        """Should not auto-refresh when token_url is None."""
        from src.routers.cli import should_auto_refresh_token

        provider = MagicMock()
        provider.token_url = None
        provider.oauth_flow_type = "client_credentials"

        result = should_auto_refresh_token(provider, "some-entity")

        assert result is False


class TestBuildOAuthDataAutoRefresh:
    """Test _build_oauth_data auto-refresh integration."""

    @pytest.mark.asyncio
    async def test_build_oauth_data_calls_get_client_credentials_when_templated(self):
        """_build_oauth_data should call OAuthProviderClient when token URL is templated."""
        from src.routers.cli import _build_oauth_data

        # Mock provider with templated URL
        provider = MagicMock()
        provider.provider_name = "Microsoft CSP"
        provider.client_id = "test-client-id"
        provider.token_url = "https://login.microsoftonline.com/{entity_id}/oauth2/v2.0/token"
        provider.token_url_defaults = {}
        provider.oauth_flow_type = "client_credentials"
        provider.authorization_url = None
        provider.scopes = ["https://graph.microsoft.com/.default"]
        provider.encrypted_client_secret = "encrypted-secret"
        provider.audience = None

        token = None  # No stored token
        entity_id = "customer-tenant-123"

        # Mock helper functions
        def resolve_url_template(url, entity_id, defaults):
            return url.replace("{entity_id}", entity_id)

        def decrypt_secret(value):
            return "decrypted-client-secret"

        # Mock the OAuthProviderClient
        mock_token_response = {
            "access_token": "fresh-access-token",
            "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
        }

        with patch("src.services.oauth_provider.OAuthProviderClient") as mock_client_class:
            mock_instance = MagicMock()
            mock_instance.get_client_credentials_token = AsyncMock(
                return_value=(True, mock_token_response)
            )
            mock_client_class.return_value = mock_instance

            result = await _build_oauth_data(
                provider, token, entity_id, resolve_url_template, decrypt_secret
            )

            # Verify OAuthProviderClient was called
            mock_instance.get_client_credentials_token.assert_called_once_with(
                token_url="https://login.microsoftonline.com/customer-tenant-123/oauth2/v2.0/token",
                client_id="test-client-id",
                client_secret="decrypted-client-secret",
                scopes="https://graph.microsoft.com/.default",
                audience=None,
            )

            # Verify the result contains the fresh token
            assert result.access_token == "fresh-access-token"
            assert result.expires_at is not None

    @pytest.mark.asyncio
    async def test_build_oauth_data_uses_stored_token_when_not_templated(self):
        """_build_oauth_data should use stored token when URL is not templated."""
        from src.routers.cli import _build_oauth_data

        # Mock provider with NON-templated URL
        provider = MagicMock()
        provider.provider_name = "Generic OAuth"
        provider.client_id = "test-client-id"
        provider.token_url = "https://oauth.example.com/token"  # No {entity_id}
        provider.token_url_defaults = {}
        provider.oauth_flow_type = "client_credentials"
        provider.authorization_url = None
        provider.scopes = ["read", "write"]
        provider.encrypted_client_secret = "encrypted-secret"

        # Mock stored token
        token = MagicMock()
        token.encrypted_access_token = "encrypted-stored-token"
        token.encrypted_refresh_token = None
        token.expires_at = datetime.now(timezone.utc) + timedelta(hours=1)

        entity_id = "some-entity"

        def resolve_url_template(url, entity_id, defaults):
            return url.replace("{entity_id}", entity_id)

        def decrypt_secret(value):
            if value == "encrypted-stored-token":
                return "stored-access-token"
            return "decrypted-client-secret"

        with patch("src.services.oauth_provider.OAuthProviderClient") as mock_client_class:
            result = await _build_oauth_data(
                provider, token, entity_id, resolve_url_template, decrypt_secret
            )

            # Verify OAuthProviderClient was NOT called
            mock_client_class.assert_not_called()

            # Verify the result contains the stored token
            assert result.access_token == "stored-access-token"

    @pytest.mark.asyncio
    async def test_build_oauth_data_handles_auto_refresh_failure(self):
        """_build_oauth_data should handle OAuth failure gracefully."""
        from src.routers.cli import _build_oauth_data

        # Mock provider with templated URL
        provider = MagicMock()
        provider.provider_name = "Microsoft CSP"
        provider.client_id = "test-client-id"
        provider.token_url = "https://login.microsoftonline.com/{entity_id}/oauth2/v2.0/token"
        provider.token_url_defaults = {}
        provider.oauth_flow_type = "client_credentials"
        provider.authorization_url = None
        provider.scopes = ["https://graph.microsoft.com/.default"]
        provider.encrypted_client_secret = "encrypted-secret"

        token = None
        entity_id = "customer-tenant-123"

        def resolve_url_template(url, entity_id, defaults):
            return url.replace("{entity_id}", entity_id)

        def decrypt_secret(value):
            return "decrypted-client-secret"

        # Mock OAuth failure
        mock_error_response = {
            "error": "invalid_client",
            "error_description": "Invalid client credentials",
        }

        with patch("src.services.oauth_provider.OAuthProviderClient") as mock_client_class:
            mock_instance = MagicMock()
            mock_instance.get_client_credentials_token = AsyncMock(
                return_value=(False, mock_error_response)
            )
            mock_client_class.return_value = mock_instance

            result = await _build_oauth_data(
                provider, token, entity_id, resolve_url_template, decrypt_secret
            )

            # Verify the result has no access token (failure case)
            assert result.access_token is None
            # But other data should still be populated
            assert result.client_id == "test-client-id"
            assert result.client_secret == "decrypted-client-secret"

    @pytest.mark.asyncio
    async def test_build_oauth_data_uses_configured_oauth_scope_selection(self):
        """_build_oauth_data should let callers select only configured provider scopes."""
        from src.routers.cli import _build_oauth_data

        # Mock provider with an admin-configured scope allowlist
        provider = MagicMock()
        provider.provider_name = "Microsoft"
        provider.client_id = "test-client-id"
        provider.token_url = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
        provider.token_url_defaults = {}
        provider.oauth_flow_type = "client_credentials"
        provider.authorization_url = None
        provider.scopes = [
            "https://graph.microsoft.com/.default",
            "https://outlook.office365.com/.default",
        ]
        provider.encrypted_client_secret = "encrypted-secret"
        provider.audience = None

        token = None
        entity_id = None  # No entity_id

        def resolve_url_template(url, entity_id, defaults):
            return url

        def decrypt_secret(value):
            return "decrypted-client-secret"

        mock_token_response = {
            "access_token": "exchange-access-token",
            "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
        }

        with patch("src.services.oauth_provider.OAuthProviderClient") as mock_client_class:
            mock_instance = MagicMock()
            mock_instance.get_client_credentials_token = AsyncMock(
                return_value=(True, mock_token_response)
            )
            mock_client_class.return_value = mock_instance

            # Call with an explicitly configured Exchange scope
            result = await _build_oauth_data(
                provider, token, entity_id, resolve_url_template, decrypt_secret,
                oauth_scope="https://outlook.office365.com/.default"
            )

            # Verify OAuthProviderClient was called with the selected allowed scope
            mock_instance.get_client_credentials_token.assert_called_once_with(
                token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
                client_id="test-client-id",
                client_secret="decrypted-client-secret",
                scopes="https://outlook.office365.com/.default",
                audience=None,
            )

            assert result.access_token == "exchange-access-token"

    @pytest.mark.asyncio
    async def test_build_oauth_data_rejects_unconfigured_oauth_scope(self):
        """Caller-supplied oauth_scope must not mint arbitrary resource tokens."""
        from src.routers.cli import _build_oauth_data

        provider = MagicMock()
        provider.provider_name = "Microsoft"
        provider.client_id = "test-client-id"
        provider.token_url = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
        provider.token_url_defaults = {}
        provider.oauth_flow_type = "client_credentials"
        provider.authorization_url = None
        provider.scopes = ["https://graph.microsoft.com/.default"]
        provider.encrypted_client_secret = "encrypted-secret"
        provider.audience = None

        def resolve_url_template(url, entity_id, defaults):
            return url

        def decrypt_secret(value):
            return "decrypted-client-secret"

        with patch("src.services.oauth_provider.OAuthProviderClient") as mock_client_class:
            mock_instance = MagicMock()
            mock_instance.get_client_credentials_token = AsyncMock()
            mock_client_class.return_value = mock_instance

            with pytest.raises(ValueError, match="not configured"):
                await _build_oauth_data(
                    provider, None, None, resolve_url_template, decrypt_secret,
                    oauth_scope="https://outlook.office365.com/.default"
                )

        mock_instance.get_client_credentials_token.assert_not_called()

    @pytest.mark.asyncio
    async def test_build_oauth_data_with_allowed_oauth_scope_and_entity_id(self):
        """_build_oauth_data should select configured scope and resolve entity_id."""
        from src.routers.cli import _build_oauth_data

        # Mock provider with templated URL
        provider = MagicMock()
        provider.provider_name = "Microsoft"
        provider.client_id = "test-client-id"
        provider.token_url = "https://login.microsoftonline.com/{entity_id}/oauth2/v2.0/token"
        provider.token_url_defaults = {}
        provider.oauth_flow_type = "client_credentials"
        provider.authorization_url = None
        provider.scopes = [
            "https://graph.microsoft.com/.default",
            "https://outlook.office365.com/.default",
        ]
        provider.encrypted_client_secret = "encrypted-secret"
        provider.audience = None

        token = None
        entity_id = "customer-tenant-456"

        def resolve_url_template(url, entity_id, defaults):
            return url.replace("{entity_id}", entity_id)

        def decrypt_secret(value):
            return "decrypted-client-secret"

        mock_token_response = {
            "access_token": "exchange-customer-token",
            "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
        }

        with patch("src.services.oauth_provider.OAuthProviderClient") as mock_client_class:
            mock_instance = MagicMock()
            mock_instance.get_client_credentials_token = AsyncMock(
                return_value=(True, mock_token_response)
            )
            mock_client_class.return_value = mock_instance

            # Call with both entity_id and oauth_scope override
            result = await _build_oauth_data(
                provider, token, entity_id, resolve_url_template, decrypt_secret,
                oauth_scope="https://outlook.office365.com/.default"
            )

            # Verify token URL was resolved with entity_id AND scope was overridden
            mock_instance.get_client_credentials_token.assert_called_once_with(
                token_url="https://login.microsoftonline.com/customer-tenant-456/oauth2/v2.0/token",
                client_id="test-client-id",
                client_secret="decrypted-client-secret",
                scopes="https://outlook.office365.com/.default",
                audience=None,
            )

            assert result.access_token == "exchange-customer-token"
