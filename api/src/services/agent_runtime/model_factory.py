"""Provider-neutral Pydantic AI model construction.

Provider SDK imports stay inside the selected branch. This preserves the worker and
scheduler import boundary while giving every agent surface one model abstraction.
"""

from decimal import Decimal, InvalidOperation

from pydantic_ai.models import Model
from pydantic_ai.usage import RequestUsage

from src.services.llm.base import LLMConfig, request_max_tokens
from src.services.model_pricing import is_openrouter_endpoint


def provider_name_for_config(config: LLMConfig) -> str:
    """Return the provider that actually served and billed the request."""

    return "openrouter" if is_openrouter_endpoint(config.endpoint) else config.provider


def agent_model_settings(
    config: LLMConfig,
    *,
    max_tokens: int | None,
    session_id: str,
) -> dict[str, object]:
    """Build per-run settings, including OpenRouter sticky cache routing."""

    settings: dict[str, object] = {}
    resolved_max_tokens = request_max_tokens(config, max_tokens)
    if resolved_max_tokens is not None:
        settings["max_tokens"] = resolved_max_tokens
    if is_openrouter_endpoint(config.endpoint):
        settings["extra_body"] = {"session_id": session_id[:256]}
    return settings


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


def _openrouter_cost(response: object) -> Decimal | None:
    """Extract exact OpenRouter cost from completion or streamed usage."""

    provider_usage = getattr(response, "usage", None)
    raw = getattr(provider_usage, "cost", None)
    if raw is None:
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        return None


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
        if is_openrouter_endpoint(config.endpoint):
            from pydantic_ai.models.openrouter import (
                OpenRouterModel,
                OpenRouterStreamedResponse,
            )
            from pydantic_ai.providers.openrouter import OpenRouterProvider

            class BifrostOpenRouterStreamedResponse(OpenRouterStreamedResponse):
                def _map_usage(self, response: object) -> RequestUsage:
                    if (cost := _openrouter_cost(response)) is not None:
                        self.provider_details = {
                            **(self.provider_details or {}),
                            "cost": cost,
                        }
                    return _openrouter_usage(
                        response,
                        super()._map_usage(response),  # type: ignore[arg-type]
                    )

            class BifrostOpenRouterModel(OpenRouterModel):
                def _map_usage(self, response: object) -> RequestUsage:
                    return _openrouter_usage(response, super()._map_usage(response))  # type: ignore[arg-type]

                @property
                def _streamed_response_cls(self):
                    return BifrostOpenRouterStreamedResponse

            settings: dict[str, object] = {"openrouter_usage": {"include": True}}
            if model_name.startswith("anthropic/"):
                settings.update(
                    {
                        "openrouter_cache_instructions": True,
                        "openrouter_cache_messages": True,
                        "openrouter_cache_tool_definitions": True,
                    }
                )
            elif model_name.startswith("google/"):
                settings.update(
                    {
                        "openrouter_cache_instructions": True,
                        "openrouter_cache_messages": True,
                    }
                )
            return BifrostOpenRouterModel(
                model_name,
                provider=OpenRouterProvider(api_key=config.api_key),
                settings=settings,
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
