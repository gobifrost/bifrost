"""
LLM Client Factory

Creates the appropriate LLM client based on reusable AI model profiles.
"""

import logging
from typing import Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.models.contracts.ai_models import AIModelAssignmentKey
from src.services.ai_model_service import AIModelService
from src.services.llm.base import BaseLLMClient, LLMConfig

logger = logging.getLogger(__name__)


# Default configuration values
DEFAULT_PROVIDER: Literal["openai", "anthropic", "google"] = "openai"
DEFAULT_OPENAI_MODEL = "gpt-4o"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-20250514"
DEFAULT_GOOGLE_MODEL = "gemini-2.5-flash"


async def get_llm_config(
    session: AsyncSession,
    *,
    profile_id: UUID | None = None,
    profile_name: str | None = None,
    assignment_key: AIModelAssignmentKey = "primary",
) -> LLMConfig:
    """
    Resolve LLM configuration from a model profile or assignment.

    Returns:
        LLMConfig object with all settings

    Raises:
        ValueError: If configuration is missing or invalid
    """
    try:
        return await AIModelService(session).resolve_config(
            profile_id=profile_id,
            profile_name=profile_name,
            assignment_key=assignment_key,
        )
    except ValueError:
        raise
    except Exception as e:
        logger.error(f"Failed to resolve LLM configuration: {e}")
        raise ValueError("Failed to resolve LLM configuration.") from e


async def get_llm_client(
    session: AsyncSession,
    *,
    profile_id: UUID | None = None,
    profile_name: str | None = None,
    assignment_key: AIModelAssignmentKey = "primary",
) -> BaseLLMClient:
    """
    Get an LLM client based on platform configuration.

    Args:
        session: Database session for reading configuration

    Returns:
        Provider-neutral Pydantic AI client.

    Raises:
        ValueError: If configuration is invalid or missing
    """
    config = await get_llm_config(
        session,
        profile_id=profile_id,
        profile_name=profile_name,
        assignment_key=assignment_key,
    )

    # Imported lazily so provider SDKs remain outside worker/scheduler import
    # closure until an LLM request is actually made.
    from src.services.llm.pydantic_client import PydanticAIClient

    return PydanticAIClient(config)


def create_llm_client(
    provider: Literal["openai", "anthropic", "google"],
    api_key: str,
    model: str | None = None,
    endpoint: str | None = None,
) -> BaseLLMClient:
    """
    Create an LLM client with explicit configuration.

    Use this for testing or when you need to override platform config.

    Args:
        provider: "openai", "anthropic", or "google"
        api_key: API key for the provider
        model: Model identifier (uses defaults if not provided)
        endpoint: Custom API endpoint URL
    Returns:
        Configured LLM client
    """
    if model is None:
        model = {
            "openai": DEFAULT_OPENAI_MODEL,
            "anthropic": DEFAULT_ANTHROPIC_MODEL,
            "google": DEFAULT_GOOGLE_MODEL,
        }[provider]

    config = LLMConfig(
        provider=provider,
        model=model,
        api_key=api_key,
        endpoint=endpoint,
    )

    from src.services.llm.pydantic_client import PydanticAIClient

    return PydanticAIClient(config)
