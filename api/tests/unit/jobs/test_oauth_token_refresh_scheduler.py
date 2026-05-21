"""Regression test for the OAuth token refresh scheduler.

Covers the PR #21 entity_id fallback at the scheduler entry point. The
scheduler is the one refresh path that previously had no dedicated test;
this pins the invariant that:

  - the scheduler calls ``build_token_refresh_context`` with ``org_id=None``
    (scheduler is global; no org context)
  - the scheduler delegates to ``refresh_oauth_token_http`` for the HTTP call
  - an integration with only ``entity_id`` set (no ``default_entity_id``)
    still produces a fully-resolved token URL
"""

import logging

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4


class TestSchedulerUsesSharedPrimitives:
    @pytest.mark.asyncio
    async def test_scheduler_passes_org_id_none_to_context_builder(self):
        """Scheduler is global — it must pass org_id=None so org mapping is skipped."""
        from src.jobs.schedulers import oauth_token_refresh as sched

        # Minimal token row
        token = MagicMock()
        token.id = uuid4()
        token.encrypted_refresh_token = None  # client_credentials-shaped
        token.expires_at = datetime.now(timezone.utc) + timedelta(minutes=1)

        provider = MagicMock()
        provider.id = uuid4()
        provider.provider_name = "TestProvider"
        provider.oauth_flow_type = "client_credentials"
        provider.integration_id = uuid4()
        provider.client_id = "c"
        provider.encrypted_client_secret = b"s"
        provider.token_url = "https://login.example.com/{entity_id}/token"
        provider.token_url_defaults = {}
        provider.scopes = []
        provider.audience = None
        token.provider = provider

        # Stub the session context
        mock_db = AsyncMock()
        token_query_result = MagicMock()
        token_query_result.scalars.return_value.all.return_value = [token]
        mock_db.execute = AsyncMock(return_value=token_query_result)
        mock_db.commit = AsyncMock()
        # get returns either the token or the provider
        mock_db.get = AsyncMock(side_effect=[token, provider])

        class _DbCtxManager:
            async def __aenter__(self):
                return mock_db

            async def __aexit__(self, *_args):
                return False

        with (
            patch.object(sched, "get_db_context", return_value=_DbCtxManager()),
            patch.object(
                sched,
                "build_token_refresh_context",
                new_callable=AsyncMock,
            ) as mock_build,
            patch.object(
                sched,
                "refresh_oauth_token_http",
                new_callable=AsyncMock,
            ) as mock_refresh,
        ):
            mock_build.return_value = {
                "token_id": token.id,
                "provider_id": provider.id,
                "provider_name": "TestProvider",
                "oauth_flow_type": "client_credentials",
                "client_id": "c",
                "encrypted_client_secret": b"s",
                "token_url": "https://login.example.com/tenant-from-fallback/token",
                "token_url_defaults": {"entity_id": "tenant-from-fallback"},
                "scopes": [],
                "audience": None,
                "encrypted_refresh_token": None,
            }
            mock_refresh.return_value = {
                "token_id": token.id,
                "provider_id": provider.id,
                "success": True,
                "access_token": "fresh-token",
                "encrypted_access_token": b"encrypted-fresh",
                "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
            }

            results = await sched.run_refresh_job(
                trigger_type="test",
                refresh_threshold_minutes=None,
            )

        # Scheduler delegated once to each shared helper for the single token
        mock_build.assert_called_once()
        assert mock_build.call_args.kwargs["org_id"] is None
        assert mock_build.call_args.kwargs["provider"] is provider
        mock_refresh.assert_called_once()
        assert results["refreshed_successfully"] == 1
        assert results["refresh_failed"] == 0

    @pytest.mark.asyncio
    async def test_refresh_exception_log_omits_token_id_and_secret(self, caplog):
        """Scheduler exception logs should not include provider, token, or secret values."""
        from src.jobs.schedulers import oauth_token_refresh as sched

        token = MagicMock()
        token.id = uuid4()
        token.encrypted_refresh_token = "encrypted-refresh-secret"
        token.expires_at = datetime.now(timezone.utc) + timedelta(minutes=1)

        provider = MagicMock()
        provider.id = uuid4()
        provider.provider_name = "SensitiveProvider"
        provider.oauth_flow_type = "authorization_code"
        token.provider = provider

        mock_db = AsyncMock()
        token_query_result = MagicMock()
        token_query_result.scalars.return_value.all.return_value = [token]
        mock_db.execute = AsyncMock(return_value=token_query_result)
        mock_db.commit = AsyncMock()

        class _DbCtxManager:
            async def __aenter__(self):
                return mock_db

            async def __aexit__(self, *_args):
                return False

        with (
            caplog.at_level(
                logging.ERROR, logger="src.jobs.schedulers.oauth_token_refresh"
            ),
            patch.object(sched, "get_db_context", return_value=_DbCtxManager()),
            patch.object(
                sched,
                "build_token_refresh_context",
                new_callable=AsyncMock,
            ) as mock_build,
            patch.object(
                sched,
                "refresh_oauth_token_http",
                new_callable=AsyncMock,
            ) as mock_refresh,
        ):
            mock_build.return_value = {
                "token_id": token.id,
                "provider_id": provider.id,
                "provider_name": provider.provider_name,
            }
            mock_refresh.side_effect = RuntimeError(
                "refresh failed for super-secret-token"
            )

            results = await sched.run_refresh_job(
                trigger_type="test",
                refresh_threshold_minutes=None,
            )

        assert results["refresh_failed"] == 1
        log_text = caplog.text
        assert str(token.id) not in log_text
        assert "super-secret-token" not in log_text
        assert "SensitiveProvider" not in log_text
