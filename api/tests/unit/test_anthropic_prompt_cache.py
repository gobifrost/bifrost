"""Anthropic requests carry prompt-cache settings (instructions, tool
definitions, automatic message breakpoint); other providers are untouched."""

from src.services.agent_runtime.model_factory import agent_model_settings
from src.services.llm.base import LLMConfig


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
