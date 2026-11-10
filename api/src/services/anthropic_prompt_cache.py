"""Adaptive Anthropic prompt caching for compatible provider endpoints."""

from collections.abc import Awaitable, Callable
import logging
from typing import TypeVar

from pydantic_ai.exceptions import ModelHTTPError
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError

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
    if error.status_code not in (400, 422) or not isinstance(error.body, dict):
        return False
    details = error.body.get("error", error.body.get("detail", error.body))
    if not isinstance(details, list):
        details = [details]
    for detail in details:
        if isinstance(detail, str):
            detail = {"message": detail}
        if not isinstance(detail, dict):
            continue
        message = detail.get("message", detail.get("msg", ""))
        if not isinstance(message, str):
            continue
        message = message.casefold()
        location = detail.get("param", detail.get("loc", detail.get("path", "")))
        explicit_field = "cache_control" in message or "cache_control" in str(location).casefold()
        rejection = any(term in message for term in (
            "unsupported", "not supported", "not allowed", "unknown", "unrecognized", "extra inputs", "extra fields",
        ))
        if explicit_field and rejection:
            return True
    return False


async def persist_prompt_cache_support(config: LLMConfig, supported: bool) -> None:
    """Observe only the resolved profile/revision without blocking caller writes."""
    if config.model_profile_updated_at is None or config.model_profile_id is None or config.provider_connection_id is None or config.provider_connection_updated_at is None:
        return
    try:
        from src.core.database import get_session_factory
        from src.models.orm.ai_models import AIModelProfile, AIProviderConnection

        async with get_session_factory()() as session:
            # Configuration discovery can hold this row in the caller's own
            # transaction. Bookkeeping must not wait for that caller to return.
            observed = await session.execute(
                select(AIModelProfile.id, AIModelProfile.anthropic_prompt_cache_supported)
                .where(
                    AIModelProfile.id == config.model_profile_id,
                    AIModelProfile.connection_id == config.provider_connection_id,
                    AIModelProfile.model == config.model,
                    AIModelProfile.updated_at == config.model_profile_updated_at,
                ).with_for_update(nowait=True)
            )
            profile = observed.one_or_none()
            if profile is None:
                return
            revision = await session.scalar(
                select(AIProviderConnection.updated_at).where(
                    AIProviderConnection.id == config.provider_connection_id
                )
            )
            if revision != config.provider_connection_updated_at:
                return
            if supported and profile.anthropic_prompt_cache_supported is not None:
                return  # An explicit rejection wins over concurrent success.
            await session.execute(
                update(AIModelProfile).where(AIModelProfile.id == profile.id).values(
                    anthropic_prompt_cache_supported=supported,
                    updated_at=AIModelProfile.updated_at,
                )
            )
            await session.commit()
    except DBAPIError as error:
        if getattr(error.orig, "sqlstate", None) == "55P03":
            logger.debug("Profile cache observation skipped while configuration is locked")
        else:
            logger.warning("Could not persist profile cache observation", exc_info=True)
    except Exception:
        logger.warning("Could not persist profile cache observation", exc_info=True)


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
        result = await send(without_prompt_cache_settings(initial_settings))
        config.anthropic_prompt_cache_supported = False
        await persist_prompt_cache_support(config, False)
        return result

    if attempted_cache and config.anthropic_prompt_cache_supported is None:
        config.anthropic_prompt_cache_supported = True
        await persist_prompt_cache_support(config, True)
    return result

