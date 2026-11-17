"""Invalid generated source cannot reach workspace storage."""

import traceback
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.routers.integrations import GenerateSDKRequest, generate_sdk


@pytest.mark.asyncio
async def test_syntax_failure_returns_sanitized_error_without_writing():
    template = MagicMock()
    template.render.return_value = 'secret-vendor-token = ('
    ctx = MagicMock()
    with (
        patch("src.routers.integrations._global_definition_writer", new=AsyncMock()),
        patch("src.routers.integrations.IntegrationsRepository") as repository,
        patch("src.services.sdk_generator.load_spec_from_url", return_value={"info": {"title": "Example"}}),
        patch("src.services.sdk_generator.Environment.get_template", return_value=template),
        patch("src.services.file_storage.FileStorageService") as storage,
    ):
        repository.return_value.get_integration_by_id = AsyncMock(
            return_value=SimpleNamespace(name="Example"))
        repository.return_value.update_integration = AsyncMock()
        with pytest.raises(HTTPException) as error:
            await generate_sdk(uuid4(), GenerateSDKRequest(
                spec_url="https://example.test/spec.json", auth_type="bearer"), ctx, MagicMock())
    assert error.value.status_code == 400
    assert error.value.detail == "SDK generation failed: Generated SDK contains invalid Python syntax"
    assert "secret-vendor-token" not in "".join(traceback.format_exception(error.value))
    storage.assert_not_called()
