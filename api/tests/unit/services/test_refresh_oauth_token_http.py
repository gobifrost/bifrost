"""Tests for refresh_oauth_token_http — the shared refresh primitive.

This is the single HTTP-refresh code path used by the scheduler, SDK
endpoint, and oauth_connections router. It does not touch the database;
it takes a context dict, performs URL template resolution + client_secret
decryption + the client_credentials vs authorization_code branching, and
returns an outcome dict for the caller to persist.
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4


def _context_client_credentials(
    *,
    entity_id_default=None,
    token_url="https://login.example.com/{entity_id}/token",
):
    defaults = {}
    if entity_id_default:
        defaults["entity_id"] = entity_id_default
    return {
        "token_id": None,
        "provider_id": uuid4(),
        "provider_name": "TestProvider",
        "oauth_flow_type": "client_credentials",
        "client_id": "client-id",
        "encrypted_client_secret": b"encrypted-secret",
        "token_url": token_url,
        "token_url_defaults": defaults,
        "scopes": ["read", "write"],
        "audience": None,
        "encrypted_refresh_token": None,
    }


def _context_authorization_code(
    *,
    entity_id_default=None,
):
    defaults = {}
    if entity_id_default:
        defaults["entity_id"] = entity_id_default
    return {
        "token_id": uuid4(),
        "provider_id": uuid4(),
        "provider_name": "TestProvider",
        "oauth_flow_type": "authorization_code",
        "client_id": "client-id",
        "encrypted_client_secret": b"encrypted-secret",
        "token_url": "https://oauth.example.com/token",
        "token_url_defaults": defaults,
        "scopes": ["openid"],
        "audience": None,
        "encrypted_refresh_token": b"encrypted-refresh-token",
    }


class TestClientCredentialsFlow:
    @pytest.mark.asyncio
    async def test_resolves_entity_id_from_defaults(self):
        """entity_id in token_url_defaults is substituted into the token URL."""
        from src.services.oauth_provider import refresh_oauth_token_http

        td = _context_client_credentials(entity_id_default="tenant-123")

        expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
        mock_response = {
            "access_token": "fresh-token",
            "expires_at": expires_at,
        }

        with (
            patch("src.services.oauth_provider.OAuthProviderClient") as mock_client_class,
            patch("src.services.oauth_provider.decrypt_secret", return_value="decrypted-secret"),
            patch("src.services.oauth_provider.encrypt_secret", return_value="encrypted-fresh"),
        ):
            mock_instance = MagicMock()
            mock_instance.get_client_credentials_token = AsyncMock(
                return_value=(True, mock_response)
            )
            mock_client_class.return_value = mock_instance

            outcome = await refresh_oauth_token_http(td)

        assert outcome["success"] is True
        assert outcome["access_token"] == "fresh-token"
        # The placeholder must have been resolved — no literal {entity_id} in the URL.
        call_kwargs = mock_instance.get_client_credentials_token.call_args.kwargs
        assert call_kwargs["token_url"] == "https://login.example.com/tenant-123/token"
        assert "{entity_id}" not in call_kwargs["token_url"]
        assert call_kwargs["client_secret"] == "decrypted-secret"
        assert call_kwargs["scopes"] == "read write"

    @pytest.mark.asyncio
    async def test_missing_client_secret_returns_error(self):
        from src.services.oauth_provider import refresh_oauth_token_http

        td = _context_client_credentials()
        td["encrypted_client_secret"] = None

        outcome = await refresh_oauth_token_http(td)

        assert outcome["success"] is False
        assert "client secret" in outcome["error"].lower()

    @pytest.mark.asyncio
    async def test_missing_token_url_returns_error(self):
        from src.services.oauth_provider import refresh_oauth_token_http

        td = _context_client_credentials()
        td["token_url"] = None

        with patch("src.services.oauth_provider.decrypt_secret", return_value="decrypted-secret"):
            outcome = await refresh_oauth_token_http(td)

        assert outcome["success"] is False
        assert "token url" in outcome["error"].lower()

    @pytest.mark.asyncio
    async def test_provider_error_response_propagates(self):
        from src.services.oauth_provider import refresh_oauth_token_http

        td = _context_client_credentials(entity_id_default="tenant-123")

        with (
            patch("src.services.oauth_provider.OAuthProviderClient") as mock_client_class,
            patch("src.services.oauth_provider.decrypt_secret", return_value="decrypted-secret"),
        ):
            mock_instance = MagicMock()
            mock_instance.get_client_credentials_token = AsyncMock(
                return_value=(
                    False,
                    {"error": "invalid_client", "error_description": "Bad credentials"},
                )
            )
            mock_client_class.return_value = mock_instance

            outcome = await refresh_oauth_token_http(td)

        assert outcome["success"] is False
        assert "Bad credentials" in outcome["error"]


class TestAuthorizationCodeFlow:
    @pytest.mark.asyncio
    async def test_uses_decrypted_refresh_token(self):
        from src.services.oauth_provider import refresh_oauth_token_http

        td = _context_authorization_code()

        expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
        mock_response = {
            "access_token": "refreshed-token",
            "refresh_token": "new-refresh-token",
            "expires_at": expires_at,
        }

        with (
            patch("src.services.oauth_provider.OAuthProviderClient") as mock_client_class,
            patch(
                "src.services.oauth_provider.decrypt_secret",
                side_effect=["decrypted-secret", "decrypted-refresh", "decrypted-refresh"],
            ),
            patch("src.services.oauth_provider.encrypt_secret", return_value="encrypted-new"),
        ):
            mock_instance = MagicMock()
            mock_instance.refresh_access_token = AsyncMock(
                return_value=(True, mock_response)
            )
            mock_client_class.return_value = mock_instance

            outcome = await refresh_oauth_token_http(td)

        assert outcome["success"] is True
        assert outcome["access_token"] == "refreshed-token"
        assert outcome["refresh_token"] == "new-refresh-token"
        call_kwargs = mock_instance.refresh_access_token.call_args.kwargs
        assert call_kwargs["refresh_token"] == "decrypted-refresh"

    @pytest.mark.asyncio
    async def test_missing_refresh_token_returns_error(self):
        from src.services.oauth_provider import refresh_oauth_token_http

        td = _context_authorization_code()
        td["encrypted_refresh_token"] = None

        with patch("src.services.oauth_provider.decrypt_secret", return_value="decrypted-secret"):
            outcome = await refresh_oauth_token_http(td)

        assert outcome["success"] is False
        assert "refresh token" in outcome["error"].lower()


class TestWorkloadIdentity:
    @pytest.mark.asyncio
    async def test_rejects_stale_token_context_without_decrypting_or_calling_provider(self):
        """Host-managed identity must never turn stale OAuth material into a token call."""
        from src.services.oauth_provider import refresh_oauth_token_http

        td = _context_client_credentials()
        td["oauth_flow_type"] = "workload_identity"

        with (
            patch("src.services.oauth_provider.decrypt_secret") as decrypt,
            patch("src.services.oauth_provider.OAuthProviderClient") as client,
        ):
            outcome = await refresh_oauth_token_http(td)

        assert outcome["success"] is False
        assert (
            outcome["error"]
            == "Workload Identity is SDK managed and does not obtain or refresh OAuth tokens"
        )
        decrypt.assert_not_called()
        client.assert_not_called()


class TestOutcomeShape:
    @pytest.mark.asyncio
    async def test_success_outcome_contains_encrypted_access_token(self):
        from src.services.oauth_provider import refresh_oauth_token_http

        td = _context_client_credentials(entity_id_default="tenant-123")

        mock_response = {
            "access_token": "fresh-token",
            "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
            "scope": "read write",
        }

        with (
            patch("src.services.oauth_provider.OAuthProviderClient") as mock_client_class,
            patch("src.services.oauth_provider.decrypt_secret", return_value="decrypted-secret"),
            patch("src.services.oauth_provider.encrypt_secret", return_value="encrypted-fresh"),
        ):
            mock_instance = MagicMock()
            mock_instance.get_client_credentials_token = AsyncMock(
                return_value=(True, mock_response)
            )
            mock_client_class.return_value = mock_instance

            outcome = await refresh_oauth_token_http(td)

        assert outcome["success"] is True
        assert outcome["access_token"] == "fresh-token"
        assert outcome["encrypted_access_token"] == b"encrypted-fresh"
        assert outcome["expires_at"] is not None
        assert outcome["scopes"] == ["read", "write"]
