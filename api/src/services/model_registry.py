"""
Model Registry Service

Caches model display names for consistent pricing and reporting.
Maps versioned model IDs (e.g., "claude-opus-4-5-20251101") to display names
(e.g., "Claude Opus 4.5").

Cache is populated when testing LLM connection in settings (which fetches the
model list anyway). On cache miss, the model_id is returned as-is - no provider
API call is made during AI usage recording to avoid latency spikes.

Redis Key Structure:
- model_registry:{provider} - Cached model ID -> display name mapping (TTL: 24 hours)
"""

import json
import logging

import httpx
import redis.asyncio as redis

from src.core.log_safety import log_safe

logger = logging.getLogger(__name__)

# Redis key prefix and TTL
MODEL_REGISTRY_KEY_PREFIX = "model_registry:"
MODEL_REGISTRY_TTL = 86400  # 24 hours


async def get_display_name(
    redis_client: redis.Redis,
    provider: str,
    model_id: str,
    api_key: str | None = None,
) -> str:
    """
    Get display name for a model ID.

    Checks Redis cache first. On cache miss, if an API key is available,
    fetches display names from the provider API and populates the cache.
    Falls back to normalize_model_name() only if no API key is available.

    Args:
        redis_client: Redis connection
        provider: LLM provider ("openai", "anthropic", or "google")
        model_id: Versioned model ID from API response
        api_key: Provider API key (used to refresh cache on miss)

    Returns:
        Display name (e.g., "Claude 3.5 Haiku") if available,
        otherwise normalized model name with date stripped as fallback
    """
    cache_key = f"{MODEL_REGISTRY_KEY_PREFIX}{provider}"

    # Check cache first
    try:
        cached = await redis_client.get(cache_key)
        if cached:
            mapping = json.loads(cached)
            if model_id in mapping:
                return mapping[model_id]
    except Exception as e:
        logger.warning(f"Redis cache read failed for model registry: {e}")

    # Cache miss - try to refresh from provider API if we have an API key
    if api_key:
        try:
            mapping = await refresh_model_registry(redis_client, provider, api_key)
            if model_id in mapping:
                return mapping[model_id]
        except Exception as e:
            logger.warning(f"Failed to refresh model registry from API: {e}")

    # Last resort fallback - normalize the model ID by stripping date suffixes
    return normalize_model_name(model_id)


async def cache_model_mapping(
    redis_client: redis.Redis,
    provider: str,
    mapping: dict[str, str],
) -> None:
    """
    Cache a model ID -> display name mapping for a provider.

    Called when testing LLM connection (which already fetches the model list).

    Args:
        redis_client: Redis connection
        provider: LLM provider ("openai", "anthropic", or "google")
        mapping: Dictionary mapping model IDs to display names
    """
    if not mapping:
        return

    cache_key = f"{MODEL_REGISTRY_KEY_PREFIX}{provider}"

    try:
        await redis_client.setex(cache_key, MODEL_REGISTRY_TTL, json.dumps(mapping))
        logger.info(f"Cached model mapping for {log_safe(provider)} with {len(mapping)} models")
    except Exception as e:
        logger.warning(f"Redis cache write failed for model registry: {e}")


async def refresh_model_registry(
    redis_client: redis.Redis,
    provider: str,
    api_key: str,
) -> dict[str, str]:
    """
    Force refresh the model registry cache for a provider.

    Args:
        redis_client: Redis connection
        provider: LLM provider ("openai", "anthropic", or "google")
        api_key: Provider API key

    Returns:
        Updated model ID -> display name mapping
    """
    cache_key = f"{MODEL_REGISTRY_KEY_PREFIX}{provider}"

    mapping = await _fetch_model_mapping(provider, api_key)

    try:
        await redis_client.setex(cache_key, MODEL_REGISTRY_TTL, json.dumps(mapping))
        logger.info(f"Refreshed model registry for {provider} with {len(mapping)} models")
    except Exception as e:
        logger.warning(f"Redis cache write failed for model registry: {e}")

    return mapping


async def invalidate_model_registry(
    redis_client: redis.Redis,
    provider: str | None = None,
) -> None:
    """
    Invalidate model registry cache.

    Args:
        redis_client: Redis connection
        provider: Specific provider to invalidate, or None for all
    """
    try:
        if provider:
            cache_key = f"{MODEL_REGISTRY_KEY_PREFIX}{provider}"
            await redis_client.delete(cache_key)
        else:
            # Invalidate all providers
            for p in ["openai", "anthropic", "google"]:
                cache_key = f"{MODEL_REGISTRY_KEY_PREFIX}{p}"
                await redis_client.delete(cache_key)
    except Exception as e:
        logger.warning(f"Failed to invalidate model registry cache: {e}")


async def _fetch_model_mapping(provider: str, api_key: str) -> dict[str, str]:
    """
    Fetch model ID -> display name mapping from provider API.

    Args:
        provider: LLM provider ("openai", "anthropic", or "google")
        api_key: Provider API key

    Returns:
        Dictionary mapping model IDs to display names
    """
    if provider == "anthropic":
        return await _fetch_anthropic_models(api_key)
    elif provider == "openai":
        return await _fetch_openai_models(api_key)
    elif provider == "google":
        return await _fetch_google_models(api_key)
    else:
        logger.warning(f"Unknown provider: {provider}")
        return {}


async def _fetch_google_models(api_key: str) -> dict[str, str]:
    """Fetch the Gemini Developer API model list."""
    from google import genai

    client = genai.Client(api_key=api_key)
    try:
        pager = await client.aio.models.list(config={"page_size": 100})
        return {
            (item.name or "").removeprefix("models/"): (
                item.display_name or item.name or "Unknown model"
            )
            for item in pager.page
            if item.name
        }
    finally:
        await client.aio.aclose()


async def _fetch_anthropic_models(api_key: str) -> dict[str, str]:
    """
    Fetch model list from Anthropic API.

    Anthropic's /v1/models endpoint returns:
    {
        "data": [
            {
                "type": "model",
                "id": "claude-opus-4-5-20251101",
                "display_name": "Claude Opus 4.5",
                "created_at": "2025-11-24T00:00:00Z"
            },
            ...
        ]
    }

    Returns:
        Dictionary mapping model IDs to display names
    """
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                "https://api.anthropic.com/v1/models",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                },
                timeout=30.0,
            )
            response.raise_for_status()

            data = response.json()
            models = data.get("data", [])

            mapping = {}
            for model in models:
                model_id = model.get("id")
                display_name = model.get("display_name")
                if model_id and display_name:
                    mapping[model_id] = display_name

            logger.debug(f"Fetched {len(mapping)} Anthropic models")
            return mapping

        except httpx.HTTPStatusError as e:
            logger.error(f"Anthropic API error: {e.response.status_code} - {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Failed to fetch Anthropic models: {e}")
            raise


async def _fetch_openai_models(api_key: str) -> dict[str, str]:
    """
    Fetch model list from OpenAI API.

    OpenAI's /v1/models endpoint returns:
    {
        "data": [
            {
                "id": "gpt-4o-2024-11-20",
                "object": "model",
                "owned_by": "openai",
                ...
            },
            ...
        ]
    }

    Note: OpenAI doesn't provide a "display_name" field. For OpenAI models,
    we derive the display name by stripping date suffixes from the ID.

    Returns:
        Dictionary mapping model IDs to display names
    """
    import re

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                "https://api.openai.com/v1/models",
                headers={
                    "Authorization": f"Bearer {api_key}",
                },
                timeout=30.0,
            )
            response.raise_for_status()

            data = response.json()
            models = data.get("data", [])

            # OpenAI date suffix pattern: -YYYY-MM-DD
            date_pattern = re.compile(r"-\d{4}-\d{2}-\d{2}$")

            mapping = {}
            for model in models:
                model_id = model.get("id")
                if model_id:
                    # Derive display name by stripping date suffix
                    display_name = date_pattern.sub("", model_id)
                    mapping[model_id] = display_name

            logger.debug(f"Fetched {len(mapping)} OpenAI models")
            return mapping

        except httpx.HTTPStatusError as e:
            logger.error(f"OpenAI API error: {e.response.status_code} - {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Failed to fetch OpenAI models: {e}")
            raise


def normalize_model_name(model_id: str) -> str:
    """
    Fallback normalization when provider API isn't available.

    Strips date suffixes from model IDs:
    - Anthropic: "claude-opus-4-5-20251101" -> "claude-opus-4-5"
    - OpenAI: "gpt-4o-2024-11-20" -> "gpt-4o"

    This is a fallback for when we can't reach the provider API.
    The get_display_name function should be preferred.

    Args:
        model_id: Versioned model ID

    Returns:
        Normalized model name with date suffix removed
    """
    import re

    # Match: -YYYYMMDD (Anthropic) or -YYYY-MM-DD (OpenAI)
    pattern = r"-\d{4}(?:-\d{2}-\d{2}|\d{4})$"
    return re.sub(pattern, "", model_id)
