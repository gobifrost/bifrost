from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.services.provider_catalog_service import ProviderCatalogService


@pytest.mark.asyncio
async def test_foundry_catalog_is_not_presented_as_deployment_names():
    client = SimpleNamespace(
        models=SimpleNamespace(
            list=AsyncMock(
                return_value=SimpleNamespace(
                    data=[SimpleNamespace(id="gpt-5.6-luna-2026-07-09")]
                )
            )
        )
    )
    with patch("openai.AsyncOpenAI", return_value=client):
        result = await ProviderCatalogService().list_openai(
            "sk-test",
            "https://example.openai.azure.com/openai/v1",
        )

    assert result.success is True
    assert result.models is None
    assert "deployment name manually" in result.message


@pytest.mark.asyncio
async def test_openai_catalog_forwards_opencode_go_identity_headers():
    client = SimpleNamespace(
        models=SimpleNamespace(
            list=AsyncMock(return_value=SimpleNamespace(data=[]))
        )
    )
    headers = {
        "User-Agent": "Bifrost/test",
        "x-opencode-session": "catalog-session",
    }
    with patch("openai.AsyncOpenAI", return_value=client):
        result = await ProviderCatalogService().list_openai(
            "sk-test",
            "https://opencode.ai/zen/go/v1",
            extra_headers=headers,
        )

    assert result.success is True
    assert client.models.list.await_args.kwargs["extra_headers"] == headers
