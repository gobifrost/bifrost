"""Tests for POST /api/cli/integrations/refresh_token endpoint.

These tests exercise the adapter-shaped handler; the underlying refresh
orchestration is covered by
``api/tests/unit/services/test_refresh_oauth_token_http.py``.
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, AsyncMock, patch
from uuid import uuid4


def _compiled_sql(statement) -> str:
    return str(statement.compile(compile_kwargs={"literal_binds": False})).lower()


class TestRefreshTokenClientCredentials:
    """Test refresh_token endpoint for client_credentials flows."""

    @pytest.mark.asyncio
    async def test_refreshes_client_credentials_token(self):
        """Should fetch a fresh token and persist it to DB."""
        from src.routers.cli import sdk_integrations_refresh_token
        from src.models.contracts.cli import SDKIntegrationsRefreshTokenRequest

        request = SDKIntegrationsRefreshTokenRequest(
            connection_name="Pax8",
        )

        mock_user = MagicMock()
        mock_user.user_id = uuid4()
        mock_user.email = "test@example.com"

        mock_db = AsyncMock()

        provider_id = uuid4()

        mock_provider = MagicMock()
        mock_provider.id = provider_id
        mock_provider.provider_name = "Pax8"
        mock_provider.client_id = "pax8-client-id"
        mock_provider.encrypted_client_secret = b"encrypted-secret"
        mock_provider.token_url = "https://login.pax8.com/oauth/token"
        mock_provider.token_url_defaults = {}
        mock_provider.oauth_flow_type = "client_credentials"
        mock_provider.scopes = ["read", "write"]
        mock_provider.integration_id = None
        mock_provider.organization_id = None
        mock_provider.audience = None

        # Mock DB execute:
        #   1. provider lookup
        #   2. persist-time token lookup (client_credentials branch)
        provider_result = MagicMock()
        provider_result.scalars.return_value.first.return_value = mock_provider
        token_result = MagicMock()
        token_result.scalars.return_value.first.return_value = None

        mock_db.execute = AsyncMock(side_effect=[provider_result, token_result])
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()

        expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
        mock_token_response = {
            "access_token": "fresh-pax8-token",
            "expires_at": expires_at,
        }

        with (
            patch("src.routers.cli._get_cli_org_id", new_callable=AsyncMock, return_value=None),
            patch("src.services.oauth_provider.OAuthProviderClient") as mock_client_class,
            patch("src.services.oauth_provider.decrypt_secret", return_value="decrypted-secret"),
            patch("src.services.oauth_provider.encrypt_secret", return_value="encrypted-new-token"),
        ):
            mock_instance = MagicMock()
            mock_instance.get_client_credentials_token = AsyncMock(
                return_value=(True, mock_token_response)
            )
            mock_client_class.return_value = mock_instance

            result = await sdk_integrations_refresh_token(request, mock_user, mock_db)

        assert result.refreshed is True
        assert result.expires_at is not None
        response_payload = result.model_dump()
        assert "access_token" not in response_payload
        assert "refresh_token" not in response_payload
        assert "client_secret" not in response_payload
        mock_instance.get_client_credentials_token.assert_called_once_with(
            token_url="https://login.pax8.com/oauth/token",
            client_id="pax8-client-id",
            client_secret="decrypted-secret",
            scopes="read write",
            audience=None,
        )
        # Token should be persisted (new record since no existing token)
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_updates_existing_token_record(self):
        """Should update existing token record instead of creating new one."""
        from src.routers.cli import sdk_integrations_refresh_token
        from src.models.contracts.cli import SDKIntegrationsRefreshTokenRequest

        request = SDKIntegrationsRefreshTokenRequest(connection_name="Pax8")

        mock_user = MagicMock()
        mock_user.user_id = uuid4()
        mock_user.email = "test@example.com"
        mock_db = AsyncMock()

        provider_id = uuid4()
        mock_provider = MagicMock()
        mock_provider.id = provider_id
        mock_provider.provider_name = "Pax8"
        mock_provider.client_id = "pax8-client-id"
        mock_provider.encrypted_client_secret = b"encrypted-secret"
        mock_provider.token_url = "https://login.pax8.com/oauth/token"
        mock_provider.token_url_defaults = {}
        mock_provider.oauth_flow_type = "client_credentials"
        mock_provider.scopes = []
        mock_provider.integration_id = None
        mock_provider.organization_id = None
        mock_provider.audience = None

        # Existing token
        mock_existing_token = MagicMock()

        provider_result = MagicMock()
        provider_result.scalars.return_value.first.return_value = mock_provider
        token_result = MagicMock()
        token_result.scalars.return_value.first.return_value = mock_existing_token

        mock_db.execute = AsyncMock(side_effect=[provider_result, token_result])
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()

        expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
        mock_token_response = {
            "access_token": "new-token",
            "expires_at": expires_at,
        }

        with (
            patch("src.routers.cli._get_cli_org_id", new_callable=AsyncMock, return_value=None),
            patch("src.services.oauth_provider.OAuthProviderClient") as mock_client_class,
            patch("src.services.oauth_provider.decrypt_secret", return_value="decrypted-secret"),
            patch("src.services.oauth_provider.encrypt_secret", return_value="encrypted-new-token"),
        ):
            mock_instance = MagicMock()
            mock_instance.get_client_credentials_token = AsyncMock(
                return_value=(True, mock_token_response)
            )
            mock_client_class.return_value = mock_instance

            result = await sdk_integrations_refresh_token(request, mock_user, mock_db)

        assert result.refreshed is True
        response_payload = result.model_dump()
        assert "access_token" not in response_payload
        assert "refresh_token" not in response_payload
        assert "client_secret" not in response_payload
        # Existing token should be updated, not a new one added
        mock_db.add.assert_not_called()
        assert mock_existing_token.expires_at == expires_at

    @pytest.mark.asyncio
    async def test_provider_and_token_lookup_are_org_scoped(self):
        """Refresh must not select another org's provider with the same name."""
        from src.routers.cli import sdk_integrations_refresh_token
        from src.models.contracts.cli import SDKIntegrationsRefreshTokenRequest

        org_id = uuid4()
        provider_id = uuid4()
        request = SDKIntegrationsRefreshTokenRequest(
            connection_name="Pax8",
            scope=str(org_id),
        )

        mock_user = MagicMock()
        mock_user.user_id = uuid4()
        mock_user.email = "test@example.com"
        mock_db = AsyncMock()

        mock_provider = MagicMock()
        mock_provider.id = provider_id
        mock_provider.provider_name = "Pax8"
        mock_provider.client_id = "pax8-client-id"
        mock_provider.encrypted_client_secret = b"encrypted-secret"
        mock_provider.token_url = "https://login.pax8.com/oauth/token"
        mock_provider.token_url_defaults = {}
        mock_provider.oauth_flow_type = "client_credentials"
        mock_provider.scopes = []
        mock_provider.integration_id = None
        mock_provider.organization_id = org_id
        mock_provider.audience = None

        provider_result = MagicMock()
        provider_result.scalars.return_value.first.return_value = mock_provider
        token_result = MagicMock()
        token_result.scalars.return_value.first.return_value = None
        global_token_result = MagicMock()
        global_token_result.scalars.return_value.first.return_value = None
        mock_db.execute = AsyncMock(
            side_effect=[provider_result, token_result, global_token_result]
        )
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()

        expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
        mock_token_response = {
            "access_token": "fresh-pax8-token",
            "expires_at": expires_at,
        }

        with (
            patch(
                "src.routers.cli._get_cli_org_id",
                new_callable=AsyncMock,
                return_value=str(org_id),
            ),
            patch("src.services.oauth_provider.OAuthProviderClient") as mock_client_class,
            patch("src.services.oauth_provider.decrypt_secret", return_value="decrypted-secret"),
            patch("src.services.oauth_provider.encrypt_secret", return_value="encrypted-new-token"),
        ):
            mock_instance = MagicMock()
            mock_instance.get_client_credentials_token = AsyncMock(
                return_value=(True, mock_token_response)
            )
            mock_client_class.return_value = mock_instance

            await sdk_integrations_refresh_token(request, mock_user, mock_db)

        provider_stmt = mock_db.execute.await_args_list[0].args[0]
        token_stmt = mock_db.execute.await_args_list[1].args[0]
        provider_sql = _compiled_sql(provider_stmt)
        token_sql = _compiled_sql(token_stmt)
        assert "oauth_providers.provider_name" in provider_sql
        assert "oauth_providers.organization_id" in provider_sql
        assert "oauth_tokens.provider_id" in token_sql
        assert "oauth_tokens.organization_id" in token_sql
        created_token = mock_db.add.call_args.args[0]
        assert created_token.organization_id == org_id

    @pytest.mark.asyncio
    async def test_scoped_refresh_falls_back_to_global_provider_and_token(self):
        """Scoped refresh should work when the provider/token are global rows."""
        from src.routers.cli import sdk_integrations_refresh_token
        from src.models.contracts.cli import SDKIntegrationsRefreshTokenRequest

        org_id = uuid4()
        request = SDKIntegrationsRefreshTokenRequest(
            connection_name="NinjaOne",
            scope=str(org_id),
        )

        mock_user = MagicMock()
        mock_user.user_id = uuid4()
        mock_user.email = "test@example.com"
        mock_db = AsyncMock()

        provider_id = uuid4()
        mock_provider = MagicMock()
        mock_provider.id = provider_id
        mock_provider.provider_name = "NinjaOne"
        mock_provider.client_id = "ninja-client-id"
        mock_provider.encrypted_client_secret = b"encrypted-secret"
        mock_provider.token_url = "https://app.ninjarmm.com/ws/oauth/token"
        mock_provider.token_url_defaults = {}
        mock_provider.oauth_flow_type = "client_credentials"
        mock_provider.scopes = ["monitoring"]
        mock_provider.integration_id = None
        mock_provider.organization_id = None
        mock_provider.audience = None

        mock_existing_token = MagicMock()
        mock_existing_token.organization_id = None

        scoped_provider_result = MagicMock()
        scoped_provider_result.scalars.return_value.first.return_value = None
        global_provider_result = MagicMock()
        global_provider_result.scalars.return_value.first.return_value = mock_provider
        scoped_token_result = MagicMock()
        scoped_token_result.scalars.return_value.first.return_value = None
        global_token_result = MagicMock()
        global_token_result.scalars.return_value.first.return_value = mock_existing_token

        mock_db.execute = AsyncMock(
            side_effect=[
                scoped_provider_result,
                global_provider_result,
                scoped_token_result,
                global_token_result,
            ]
        )
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()

        expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
        mock_token_response = {
            "access_token": "new-token",
            "expires_at": expires_at,
        }

        with (
            patch(
                "src.routers.cli._get_cli_org_id",
                new_callable=AsyncMock,
                return_value=str(org_id),
            ),
            patch("src.services.oauth_provider.OAuthProviderClient") as mock_client_class,
            patch("src.services.oauth_provider.decrypt_secret", return_value="decrypted-secret"),
            patch("src.services.oauth_provider.encrypt_secret", return_value="encrypted-new-token"),
        ):
            mock_instance = MagicMock()
            mock_instance.get_client_credentials_token = AsyncMock(
                return_value=(True, mock_token_response)
            )
            mock_client_class.return_value = mock_instance

            result = await sdk_integrations_refresh_token(request, mock_user, mock_db)

        assert result.refreshed is True
        assert mock_db.execute.call_count == 4
        mock_db.add.assert_not_called()
        assert mock_existing_token.expires_at == expires_at

    @pytest.mark.asyncio
    async def test_global_refresh_only_selects_global_provider(self):
        """Default/global refresh must not fall through to tenant providers."""
        from fastapi import HTTPException
        from src.routers.cli import sdk_integrations_refresh_token
        from src.models.contracts.cli import SDKIntegrationsRefreshTokenRequest

        request = SDKIntegrationsRefreshTokenRequest(connection_name="Pax8")
        mock_user = MagicMock()
        mock_user.user_id = uuid4()
        mock_user.email = "test@example.com"
        mock_db = AsyncMock()

        provider_result = MagicMock()
        provider_result.scalars.return_value.first.return_value = None
        mock_db.execute = AsyncMock(return_value=provider_result)

        with (
            patch("src.routers.cli._get_cli_org_id", new_callable=AsyncMock, return_value=None),
            pytest.raises(HTTPException) as exc_info,
        ):
            await sdk_integrations_refresh_token(request, mock_user, mock_db)

        provider_stmt = mock_db.execute.await_args.args[0]
        provider_sql = _compiled_sql(provider_stmt)
        assert "oauth_providers.provider_name" in provider_sql
        assert "oauth_providers.organization_id is null" in provider_sql
        assert exc_info.value.status_code == 404


class TestRefreshTokenAuthorizationCode:
    """Test refresh_token endpoint for authorization_code flows."""

    @pytest.mark.asyncio
    async def test_refreshes_authorization_code_token(self):
        """Should use stored refresh_token for authorization_code flows."""
        from src.routers.cli import sdk_integrations_refresh_token
        from src.models.contracts.cli import SDKIntegrationsRefreshTokenRequest

        request = SDKIntegrationsRefreshTokenRequest(connection_name="Microsoft")

        mock_user = MagicMock()
        mock_user.user_id = uuid4()
        mock_user.email = "test@example.com"
        mock_db = AsyncMock()

        provider_id = uuid4()
        mock_provider = MagicMock()
        mock_provider.id = provider_id
        mock_provider.provider_name = "Microsoft"
        mock_provider.client_id = "ms-client-id"
        mock_provider.encrypted_client_secret = b"encrypted-secret"
        mock_provider.token_url = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
        mock_provider.token_url_defaults = {}
        mock_provider.oauth_flow_type = "authorization_code"
        mock_provider.scopes = ["openid", "profile"]
        mock_provider.integration_id = None
        mock_provider.organization_id = None
        mock_provider.audience = None

        # Mock stored token with refresh_token
        mock_stored_token = MagicMock()
        mock_stored_token.id = uuid4()
        mock_stored_token.encrypted_refresh_token = b"encrypted-refresh-token"

        provider_result = MagicMock()
        provider_result.scalars.return_value.first.return_value = mock_provider

        token_for_refresh = MagicMock()
        token_for_refresh.scalars.return_value.first.return_value = mock_stored_token

        # In the new adapter-shaped handler, the stored token loaded up front
        # is the same row we persist into — no second lookup is issued.
        mock_db.execute = AsyncMock(side_effect=[
            provider_result,
            token_for_refresh,
        ])
        mock_db.commit = AsyncMock()
        mock_db.add = MagicMock()

        expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
        mock_token_response = {
            "access_token": "refreshed-ms-token",
            "refresh_token": "new-refresh-token",
            "expires_at": expires_at,
        }

        with (
            patch("src.routers.cli._get_cli_org_id", new_callable=AsyncMock, return_value=None),
            patch("src.services.oauth_provider.OAuthProviderClient") as mock_client_class,
            patch("src.services.oauth_provider.decrypt_secret", return_value="decrypted-value"),
            patch("src.services.oauth_provider.encrypt_secret", return_value="encrypted-new-value"),
        ):
            mock_instance = MagicMock()
            mock_instance.refresh_access_token = AsyncMock(
                return_value=(True, mock_token_response)
            )
            mock_client_class.return_value = mock_instance

            result = await sdk_integrations_refresh_token(request, mock_user, mock_db)

        assert result.refreshed is True
        response_payload = result.model_dump()
        assert "access_token" not in response_payload
        assert "refresh_token" not in response_payload
        assert "client_secret" not in response_payload
        mock_instance.refresh_access_token.assert_called_once()

    @pytest.mark.asyncio
    async def test_clears_waiting_callback_status_on_success(self):
        """A stuck waiting_callback provider should flip to completed after a successful SDK refresh.

        Regression guard: the SDK refresh endpoint previously updated token
        data (expires_at, access_token) without touching provider.status,
        leaving providers stuck at waiting_callback whenever a user abandoned
        an authorize flow while workflow code kept calling .refresh().
        """
        from src.routers.cli import sdk_integrations_refresh_token
        from src.models.contracts.cli import SDKIntegrationsRefreshTokenRequest

        request = SDKIntegrationsRefreshTokenRequest(connection_name="NinjaOne")

        mock_user = MagicMock()
        mock_user.user_id = uuid4()
        mock_user.email = "test@example.com"
        mock_db = AsyncMock()

        mock_provider = MagicMock()
        mock_provider.id = uuid4()
        mock_provider.provider_name = "NinjaOne"
        mock_provider.client_id = "ninja-client-id"
        mock_provider.encrypted_client_secret = b"encrypted-secret"
        mock_provider.token_url = "https://app.ninjarmm.com/ws/oauth/token"
        mock_provider.token_url_defaults = {}
        mock_provider.oauth_flow_type = "authorization_code"
        mock_provider.scopes = ["monitoring"]
        mock_provider.integration_id = None
        mock_provider.organization_id = None
        mock_provider.audience = None
        # Simulate the stuck state: user clicked Reconnect then abandoned the browser flow.
        mock_provider.status = "waiting_callback"
        mock_provider.status_message = "Waiting for user to complete authorization"
        mock_provider.last_token_refresh = None

        mock_stored_token = MagicMock()
        mock_stored_token.id = uuid4()
        mock_stored_token.encrypted_refresh_token = b"encrypted-refresh-token"

        provider_result = MagicMock()
        provider_result.scalars.return_value.first.return_value = mock_provider
        token_result = MagicMock()
        token_result.scalars.return_value.first.return_value = mock_stored_token

        mock_db.execute = AsyncMock(side_effect=[provider_result, token_result])
        mock_db.commit = AsyncMock()
        mock_db.add = MagicMock()

        expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
        mock_token_response = {
            "access_token": "refreshed-ninja-token",
            "refresh_token": "new-refresh-token",
            "expires_at": expires_at,
        }

        with (
            patch("src.routers.cli._get_cli_org_id", new_callable=AsyncMock, return_value=None),
            patch("src.services.oauth_provider.OAuthProviderClient") as mock_client_class,
            patch("src.services.oauth_provider.decrypt_secret", return_value="decrypted-value"),
            patch("src.services.oauth_provider.encrypt_secret", return_value="encrypted-new-value"),
        ):
            mock_instance = MagicMock()
            mock_instance.refresh_access_token = AsyncMock(
                return_value=(True, mock_token_response)
            )
            mock_client_class.return_value = mock_instance

            await sdk_integrations_refresh_token(request, mock_user, mock_db)

        assert mock_provider.status == "completed"
        assert mock_provider.status_message is None
        assert mock_provider.last_token_refresh is not None
        assert isinstance(mock_provider.last_token_refresh, datetime)
        assert mock_provider.last_token_refresh.tzinfo is not None

    @pytest.mark.asyncio
    async def test_fails_when_no_refresh_token_stored(self):
        """Should fail when no refresh_token is available for authorization_code flow."""
        from fastapi import HTTPException
        from src.routers.cli import sdk_integrations_refresh_token
        from src.models.contracts.cli import SDKIntegrationsRefreshTokenRequest

        request = SDKIntegrationsRefreshTokenRequest(connection_name="NoRefresh")

        mock_user = MagicMock()
        mock_user.user_id = uuid4()
        mock_user.email = "test@example.com"
        mock_db = AsyncMock()

        mock_provider = MagicMock()
        mock_provider.id = uuid4()
        mock_provider.provider_name = "NoRefresh"
        mock_provider.client_id = "client-id"
        mock_provider.encrypted_client_secret = b"encrypted-secret"
        mock_provider.token_url = "https://oauth.example.com/token"
        mock_provider.token_url_defaults = {}
        mock_provider.oauth_flow_type = "authorization_code"
        mock_provider.scopes = []
        mock_provider.integration_id = None
        mock_provider.organization_id = None
        mock_provider.audience = None

        # Token with no refresh_token
        mock_stored_token = MagicMock()
        mock_stored_token.encrypted_refresh_token = None

        provider_result = MagicMock()
        provider_result.scalars.return_value.first.return_value = mock_provider

        token_result = MagicMock()
        token_result.scalars.return_value.first.return_value = mock_stored_token

        mock_db.execute = AsyncMock(side_effect=[
            provider_result,
            token_result,
        ])

        with (
            patch("src.routers.cli._get_cli_org_id", new_callable=AsyncMock, return_value=None),
            pytest.raises(HTTPException) as exc_info,
        ):
            await sdk_integrations_refresh_token(request, mock_user, mock_db)

        assert exc_info.value.status_code == 400
        assert "no refresh_token" in exc_info.value.detail


class TestRefreshTokenErrorHandling:
    """Test error handling in refresh_token endpoint."""

    @pytest.mark.asyncio
    async def test_provider_not_found(self):
        """Should return 404 when provider doesn't exist."""
        from fastapi import HTTPException
        from src.routers.cli import sdk_integrations_refresh_token
        from src.models.contracts.cli import SDKIntegrationsRefreshTokenRequest

        request = SDKIntegrationsRefreshTokenRequest(connection_name="NonExistent")

        mock_user = MagicMock()
        mock_user.user_id = uuid4()
        mock_user.email = "test@example.com"
        mock_db = AsyncMock()

        # Provider not found
        provider_result = MagicMock()
        provider_result.scalars.return_value.first.return_value = None
        mock_db.execute = AsyncMock(return_value=provider_result)

        with (
            patch("src.routers.cli._get_cli_org_id", new_callable=AsyncMock, return_value=None),
            pytest.raises(HTTPException) as exc_info,
        ):
            await sdk_integrations_refresh_token(request, mock_user, mock_db)

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_token_refresh_failure_returns_502(self):
        """Should return 502 when the OAuth provider rejects the request."""
        from fastapi import HTTPException
        from src.routers.cli import sdk_integrations_refresh_token
        from src.models.contracts.cli import SDKIntegrationsRefreshTokenRequest

        request = SDKIntegrationsRefreshTokenRequest(connection_name="Pax8")

        mock_user = MagicMock()
        mock_user.user_id = uuid4()
        mock_user.email = "test@example.com"
        mock_db = AsyncMock()

        mock_provider = MagicMock()
        mock_provider.id = uuid4()
        mock_provider.provider_name = "Pax8"
        mock_provider.client_id = "pax8-client-id"
        mock_provider.encrypted_client_secret = b"encrypted-secret"
        mock_provider.token_url = "https://login.pax8.com/oauth/token"
        mock_provider.token_url_defaults = {}
        mock_provider.oauth_flow_type = "client_credentials"
        mock_provider.scopes = []
        mock_provider.integration_id = None
        mock_provider.organization_id = None
        mock_provider.audience = None

        provider_result = MagicMock()
        provider_result.scalars.return_value.first.return_value = mock_provider
        mock_db.execute = AsyncMock(return_value=provider_result)

        with (
            patch("src.routers.cli._get_cli_org_id", new_callable=AsyncMock, return_value=None),
            patch("src.services.oauth_provider.OAuthProviderClient") as mock_client_class,
            patch("src.services.oauth_provider.decrypt_secret", return_value="decrypted-secret"),
            pytest.raises(HTTPException) as exc_info,
        ):
            mock_instance = MagicMock()
            mock_instance.get_client_credentials_token = AsyncMock(
                return_value=(False, {"error": "invalid_client", "error_description": "Bad credentials"})
            )
            mock_client_class.return_value = mock_instance

            await sdk_integrations_refresh_token(request, mock_user, mock_db)

        assert exc_info.value.status_code == 502
        assert "Bad credentials" in exc_info.value.detail


class TestRefreshTokenSDKModel:
    """Test the OAuthCredentials.refresh() SDK method."""

    @pytest.mark.asyncio
    async def test_refresh_updates_expires_at_without_token_material(self):
        """refresh() should update expires_at without requiring returned tokens."""
        from bifrost.models import OAuthCredentials

        creds = OAuthCredentials(
            connection_name="Pax8",
            client_id="test",
            client_secret=None,
            authorization_url=None,
            token_url=None,
            scopes=[],
            access_token="old-token",
            refresh_token=None,
            expires_at=None,
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "refreshed": True,
            "expires_at": "2026-03-02T00:00:00+00:00",
        }

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with (
            patch("bifrost.client.get_client", return_value=mock_client),
            patch("bifrost._context.register_secret"),
        ):
            result = await creds.refresh()

        assert result is creds  # returns self
        assert creds.access_token == "old-token"
        assert creds.expires_at == "2026-03-02T00:00:00+00:00"
        mock_client.post.assert_called_once_with(
            "/api/cli/integrations/refresh_token",
            json={"connection_name": "Pax8"},
        )

    @pytest.mark.asyncio
    async def test_refresh_does_not_register_response_secrets(self):
        """refresh() should not receive or register secret material."""
        from bifrost.models import OAuthCredentials

        creds = OAuthCredentials(
            connection_name="Pax8",
            client_id="test",
            client_secret=None,
            authorization_url=None,
            token_url=None,
            scopes=[],
            access_token="old-token",
            refresh_token=None,
            expires_at=None,
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "refreshed": True,
            "expires_at": None,
        }

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with (
            patch("bifrost.client.get_client", return_value=mock_client),
            patch("bifrost._context.register_secret") as mock_register,
        ):
            await creds.refresh()

        mock_register.assert_not_called()
        assert creds.access_token == "old-token"

    @pytest.mark.asyncio
    async def test_refresh_raises_on_failure(self):
        """refresh() should raise RuntimeError on non-200 response."""
        from bifrost.models import OAuthCredentials

        creds = OAuthCredentials(
            connection_name="Pax8",
            client_id="test",
            client_secret=None,
            authorization_url=None,
            token_url=None,
            scopes=[],
            access_token="old-token",
            refresh_token=None,
            expires_at=None,
        )

        mock_response = MagicMock()
        mock_response.status_code = 502
        mock_response.text = "Token refresh failed: Bad credentials"

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with (
            patch("bifrost.client.get_client", return_value=mock_client),
            pytest.raises(RuntimeError, match="Token refresh failed"),
        ):
            await creds.refresh()
