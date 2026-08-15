"""Provider-neutral Pydantic AI model construction.

Provider SDK imports stay inside the selected branch. This preserves the worker and
scheduler import boundary while giving every agent surface one model abstraction.
"""

from pydantic_ai.models import Model

from src.services.llm.base import LLMConfig


def create_agent_model(config: LLMConfig, *, model: str | None = None) -> Model:
    """Build the configured Pydantic AI model.

    OpenAI-compatible endpoints (including OpenRouter) deliberately use the Chat
    Completions adapter because that is the compatibility contract Bifrost exposes
    today. Native OpenAI can move to Responses independently without changing this
    runtime boundary.
    """

    model_name = model or config.model

    if config.provider == "openai":
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
