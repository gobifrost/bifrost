"""Embedding client factory backed by the AI embedding configuration."""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from src.services.embeddings.base import (
    BaseEmbeddingClient,
    EmbeddingConfig,
)
from src.services.ai_model_service import AIModelService

logger = logging.getLogger(__name__)


async def get_embedding_config(session: AsyncSession) -> EmbeddingConfig:
    """
    Get the explicit embedding configuration.

    Returns:
        EmbeddingConfig with API key and model settings

    Raises:
        ValueError: If no embedding configuration is available
    """
    return await AIModelService(session).resolve_embedding_config()


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
