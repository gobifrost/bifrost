"""Adaptive Anthropic prompt caching for compatible provider endpoints."""

from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
import logging
from typing import TypeVar
from uuid import UUID

from pydantic_ai.exceptions import ModelHTTPError
from sqlalchemy import update

from src.services.llm.base import LLMConfig

logger = logging.getLogger(__name__)
ANTHROPIC_CACHE_SETTING_KEYS = frozenset(
    {
        "anthropic_cache",
        "anthropic_cache_instructions",
        "anthropic_cache_messages",
        "anthropic_cache_tool_definitions",
    }
)
T = TypeVar("T")


def without_prompt_cache_settings(settings: dict[str, object]) -> dict[str, object]:
    """Return settings with every Anthropic cache control removed."""
    return {key: value for key, value in settings.items() if key not in ANTHROPIC_CACHE_SETTING_KEYS}


def is_prompt_cache_rejection(error: ModelHTTPError) -> bool:
    """Recognize only explicit pre-generation cache-control validation errors."""
    return error.status_code in (400, 422) and "cache_control" in str(error.body).casefold()


async def persist_prompt_cache_support(connection_id: UUID, supported: bool) -> None:
    """Best-effort persistence; bookkeeping must never fail a model request."""
    try:
        from src.core.database import get_session_factory
        from src.models.orm.ai_models import AIProviderConnection

        session_factory = get_session_factory()
        async with session_factory() as session:
            statement = update(AIProviderConnection).where(AIProviderConnection.id == connection_id)
            if supported:
                # An explicit rejection wins over a concurrent successful probe.
                statement = statement.where(
                    AIProviderConnection.anthropic_prompt_cache_supported.is_(None)
                )
            statement = statement.values(
                anthropic_prompt_cache_supported=supported,
                updated_at=datetime.now(timezone.utc),
            )
            await session.execute(statement)
            await session.commit()
    except Exception:
        logger.warning(
            "Could not persist Anthropic prompt-cache capability for connection %s",
            connection_id,
            exc_info=True,
        )


async def request_with_prompt_cache_fallback(
    send: Callable[[dict[str, object]], Awaitable[T]],
    model_settings: dict[str, object],
    config: LLMConfig,
) -> T:
    """Send once with caching, retrying only an explicit cache rejection."""
    initial_settings = (
        without_prompt_cache_settings(model_settings)
        if config.anthropic_prompt_cache_supported is False
        else dict(model_settings)
    )
    attempted_cache = bool(ANTHROPIC_CACHE_SETTING_KEYS & initial_settings.keys())

    try:
        result = await send(initial_settings)
    except ModelHTTPError as error:
        if not attempted_cache or not is_prompt_cache_rejection(error):
            raise
        config.anthropic_prompt_cache_supported = False
        if config.provider_connection_id is not None:
            await persist_prompt_cache_support(config.provider_connection_id, False)
        return await send(without_prompt_cache_settings(initial_settings))

    if attempted_cache and config.anthropic_prompt_cache_supported is None:
        config.anthropic_prompt_cache_supported = True
        if config.provider_connection_id is not None:
            await persist_prompt_cache_support(config.provider_connection_id, True)
    return result

