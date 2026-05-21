"""Unit tests for OAuth SSO configuration service."""

import logging
from unittest.mock import AsyncMock, patch

import pytest

from src.services.oauth_config_service import OAuthConfigService


@pytest.mark.asyncio
async def test_secret_decrypt_failure_log_omits_secret_material(caplog):
    """Decrypt failures should not log ciphertext, key names, or exception text."""
    service = OAuthConfigService(db=AsyncMock())
    service._get_config_value = AsyncMock(return_value="encrypted-secret-payload")  # type: ignore[method-assign]

    with (
        patch(
            "src.services.oauth_config_service.decrypt_secret",
            side_effect=RuntimeError("failed for super-secret-value"),
        ),
        caplog.at_level(logging.ERROR, logger="src.services.oauth_config_service"),
    ):
        result = await service._get_secret_value("oauth_client_secret")

    assert result is None
    log_text = caplog.text
    assert "RuntimeError" in log_text
    assert "oauth_client_secret" not in log_text
    assert "encrypted-secret-payload" not in log_text
    assert "super-secret-value" not in log_text
