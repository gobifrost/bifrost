"""Provider-neutral Pydantic AI model construction.

Provider SDK imports stay inside the selected branch. This preserves the worker and
scheduler import boundary while giving every agent surface one model abstraction.
"""

from urllib.parse import urlparse

from pydantic_ai.models import Model
from pydantic_ai.usage import RequestUsage

from src.services.llm.base import LLMConfig


def _is_openrouter_endpoint(endpoint: str | None) -> bool:
    """Return whether an OpenAI-compatible endpoint is OpenRouter itself."""

    if not endpoint:
        return False
    hostname = urlparse(endpoint).hostname
    return hostname == "openrouter.ai" or bool(
        hostname and hostname.endswith(".openrouter.ai")
    )


def _openrouter_usage(response: object, fallback: RequestUsage) -> RequestUsage:
    """Map counts OpenRouter returned even when Pydantic's price lookup misses."""

    provider_usage = getattr(response, "usage", None)
    if provider_usage is None:
        return fallback

    prompt_details = getattr(provider_usage, "prompt_tokens_details", None)
    cached_tokens = getattr(prompt_details, "cached_tokens", 0) or 0
    return RequestUsage(
        input_tokens=provider_usage.prompt_tokens,
        output_tokens=provider_usage.completion_tokens,
        cache_read_tokens=cached_tokens,
        cache_write_tokens=fallback.cache_write_tokens,
        input_audio_tokens=fallback.input_audio_tokens,
        cache_audio_read_tokens=fallback.cache_audio_read_tokens,
        output_audio_tokens=fallback.output_audio_tokens,
        details=fallback.details,
    )


def create_agent_model(config: LLMConfig, *, model: str | None = None) -> Model:
    """Build the configured Pydantic AI model.

    Bifrost's public configuration remains provider-neutral. Known compatible
    services use their native Pydantic adapter internally so provider-specific
    response metadata, especially usage accounting, is preserved. Other custom
    OpenAI-compatible endpoints continue through the generic Chat Completions
    adapter.
    """

    model_name = model or config.model

    if config.provider == "openai":
        if _is_openrouter_endpoint(config.endpoint):
            from pydantic_ai.models.openrouter import OpenRouterModel
            from pydantic_ai.providers.openrouter import OpenRouterProvider

            class BifrostOpenRouterModel(OpenRouterModel):
                def _map_usage(self, response: object) -> RequestUsage:
                    return _openrouter_usage(response, super()._map_usage(response))  # type: ignore[arg-type]

            return BifrostOpenRouterModel(
                model_name,
                provider=OpenRouterProvider(api_key=config.api_key),
                settings={"openrouter_usage": {"include": True}},
            )

        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider

        provider = OpenAIProvider(
            api_key=config.api_key,
            base_url=config.endpoint,
        )
        return OpenAIChatModel(model_name, provider=provider)

    if config.provider == "anthropic":
        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.providers.anthropic import AnthropicProvider

        provider = AnthropicProvider(
            api_key=config.api_key,
            base_url=config.endpoint,
        )
        return AnthropicModel(model_name, provider=provider)

    if config.provider == "google":
        from pydantic_ai.models.google import GoogleModel
        from pydantic_ai.providers.google import GoogleProvider

        provider = GoogleProvider(
            api_key=config.api_key,
            base_url=config.endpoint,
        )
        return GoogleModel(model_name, provider=provider)

    raise ValueError(f"Unknown LLM provider: {config.provider}")
