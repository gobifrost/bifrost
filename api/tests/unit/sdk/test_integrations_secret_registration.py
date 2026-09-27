import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.sdk.context import ExecutionContext


def _make_oauth_response(access_token="access-tok-abc123", refresh_token="refresh-tok-xyz789", client_secret="client-sec-def456"):
    return {
        "integration_id": "integ-uuid-1",
        "entity_id": "tenant-1",
        "entity_name": "Test Entity",
        "config": {},
        "oauth": {
            "connection_name": "TestConn",
            "client_id": "client-id",
            "client_secret": client_secret,
            "authorization_url": None,
            "token_url": None,
            "scopes": [],
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": None,
        },
    }


class TestIntegrationsGetRegistersSecrets:
    def _make_ctx(self):
        return ExecutionContext(
            user_id="u1", email="e@e.com", name="Test",
            scope="org-123", organization=None,
            is_platform_admin=False, is_function_key=False,
            execution_id="exec-1",
        )

    @pytest.mark.asyncio
    async def test_oauth_secrets_registered(self):
        from bifrost.integrations import integrations
        from bifrost._context import set_execution_context, clear_execution_context

        ctx = self._make_ctx()
        set_execution_context(ctx)
        try:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = _make_oauth_response()
            mock_client = AsyncMock()
            mock_client.engine_request = AsyncMock(return_value=mock_response)

            with patch("bifrost.integrations.get_client", return_value=mock_client):
                result = await integrations.get("TestIntegration")

            assert result is not None
            secrets = ctx._collect_secret_values()
            assert "access-tok-abc123" in secrets
            assert "refresh-tok-xyz789" in secrets
            assert "client-sec-def456" in secrets
        finally:
            clear_execution_context()

    @pytest.mark.asyncio
    async def test_none_oauth_fields_not_registered(self):
        from bifrost.integrations import integrations
        from bifrost._context import set_execution_context, clear_execution_context

        ctx = self._make_ctx()
        set_execution_context(ctx)
        try:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = _make_oauth_response(
                access_token=None, refresh_token=None, client_secret=None
            )
            mock_client = AsyncMock()
            mock_client.engine_request = AsyncMock(return_value=mock_response)

            with patch("bifrost.integrations.get_client", return_value=mock_client):
                await integrations.get("TestIntegration")

            assert ctx._collect_secret_values() == set()
        finally:
            clear_execution_context()

    @pytest.mark.asyncio
    async def test_no_oauth_block(self):
        from bifrost.integrations import integrations
        from bifrost._context import set_execution_context, clear_execution_context

        ctx = self._make_ctx()
        set_execution_context(ctx)
        try:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "integration_id": "id", "entity_id": "t", "entity_name": None,
                "config": {}, "oauth": None,
            }
            mock_client = AsyncMock()
            mock_client.engine_request = AsyncMock(return_value=mock_response)

            with patch("bifrost.integrations.get_client", return_value=mock_client):
                await integrations.get("TestIntegration")

            assert ctx._collect_secret_values() == set()
        finally:
            clear_execution_context()

    @pytest.mark.asyncio
    async def test_config_secrets_registered(self):
        """Config values identified by config_secret_keys should be registered for log masking."""
        from bifrost.integrations import integrations
        from bifrost._context import set_execution_context, clear_execution_context

        ctx = self._make_ctx()
        set_execution_context(ctx)
        try:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "integration_id": "id", "entity_id": "t", "entity_name": None,
                "config": {"api_key": "sk-secret-123", "base_url": "https://example.com"},
                "oauth": None,
                "config_secret_keys": ["api_key"],
            }
            mock_client = AsyncMock()
            mock_client.engine_request = AsyncMock(return_value=mock_response)

            with patch("bifrost.integrations.get_client", return_value=mock_client):
                result = await integrations.get("TestIntegration")

            assert result is not None
            secrets = ctx._collect_secret_values()
            assert "sk-secret-123" in secrets, "Secret config value should be registered"
            assert "https://example.com" not in secrets, "Non-secret config value should not be registered"
        finally:
            clear_execution_context()

    @pytest.mark.asyncio
    async def test_config_secrets_empty_value_not_registered(self):
        """Config secret keys with no value should not be registered."""
        from bifrost.integrations import integrations
        from bifrost._context import set_execution_context, clear_execution_context

        ctx = self._make_ctx()
        set_execution_context(ctx)
        try:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "integration_id": "id", "entity_id": "t", "entity_name": None,
                "config": {"api_key": None},
                "oauth": None,
                "config_secret_keys": ["api_key"],
            }
            mock_client = AsyncMock()
            mock_client.engine_request = AsyncMock(return_value=mock_response)

            with patch("bifrost.integrations.get_client", return_value=mock_client):
                result = await integrations.get("TestIntegration")

            assert result is not None
            secrets = ctx._collect_secret_values()
            assert len(secrets) == 0
        finally:
            clear_execution_context()

    @pytest.mark.asyncio
    async def test_no_context_does_not_raise(self):
        from bifrost.integrations import integrations
        from bifrost._context import clear_execution_context

        clear_execution_context()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = _make_oauth_response()
        mock_client = AsyncMock()
        mock_client.engine_request = AsyncMock(return_value=mock_response)

        with patch("bifrost.integrations.get_client", return_value=mock_client):
            result = await integrations.get("TestIntegration")

        assert result is not None  # still returns data
