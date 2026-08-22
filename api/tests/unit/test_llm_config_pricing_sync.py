"""LLM configuration must synchronize pricing for every selected model surface."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.contracts.llm import LLMConfigRequest
from src.routers.llm_config import set_llm_config
from src.services.llm_config_service import LLMProviderConfig, LLMTestResult


@pytest.mark.asyncio
async def test_config_save_syncs_default_tiers_and_internal_models() -> None:
    request = LLMConfigRequest(
        provider="openai",
        model="primary/model",
        api_key="secret",
        endpoint="https://openrouter.ai/api/v1",
        chat_fast_model="fast/model",
        chat_balanced_model="balanced/model",
        chat_pro_model="pro/model",
        summarization_model="summary/model",
        tuning_model="tuning/model",
        image_generation_model="image/model",
        video_generation_model="video/model",
    )
    db = AsyncMock()
    user = MagicMock(email="admin@example.com")
    service = MagicMock()
    service.save_config = AsyncMock()
    service.verify_completion = AsyncMock(
        return_value=LLMTestResult(success=True, message="ok")
    )
    service.sync_provider_pricing = AsyncMock(return_value=6)
    service.get_config = AsyncMock(
        return_value=LLMProviderConfig(
            provider="openai",
            model="primary/model",
            endpoint=request.endpoint,
            api_key_set=True,
        )
    )
    decrypted = MagicMock(api_key="provider-key")
    redis = AsyncMock()

    with (
        patch("src.routers.llm_config.LLMConfigService", return_value=service),
        patch(
            "src.services.llm.factory.get_llm_config",
            new=AsyncMock(return_value=decrypted),
        ),
        patch(
            "src.core.cache.get_shared_redis",
            new=AsyncMock(return_value=redis),
        ),
    ):
        response = await set_llm_config(request, db, user)

    selected = {
        "primary/model",
        "balanced/model",
        "fast/model",
        "pro/model",
        "summary/model",
        "tuning/model",
        "image/model",
        "video/model",
    }
    service.sync_provider_pricing.assert_awaited_once_with(
        provider="openrouter",
        models=selected,
        api_key="provider-key",
        endpoint="https://openrouter.ai/api/v1",
    )
    assert {call.args[-1] for call in redis.delete.await_args_list} == {
        f"ai_pricing:openrouter:{model}" for model in selected
    }
    assert response.api_key_set is True
