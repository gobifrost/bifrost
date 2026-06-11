"""
Embedding Client Factory

Creates embedding clients based on platform configuration.
Supports dedicated embedding config or fallback to LLM config.
"""

import base64
import logging

from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.models.orm import SystemConfig
from src.services.embeddings.base import (
    DEFAULT_EMBEDDING_MODEL,
    EMBEDDING_DIMENSIONS,
    BaseEmbeddingClient,
    EmbeddingConfig,
)

logger = logging.getLogger(__name__)


# SystemConfig keys for embedding configuration
EMBEDDING_CONFIG_CATEGORY = "llm"
EMBEDDING_CONFIG_KEY = "embedding_config"

# LLM config keys (for fallback)
LLM_CONFIG_CATEGORY = "llm"
LLM_CONFIG_KEY = "provider_config"


async def get_embedding_config(session: AsyncSession) -> EmbeddingConfig:
    """
    Get embedding configuration from system_configs table.

    Fallback order:
    1. Dedicated embedding_config (category=llm, key=embedding_config)
    2. LLM provider_config if provider is OpenAI (reuse the same key)
    3. Error if neither is available

    Returns:
        EmbeddingConfig with API key and model settings

    Raises:
        ValueError: If no embedding configuration is available
    """
    settings = get_settings()

    # Try dedicated embedding config first
    result = await session.execute(
        select(SystemConfig).where(
            SystemConfig.category == EMBEDDING_CONFIG_CATEGORY,
            SystemConfig.key == EMBEDDING_CONFIG_KEY,
            SystemConfig.organization_id.is_(None),  # Global config
        )
    )
    embedding_config = result.scalars().first()

    if embedding_config and embedding_config.value_json:
        config_data = embedding_config.value_json
        encrypted_api_key = config_data.get("encrypted_api_key")

        if encrypted_api_key:
            try:
                key_bytes = settings.secret_key.encode()[:32].ljust(32, b"0")
                fernet = Fernet(base64.urlsafe_b64encode(key_bytes))
                api_key = fernet.decrypt(encrypted_api_key.encode()).decode()

                return EmbeddingConfig(
                    api_key=api_key,
                    model=config_data.get("model", DEFAULT_EMBEDDING_MODEL),
                    dimensions=config_data.get("dimensions", EMBEDDING_DIMENSIONS),
                    endpoint=config_data.get("endpoint"),
                )
            except Exception as e:
                logger.error(f"Failed to decrypt embedding API key: {e}")
                # Fall through to LLM config

    # Fallback: try to use OpenAI key from LLM config
    result = await session.execute(
        select(SystemConfig).where(
            SystemConfig.category == LLM_CONFIG_CATEGORY,
            SystemConfig.key == LLM_CONFIG_KEY,
            SystemConfig.organization_id.is_(None),  # Global config
        )
    )
    llm_config = result.scalars().first()

    if llm_config and llm_config.value_json:
        config_data = llm_config.value_json
        provider = config_data.get("provider", "openai")
        llm_endpoint = config_data.get("endpoint")

        if provider != "openai":
            raise ValueError(
                "Knowledge store requires an OpenAI-compatible provider for embeddings. "
                "Either configure a dedicated embedding key in AI Configuration, "
                "or set your LLM provider to OpenAI (or an OpenAI-compatible endpoint)."
            )

        # Only fall back when the LLM is on stock OpenAI. For custom endpoints
        # (OpenRouter, Azure, Ollama, etc.) we don't know what the right
        # embedding model id is — `text-embedding-3-small` is OpenAI-only.
        # Force the user to configure embeddings explicitly.
        if llm_endpoint:
            raise ValueError(
                "LLM provider uses a custom endpoint; embeddings require a "
                "dedicated configuration. Please configure embeddings in "
                "System Settings > AI Configuration."
            )

        encrypted_api_key = config_data.get("encrypted_api_key")
        if not encrypted_api_key:
            raise ValueError(
                "No API key configured for embeddings. "
                "Please configure embedding settings in System Settings > AI Configuration."
            )

        try:
            key_bytes = settings.secret_key.encode()[:32].ljust(32, b"0")
            fernet = Fernet(base64.urlsafe_b64encode(key_bytes))
            api_key = fernet.decrypt(encrypted_api_key.encode()).decode()

            return EmbeddingConfig(
                api_key=api_key,
                model=DEFAULT_EMBEDDING_MODEL,
                dimensions=EMBEDDING_DIMENSIONS,
                endpoint=None,
            )
        except Exception as e:
            logger.error(f"Failed to decrypt LLM API key for embeddings: {e}")
            raise ValueError(
                "Failed to decrypt API key for embeddings. Configuration may be corrupted."
            ) from e

    raise ValueError(
        "No embedding configuration found. "
        "Please configure AI settings in System Settings > AI Configuration."
    )


async def get_embedding_client(session: AsyncSession) -> BaseEmbeddingClient:
    """
    Get an embedding client based on platform configuration.

    Args:
        session: Database session for reading configuration

    Returns:
        Configured embedding client (OpenAI)

    Raises:
        ValueError: If configuration is invalid or missing
    """
    config = await get_embedding_config(session)
    # Imported lazily so the openai SDK stays out of the worker/scheduler
    # import closure (tests/unit/test_import_hygiene.py).
    from src.services.embeddings.openai_client import OpenAIEmbeddingClient

    return OpenAIEmbeddingClient(config)
