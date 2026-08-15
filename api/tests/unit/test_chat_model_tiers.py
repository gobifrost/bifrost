from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.routers.chat import get_model_tiers
from src.services.llm_config_service import LLMProviderConfig


@pytest.mark.asyncio
async def test_model_tiers_expose_labels_without_provider_model_ids() -> None:
    config = LLMProviderConfig(
        provider="openai",
        model="provider-balanced-model",
        chat_fast_label="Quick",
        chat_fast_model="provider-fast-model",
        chat_balanced_label="Everyday",
        chat_pro_label="Deep",
        chat_pro_model="provider-pro-model",
    )
    with patch(
        "src.services.llm_config_service.LLMConfigService.get_config",
        new=AsyncMock(return_value=config),
    ):
        response = await get_model_tiers(MagicMock(), MagicMock())

    assert response.model_dump() == {
        "tiers": [
            {"id": "fast", "label": "Quick"},
            {"id": "balanced", "label": "Everyday"},
            {"id": "pro", "label": "Deep"},
        ],
        "default_tier": "balanced",
    }
    assert "provider-" not in response.model_dump_json()


@pytest.mark.asyncio
async def test_disabled_optional_model_tiers_are_hidden() -> None:
    config = LLMProviderConfig(provider="openai", model="balanced-model")
    with patch(
        "src.services.llm_config_service.LLMConfigService.get_config",
        new=AsyncMock(return_value=config),
    ):
        response = await get_model_tiers(MagicMock(), MagicMock())

    assert [tier.id for tier in response.tiers] == ["balanced"]
