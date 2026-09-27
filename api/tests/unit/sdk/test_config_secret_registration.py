import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.sdk.context import ExecutionContext


class TestConfigGetRegistersSecret:
    def _make_ctx(self):
        return ExecutionContext(
            user_id="u1", email="e@e.com", name="Test",
            scope="org-123", organization=None,
            is_platform_admin=False, is_function_key=False,
            execution_id="exec-1",
        )

    @pytest.mark.asyncio
    async def test_secret_config_registered(self):
        from bifrost.config import config
        from bifrost._context import set_execution_context, clear_execution_context

        ctx = self._make_ctx()
        set_execution_context(ctx)
        try:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "value": "super-secret-api-key",
                "config_type": "secret",
            }
            mock_client = AsyncMock()
            mock_client.engine_request = AsyncMock(return_value=mock_response)

            with patch("bifrost.config.get_client", return_value=mock_client):
                result = await config.get("my_key")

            assert result == "super-secret-api-key"
            assert "super-secret-api-key" in ctx._collect_secret_values()
        finally:
            clear_execution_context()

    @pytest.mark.asyncio
    async def test_non_secret_not_registered(self):
        from bifrost.config import config
        from bifrost._context import set_execution_context, clear_execution_context

        ctx = self._make_ctx()
        set_execution_context(ctx)
        try:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "value": "https://api.example.com",
                "config_type": "string",
            }
            mock_client = AsyncMock()
            mock_client.engine_request = AsyncMock(return_value=mock_response)

            with patch("bifrost.config.get_client", return_value=mock_client):
                await config.get("my_url")

            assert ctx._collect_secret_values() == set()
        finally:
            clear_execution_context()

    @pytest.mark.asyncio
    async def test_no_context_does_not_raise(self):
        from bifrost.config import config
        from bifrost._context import clear_execution_context

        clear_execution_context()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"value": "secret-val", "config_type": "secret"}
        mock_client = AsyncMock()
        mock_client.engine_request = AsyncMock(return_value=mock_response)

        with patch("bifrost.config.get_client", return_value=mock_client):
            result = await config.get("my_key")

        assert result == "secret-val"  # still returns value
