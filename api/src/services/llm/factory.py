"""
LLM Client Factory

Creates the appropriate LLM client based on platform configuration.
Follows the same pattern as GitHub integration for SystemConfig storage.
"""

import base64
import logging
from typing import Literal

from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.models.orm import SystemConfig
from src.services.llm.base import BaseLLMClient, LLMConfig

logger = logging.getLogger(__name__)


# Default configuration values
DEFAULT_PROVIDER: Literal["openai", "anthropic"] = "openai"
DEFAULT_OPENAI_MODEL = "gpt-4o"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-20250514"
DEFAULT_MAX_TOKENS = 16384

# SystemConfig keys (follows GitHub integration pattern)
LLM_CONFIG_CATEGORY = "llm"
LLM_CONFIG_KEY = "provider_config"

async def get_llm_config(session: AsyncSession) -> LLMConfig:
    """
    Get LLM configuration from system_configs table.

    Configuration is stored as a single JSON object following the GitHub integration pattern:
    - category: "llm"
    - key: "provider_config"
    - value_json: {
        "provider": "openai" | "anthropic",
        "model": "gpt-4o" | "claude-sonnet-4-20250514",
        "encrypted_api_key": "<fernet-encrypted-key>",
        "endpoint": null,  # For custom OpenAI-compatible providers
        "max_tokens": 4096
      }

    Returns:
        LLMConfig object with all settings

    Raises:
        ValueError: If configuration is missing or invalid
    """
    settings = get_settings()

    # Query consolidated LLM config (follows GitHub pattern)
    result = await session.execute(
        select(SystemConfig).where(
            SystemConfig.category == LLM_CONFIG_CATEGORY,
            SystemConfig.key == LLM_CONFIG_KEY,
            SystemConfig.organization_id.is_(None),  # Global config
        )
    )
    config = result.scalars().first()

    if not config or not config.value_json:
        raise ValueError(
            "LLM provider not configured. "
            "Please configure LLM settings in System Settings > AI Configuration."
        )

    config_data = config.value_json

    # Determine provider (map legacy "custom" to "openai")
    provider_str = config_data.get("provider", DEFAULT_PROVIDER)
    if provider_str == "custom":
        provider_str = "openai"
    if provider_str not in ("openai", "anthropic"):
        logger.warning(f"Invalid provider '{provider_str}', defaulting to {DEFAULT_PROVIDER}")
        provider_str = DEFAULT_PROVIDER
    provider: Literal["openai", "anthropic"] = provider_str  # type: ignore[assignment]

    # Get model based on provider
    default_model = DEFAULT_OPENAI_MODEL if provider == "openai" else DEFAULT_ANTHROPIC_MODEL
    model = config_data.get("model", default_model)

    # Decrypt API key (same pattern as GitHub token encryption)
    encrypted_api_key = config_data.get("encrypted_api_key")
    if not encrypted_api_key:
        raise ValueError(
            f"No API key configured for LLM provider '{provider}'. "
            "Please configure the API key in System Settings > AI Configuration."
        )

    try:
        key_bytes = settings.secret_key.encode()[:32].ljust(32, b"0")
        fernet = Fernet(base64.urlsafe_b64encode(key_bytes))
        api_key = fernet.decrypt(encrypted_api_key.encode()).decode()
    except Exception as e:
        logger.error(f"Failed to decrypt LLM API key: {e}")
        raise ValueError("Failed to decrypt LLM API key. Configuration may be corrupted.") from e

    # Get optional parameters with defaults
    max_tokens = config_data.get("max_tokens", DEFAULT_MAX_TOKENS)
    endpoint = config_data.get("endpoint") or None

    return LLMConfig(
        provider=provider,
        model=model,
        api_key=api_key,
        endpoint=endpoint,
        max_tokens=max_tokens,
    )


async def get_llm_client(session: AsyncSession) -> BaseLLMClient:
    """
    Get an LLM client based on platform configuration.

    Args:
        session: Database session for reading configuration

    Returns:
        Configured LLM client (OpenAI or Anthropic)

    Raises:
        ValueError: If configuration is invalid or missing
    """
    config = await get_llm_config(session)

    if config.provider == "openai":
        # Imported lazily so the openai SDK stays out of the worker/scheduler
        # import closure (tests/unit/test_import_hygiene.py).
        from src.services.llm.openai_client import OpenAIClient

        return OpenAIClient(config)
    elif config.provider == "anthropic":
        # Imported lazily so the anthropic SDK stays out of the worker/scheduler
        # import closure (tests/unit/test_import_hygiene.py).
        from src.services.llm.anthropic_client import AnthropicClient

        return AnthropicClient(config)
    else:
        # This shouldn't happen due to validation in get_llm_config
        raise ValueError(f"Unknown LLM provider: {config.provider}")


def create_llm_client(
    provider: Literal["openai", "anthropic"],
    api_key: str,
    model: str | None = None,
    endpoint: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> BaseLLMClient:
    """
    Create an LLM client with explicit configuration.

    Use this for testing or when you need to override platform config.

    Args:
        provider: "openai" or "anthropic"
        api_key: API key for the provider
        model: Model identifier (uses defaults if not provided)
        endpoint: Custom API endpoint URL
        max_tokens: Maximum tokens for completion

    Returns:
        Configured LLM client
    """
    if model is None:
        model = DEFAULT_OPENAI_MODEL if provider == "openai" else DEFAULT_ANTHROPIC_MODEL

    config = LLMConfig(
        provider=provider,
        model=model,
        api_key=api_key,
        endpoint=endpoint,
        max_tokens=max_tokens,
    )

    if provider == "openai":
        # Imported lazily so the openai SDK stays out of the worker/scheduler
        # import closure (tests/unit/test_import_hygiene.py).
        from src.services.llm.openai_client import OpenAIClient

        return OpenAIClient(config)
    elif provider == "anthropic":
        # Imported lazily so the anthropic SDK stays out of the worker/scheduler
        # import closure (tests/unit/test_import_hygiene.py).
        from src.services.llm.anthropic_client import AnthropicClient

        return AnthropicClient(config)
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")
