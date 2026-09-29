"""Reasoning choices reach each adapter verbatim, and usage records them."""

import pytest
from pydantic_ai.usage import RequestUsage

from src.services.agent_runtime import reasoning_tokens
from src.services.agent_runtime.model_factory import (
    agent_model_settings,
    provider_name_for_config,
    reasoning_settings,
)
from src.services.llm.base import LLMConfig


def _config(**overrides) -> LLMConfig:
    values = {"provider": "openai", "model": "m", "api_key": "k"}
    values.update(overrides)
    return LLMConfig(**values)


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        # OpenRouter keeps levels Pydantic AI's unified setting would round.
        (
            _config(endpoint="https://openrouter.ai/api/v1", reasoning_effort="xhigh"),
            {"openrouter_reasoning": {"effort": "xhigh"}},
        ),
        (_config(provider="anthropic", reasoning_effort="max"), {"anthropic_effort": "max"}),
        (_config(provider="google", reasoning_effort="low"), {"thinking": "low"}),
        (_config(reasoning_effort="high"), {"openai_reasoning_effort": "high"}),
        # A community OpenAI-compatible endpoint uses the OpenAI field.
        (
            _config(endpoint="https://api.deepseek.com", reasoning_effort="max"),
            {"openai_reasoning_effort": "max"},
        ),
        # Toggles use the unified setting; each adapter maps it.
        (_config(provider="anthropic", reasoning_effort="on"), {"thinking": True}),
        (_config(reasoning_effort="off"), {"thinking": False}),
        (_config(), {}),
    ],
)
def test_reasoning_choice_maps_to_the_adapter_field(config, expected) -> None:
    assert reasoning_settings(config, max_tokens=None) == expected


@pytest.mark.parametrize(("cap", "budget"), [(4096, 4095), (64000, 10_000), (800, 1024)])
def test_anthropic_thinking_budget_fits_under_the_output_cap(cap, budget) -> None:
    # Found live: Anthropic rejects budget_tokens >= max_tokens, and the
    # default "on" budget is 10k.
    settings = reasoning_settings(
        _config(provider="anthropic", reasoning_effort="on"), max_tokens=cap
    )
    assert settings == {"anthropic_thinking": {"type": "enabled", "budget_tokens": budget}}


def test_agent_settings_include_the_reasoning_choice() -> None:
    settings = agent_model_settings(
        _config(provider="anthropic", reasoning_effort="high"),
        max_tokens=None,
        session_id="run-1",
    )
    assert settings["anthropic_effort"] == "high"


def test_community_providers_bill_under_their_catalog_id() -> None:
    community = _config(endpoint="https://api.fireworks.ai/inference/v1", catalog_provider_id="fireworks-ai")
    native = _config(endpoint="https://openrouter.ai/api/v1", catalog_provider_id="openrouter")
    assert provider_name_for_config(community) == "fireworks-ai"
    assert provider_name_for_config(native) == "openrouter"
    assert provider_name_for_config(_config(provider="anthropic")) == "anthropic"


@pytest.mark.parametrize("key", ["reasoning_tokens", "thinking_tokens", "thoughts_tokens"])
def test_reasoning_tokens_are_read_under_every_adapter_name(key) -> None:
    usage = RequestUsage(output_tokens=900, details={key: 640})
    assert reasoning_tokens(usage) == 640


def test_no_reported_reasoning_is_zero() -> None:
    assert reasoning_tokens(RequestUsage(output_tokens=10)) == 0
