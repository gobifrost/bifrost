"""Tests for secret decryption in CLI config/get endpoint."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.security import encrypt_secret, decrypt_secret


class TestConfigGetDecryptsSecrets:
    """Verify that cli_get_config decrypts secret-type config values."""

    @pytest.mark.asyncio
    async def test_get_config_returns_decrypted_secret(self):
        """config/get should return plaintext for secret-type values, not encrypted blobs."""
        from src.routers.cli import cli_get_config
        from src.models.contracts.cli import CLIConfigGetRequest, CLIConfigValue

        plaintext = "my_api_key_12345"
        encrypted = encrypt_secret(plaintext)

        mock_resolver = AsyncMock()
        mock_resolver.merged_for_sdk = AsyncMock(return_value={
            "test_secret": {"value": encrypted, "type": "secret"},
        })

        mock_user = MagicMock()
        mock_user.user_id = "test-user-id"
        mock_user.email = "test@example.com"

        request = CLIConfigGetRequest(key="test_secret")

        with patch("src.routers.cli._resolve_sdk_org_id", new_callable=AsyncMock, return_value="11111111-1111-1111-1111-111111111111"), \
             patch("src.repositories.config.ConfigRepository", return_value=mock_resolver):
            result = await cli_get_config(request=request, current_user=mock_user, db=AsyncMock())

        assert isinstance(result, CLIConfigValue)
        assert result.value == plaintext, (
            f"Expected decrypted plaintext '{plaintext}', got '{result.value}'"
        )
        assert result.config_type == "secret"

    @pytest.mark.asyncio
    async def test_get_config_returns_none_for_corrupt_secret(self):
        """config/get should return None value if secret decryption fails."""
        from src.routers.cli import cli_get_config
        from src.models.contracts.cli import CLIConfigGetRequest, CLIConfigValue

        mock_resolver = AsyncMock()
        mock_resolver.merged_for_sdk = AsyncMock(return_value={
            "bad_secret": {"value": "not-valid-encrypted-data", "type": "secret"},
        })

        mock_user = MagicMock()
        mock_user.user_id = "test-user-id"

        request = CLIConfigGetRequest(key="bad_secret")

        with patch("src.routers.cli._resolve_sdk_org_id", new_callable=AsyncMock, return_value="11111111-1111-1111-1111-111111111111"), \
             patch("src.repositories.config.ConfigRepository", return_value=mock_resolver):
            result = await cli_get_config(request=request, current_user=mock_user, db=AsyncMock())

        assert isinstance(result, CLIConfigValue)
        assert result.value is None, "Corrupt secret should decrypt to None, not raise"

    @pytest.mark.asyncio
    async def test_get_config_nonsecret_not_decrypted(self):
        """config/get should not attempt decryption on non-secret types."""
        from src.routers.cli import cli_get_config
        from src.models.contracts.cli import CLIConfigGetRequest, CLIConfigValue

        plain_value = "just_a_string"

        mock_resolver = AsyncMock()
        mock_resolver.merged_for_sdk = AsyncMock(return_value={
            "normal_key": {"value": plain_value, "type": "string"},
        })

        mock_user = MagicMock()
        mock_user.user_id = "test-user-id"

        request = CLIConfigGetRequest(key="normal_key")

        with patch("src.routers.cli._resolve_sdk_org_id", new_callable=AsyncMock, return_value="11111111-1111-1111-1111-111111111111"), \
             patch("src.repositories.config.ConfigRepository", return_value=mock_resolver):
            result = await cli_get_config(request=request, current_user=mock_user, db=AsyncMock())

        assert isinstance(result, CLIConfigValue)
        assert result.value == plain_value
        assert result.config_type == "string"


class TestConfigListMasksSecrets:
    """Verify that cli_list_config always masks secret values with [SECRET]."""

    @pytest.mark.asyncio
    async def test_list_config_masks_secret_values(self):
        """config/list should return '[SECRET]' for secret-type values, never the encrypted ciphertext."""
        from src.routers.cli import cli_list_config
        from src.models.contracts.cli import CLIConfigListRequest
        from src.core.security import encrypt_secret

        encrypted = encrypt_secret("real_api_key_value")

        mock_resolver = AsyncMock()
        mock_resolver.merged_for_sdk = AsyncMock(return_value={
            "normal_key": {"value": "normal_value", "type": "string"},
            "secret_key": {"value": encrypted, "type": "secret"},
        })

        mock_user = MagicMock()
        mock_user.user_id = "test-user-id"

        request = CLIConfigListRequest()

        with patch("src.routers.cli._resolve_sdk_org_id", new_callable=AsyncMock, return_value="11111111-1111-1111-1111-111111111111"), \
             patch("src.repositories.config.ConfigRepository", return_value=mock_resolver):
            result = await cli_list_config(request=request, current_user=mock_user, db=AsyncMock())

        assert result["normal_key"] == "normal_value"
        assert result["secret_key"] == "[SECRET]", (
            f"Secret should be masked as '[SECRET]', got '{result['secret_key']}'"
        )
        # Ensure encrypted ciphertext is NOT returned
        assert result["secret_key"] != encrypted

    @pytest.mark.asyncio
    async def test_list_config_masks_empty_secret(self):
        """config/list should return '[SECRET]' even for empty/null secret values."""
        from src.routers.cli import cli_list_config
        from src.models.contracts.cli import CLIConfigListRequest

        mock_resolver = AsyncMock()
        mock_resolver.merged_for_sdk = AsyncMock(return_value={
            "empty_secret": {"value": None, "type": "secret"},
        })

        mock_user = MagicMock()
        mock_user.user_id = "test-user-id"

        request = CLIConfigListRequest()

        with patch("src.routers.cli._resolve_sdk_org_id", new_callable=AsyncMock, return_value="11111111-1111-1111-1111-111111111111"), \
             patch("src.repositories.config.ConfigRepository", return_value=mock_resolver):
            result = await cli_list_config(request=request, current_user=mock_user, db=AsyncMock())

        assert result["empty_secret"] == "[SECRET]"


class TestEncryptDecryptRoundtrip:
    """Verify encrypt/decrypt are inverses — the foundational guarantee."""

    def test_roundtrip(self):
        """encrypt then decrypt should return the original value."""
        original = "super_secret_api_key_!@#$%"
        encrypted = encrypt_secret(original)
        assert encrypted != original, "encrypt_secret should not return plaintext"
        decrypted = decrypt_secret(encrypted)
        assert decrypted == original
