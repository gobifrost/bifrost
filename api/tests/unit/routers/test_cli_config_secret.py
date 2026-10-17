"""Tests for secret decryption in CLI config/get endpoint."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.core.security import encrypt_secret, decrypt_secret
from src.core.principal import UserPrincipal
from src.services.authorization.context import (
    AuthorizationContext,
    Boundary,
    BoundaryKind,
    RoleGrant,
)
from src.services.authorization.enforce import Caller
from shared.builtin_roles import USER_ROLE_ID
from tests.helpers.authorization import admin_caller, platform_admin_grant


def _secrets_reader_caller() -> Caller:
    """A platform admin with explicit Secrets Reader at one org scope."""
    organization_id = uuid4()
    principal = UserPrincipal(
        user_id=uuid4(),
        email="secrets-reader@example.test",
        organization_id=organization_id,
    )
    return Caller(
        principal,
        AuthorizationContext(
            user_id=principal.user_id,
            home_organization_id=organization_id,
            base_role_id=USER_ROLE_ID,
            is_external=False,
            base_permissions=frozenset(),
            role_grants=(
                platform_admin_grant(),
                RoleGrant(
                    uuid4(),
                    frozenset({"secrets.read"}),
                    (Boundary(BoundaryKind.ORGANIZATION, organization_id),),
                ),
            ),
        ),
    )


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

        caller = _secrets_reader_caller()

        request = CLIConfigGetRequest(
            key="test_secret", scope=str(caller.principal.organization_id)
        )

        with patch("src.services.authorization.enforce.load_caller", new=AsyncMock(return_value=caller)), \
             patch("src.repositories.config.ConfigRepository", return_value=mock_resolver):
            result = await cli_get_config(request=request, current_user=caller.principal, db=AsyncMock())

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

        caller = _secrets_reader_caller()

        request = CLIConfigGetRequest(
            key="bad_secret", scope=str(caller.principal.organization_id)
        )

        with patch("src.services.authorization.enforce.load_caller", new=AsyncMock(return_value=caller)), \
             patch("src.repositories.config.ConfigRepository", return_value=mock_resolver):
            result = await cli_get_config(request=request, current_user=caller.principal, db=AsyncMock())

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

        caller = admin_caller()

        request = CLIConfigGetRequest(key="normal_key")

        with patch("src.services.authorization.enforce.load_caller", new=AsyncMock(return_value=caller)), \
             patch("src.repositories.config.ConfigRepository", return_value=mock_resolver):
            result = await cli_get_config(request=request, current_user=caller.principal, db=AsyncMock())

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

        caller = admin_caller()

        request = CLIConfigListRequest()

        with patch("src.services.authorization.enforce.load_caller", new=AsyncMock(return_value=caller)), \
             patch("src.repositories.config.ConfigRepository", return_value=mock_resolver):
            result = await cli_list_config(request=request, current_user=caller.principal, db=AsyncMock())

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

        caller = admin_caller()

        request = CLIConfigListRequest()

        with patch("src.services.authorization.enforce.load_caller", new=AsyncMock(return_value=caller)), \
             patch("src.repositories.config.ConfigRepository", return_value=mock_resolver):
            result = await cli_list_config(request=request, current_user=caller.principal, db=AsyncMock())

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
