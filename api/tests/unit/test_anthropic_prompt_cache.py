"""Anthropic prompt-cache settings and adaptive endpoint behavior."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.anthropic import AnthropicModel

from src.services.agent_runtime.model_factory import (
    agent_model_settings,
    anthropic_prompt_cache_settings,
    create_agent_model,
)
from src.services.anthropic_prompt_cache import (
    is_prompt_cache_rejection,
    request_with_prompt_cache_fallback,
)
from src.services.llm.base import LLMConfig
from src.services.llm.pydantic_client import PydanticAIClient


def _config(provider: str, endpoint: str | None = None) -> LLMConfig:
    return LLMConfig(provider=provider, model="m", api_key="k", endpoint=endpoint)


def test_anthropic_agent_settings_enable_prompt_cache():
    settings = agent_model_settings(_config("anthropic"), max_tokens=1000, session_id="s")
    assert settings["anthropic_cache"] is True
    assert settings["anthropic_cache_instructions"] is True
    assert settings["anthropic_cache_tool_definitions"] is True


def test_openai_agent_settings_untouched():
    settings = agent_model_settings(_config("openai"), max_tokens=1000, session_id="s")
    assert "anthropic_cache" not in settings
    assert settings["openai_store"] is False


def test_known_unsupported_endpoint_omits_cache_settings():
    config = _config("anthropic")
    config.anthropic_prompt_cache_supported = False

    settings = agent_model_settings(config, max_tokens=1000, session_id="s")

    assert not set(anthropic_prompt_cache_settings()) & settings.keys()

    direct_settings = PydanticAIClient(config)._model_settings(1000)
    assert not set(anthropic_prompt_cache_settings()) & direct_settings.keys()


def test_anthropic_messages_through_openrouter_keeps_cache_settings():
    config = _config("anthropic", "https://openrouter.ai/api")

    agent_settings = agent_model_settings(
        config, max_tokens=1000, session_id="cache-probe"
    )
    direct_settings = PydanticAIClient(config)._model_settings(1000)

    assert agent_settings["extra_body"] == {"session_id": "cache-probe"}
    assert agent_settings["anthropic_cache"] is True
    assert "anthropic_cache" in direct_settings


@pytest.mark.asyncio
async def test_native_anthropic_model_uses_adaptive_request_path():
    calls = []

    async def fake_create(
        self, messages, stream, model_settings, model_request_parameters
    ):
        calls.append(model_settings)
        if len(calls) == 1:
            raise ModelHTTPError(
                400,
                "claude",
                {"error": {"message": "cache_control unsupported"}},
            )
        return "ok"

    config = _config("anthropic")
    with patch.object(AnthropicModel, "_messages_create", fake_create):
        model = create_agent_model(config)
        result = await model._messages_create(
            [],
            False,
            anthropic_prompt_cache_settings(),
            ModelRequestParameters(),
        )

    assert result == "ok"
    assert len(calls) == 2
    assert "anthropic_cache" in calls[0]
    assert "anthropic_cache" not in calls[1]


@pytest.mark.parametrize("status_code", [400, 422])
def test_cache_control_validation_error_is_retryable(status_code):
    error = ModelHTTPError(
        status_code,
        "claude",
        {"error": {"message": "cache_control is not supported"}},
    )
    assert is_prompt_cache_rejection(error) is True


@pytest.mark.parametrize(
    "error",
    [
        ModelHTTPError(400, "claude", {"error": {"message": "invalid model"}}),
        ModelHTTPError(401, "claude", {"error": {"message": "cache_control denied"}}),
        ModelHTTPError(429, "claude", {"error": {"message": "cache_control limited"}}),
        ModelHTTPError(503, "claude", {"error": {"message": "cache_control unavailable"}}),
    ],
)
def test_ambiguous_or_non_validation_errors_are_not_cache_rejections(error):
    assert is_prompt_cache_rejection(error) is False


@pytest.mark.asyncio
async def test_unknown_endpoint_falls_back_once_and_remembers_unsupported():
    connection_id = uuid4()
    config = LLMConfig(
        provider="anthropic",
        model="claude",
        api_key="key",
        provider_connection_id=connection_id,
    )
    send = AsyncMock(
        side_effect=[
            ModelHTTPError(400, "claude", {"error": {"message": "cache_control unsupported"}}),
            "ok",
        ]
    )

    with patch(
        "src.services.anthropic_prompt_cache.persist_prompt_cache_support",
        new=AsyncMock(),
    ) as persist:
        result = await request_with_prompt_cache_fallback(
            send, {**anthropic_prompt_cache_settings(), "max_tokens": 10}, config
        )

    assert result == "ok"
    assert send.await_count == 2
    assert send.await_args_list[0].args[0]["anthropic_cache"] is True
    assert not set(anthropic_prompt_cache_settings()) & send.await_args_list[1].args[0].keys()
    assert config.anthropic_prompt_cache_supported is False
    persist.assert_awaited_once_with(connection_id, False)


@pytest.mark.asyncio
async def test_unknown_endpoint_success_remembers_supported():
    connection_id = uuid4()
    config = LLMConfig(
        provider="anthropic",
        model="claude",
        api_key="key",
        provider_connection_id=connection_id,
    )
    send = AsyncMock(return_value="ok")

    with patch(
        "src.services.anthropic_prompt_cache.persist_prompt_cache_support",
        new=AsyncMock(),
    ) as persist:
        result = await request_with_prompt_cache_fallback(
            send, anthropic_prompt_cache_settings(), config
        )

    assert result == "ok"
    assert config.anthropic_prompt_cache_supported is True
    persist.assert_awaited_once_with(connection_id, True)


@pytest.mark.asyncio
async def test_generic_validation_error_is_not_retried_or_persisted():
    config = LLMConfig(
        provider="anthropic",
        model="claude",
        api_key="key",
        provider_connection_id=uuid4(),
    )
    error = ModelHTTPError(400, "claude", {"error": {"message": "invalid model"}})
    send = AsyncMock(side_effect=error)

    with patch(
        "src.services.anthropic_prompt_cache.persist_prompt_cache_support",
        new=AsyncMock(),
    ) as persist:
        with pytest.raises(ModelHTTPError) as raised:
            await request_with_prompt_cache_fallback(
                send, anthropic_prompt_cache_settings(), config
            )

    assert raised.value is error
    assert send.await_count == 1
    persist.assert_not_awaited()
