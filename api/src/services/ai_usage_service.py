"""
AI Usage Recording Service

Records AI usage from workflow executions (CLI AI endpoints), chat conversations
(agent executor), and autonomous agent runs to the ai_usage table with Redis
caching for pricing and aggregates.

Redis Key Structure:
- ai_pricing:{provider}:{model} - Cached pricing data (TTL: 1 hour)
- ai_usage_totals:{execution_id} - Execution aggregates (no TTL, invalidate on write)
- ai_usage_totals:conv:{conversation_id} - Conversation aggregates (no TTL, invalidate on write)
- ai_usage_totals:run:{agent_run_id} - Agent run aggregates (no TTL, invalidate on write)
- ai_used_models - Redis SET of "provider:model" strings (no TTL, incremental adds via SADD)
- ai_pricing_notified:{provider}:{model} - Deduplication for missing price notifications (TTL: 24 hours)
"""

import json
import logging
from decimal import Decimal
from typing import Awaitable, cast
from uuid import UUID

import redis.asyncio as redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.log_safety import log_safe

logger = logging.getLogger(__name__)

# Redis key prefixes
PRICING_KEY_PREFIX = "ai_pricing:"
USAGE_TOTALS_KEY_PREFIX = "ai_usage_totals:"
USAGE_TOTALS_CONV_KEY_PREFIX = "ai_usage_totals:conv:"
USAGE_TOTALS_RUN_KEY_PREFIX = "ai_usage_totals:run:"
USED_MODELS_KEY = "ai_used_models"
PRICING_NOTIFIED_KEY_PREFIX = "ai_pricing_notified:"

# TTL settings (in seconds)
PRICING_TTL = 3600  # 1 hour
PRICING_NOTIFIED_TTL = 86400  # 24 hours


async def record_ai_usage(
    session: AsyncSession,
    redis_client: redis.Redis,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    provider_cost: Decimal | None = None,
    sequence: int = 1,
    duration_ms: int | None = None,
    execution_id: UUID | None = None,
    conversation_id: UUID | None = None,
    agent_run_id: UUID | None = None,
    message_id: UUID | None = None,
    organization_id: UUID | None = None,
    user_id: UUID | None = None,
    api_key: str | None = None,
) -> None:
    """
    Record an AI usage event.

    Steps:
    1. Convert model ID to display name
    2. Get pricing from cache or DB
    3. Calculate cost
    4. Insert to ai_usage table
    5. Invalidate aggregates cache
    6. Add model to used_models set

    Args:
        session: Database session
        redis_client: Redis connection
        provider: LLM provider (e.g., 'openai', 'anthropic')
        model: Model identifier (versioned, e.g., 'gpt-4o-2024-11-20', 'claude-opus-4-5-20251101')
        input_tokens: Number of input tokens used
        output_tokens: Number of output tokens used
        duration_ms: Request duration in milliseconds
        execution_id: UUID of workflow execution (for CLI AI calls)
        conversation_id: UUID of conversation (for agent chat)
        agent_run_id: UUID of agent run (for autonomous agent runs)
        message_id: UUID of message within conversation
        organization_id: UUID of organization
        user_id: UUID of user who initiated the call
        api_key: Provider API key (optional, for fetching display names if not cached)
    """
    from src.models.orm.ai_usage import AIUsage
    from src.services.model_registry import get_display_name

    try:
        from src.services.model_pricing import (
            OPENROUTER_PROVIDER,
            canonical_provider,
            discover_openrouter_pricing,
        )

        provider = canonical_provider(provider)
        # OpenRouter catalog and response IDs use stable slugs. Native providers
        # retain the existing display-name normalization contract.
        display_name = (
            model
            if provider == OPENROUTER_PROVIDER
            else await get_display_name(redis_client, provider, model, api_key)
        )

        pricing = await get_cached_pricing(
            redis_client, session, provider, display_name
        )
        pricing_missing = pricing[0] is None and pricing[1] is None
        if pricing_missing and provider == OPENROUTER_PROVIDER:
            discovered = await discover_openrouter_pricing(
                session,
                redis_client,
                display_name,
            )
            if discovered:
                await invalidate_pricing_cache(redis_client, provider, display_name)
                pricing = await get_cached_pricing(
                    redis_client, session, provider, display_name
                )
                pricing_missing = pricing[0] is None and pricing[1] is None

        calculated_cost = calculate_cost(
            input_tokens,
            output_tokens,
            pricing[0],
            pricing[1],
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            cache_read_price_per_million=pricing[2],
            cache_write_price_per_million=pricing[3],
        )
        cost = provider_cost if provider_cost is not None else calculated_cost

        if pricing_missing:
            await _notify_missing_pricing(redis_client, provider, display_name)

        # 4. Insert to ai_usage table (using display name for consistency)
        usage = AIUsage(
            provider=provider,
            model=display_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            provider_cost=provider_cost,
            cost=cost,
            duration_ms=duration_ms,
            execution_id=execution_id,
            conversation_id=conversation_id,
            agent_run_id=agent_run_id,
            message_id=message_id,
            organization_id=organization_id,
            user_id=user_id,
            sequence=sequence,
        )
        session.add(usage)
        await session.flush()

        # 5. Invalidate aggregates cache
        await invalidate_usage_cache(redis_client, execution_id, conversation_id, agent_run_id)

        # 6. Add model to used_models set (using display name)
        await _add_used_model(redis_client, provider, display_name)

        logger.debug(
            f"Recorded AI usage: provider={provider}, model={display_name}, "
            f"tokens={input_tokens}/{output_tokens}, cost={cost}"
        )

    except Exception as e:
        logger.error(f"Failed to record AI usage: {e}", exc_info=True)
        # Don't re-raise - AI usage recording should not block the main flow


async def get_cached_price(
    redis_client: redis.Redis,
    session: AsyncSession,
    provider: str,
    model: str,
) -> tuple[Decimal | None, Decimal | None]:
    """
    Get pricing for a model from Redis cache, falling back to DB.

    Returns:
        Tuple of (input_price_per_million, output_price_per_million)
        Returns (None, None) if pricing not found.

    Cache key: ai_pricing:{provider}:{model}
    TTL: 1 hour
    """
    pricing = await get_cached_pricing(redis_client, session, provider, model)
    return pricing[0], pricing[1]


async def get_cached_pricing(
    redis_client: redis.Redis,
    session: AsyncSession,
    provider: str,
    model: str,
) -> tuple[Decimal | None, Decimal | None, Decimal | None, Decimal | None]:
    """Get input, output, cache-read, and cache-write prices per million."""

    cache_key = f"{PRICING_KEY_PREFIX}{provider}:{model}"

    try:
        # Check Redis cache first
        cached = await redis_client.get(cache_key)
        if cached:
            data = json.loads(cached)
            input_price = (
                Decimal(data["input_price"])
                if data.get("input_price") is not None
                else None
            )
            output_price = (
                Decimal(data["output_price"])
                if data.get("output_price") is not None
                else None
            )
            cache_read_price = (
                Decimal(data["cache_read_price"])
                if data.get("cache_read_price") is not None
                else None
            )
            cache_write_price = (
                Decimal(data["cache_write_price"])
                if data.get("cache_write_price") is not None
                else None
            )
            return input_price, output_price, cache_read_price, cache_write_price

    except Exception as e:
        logger.warning(f"Redis cache read failed for pricing: {e}")

    # Fallback to database
    try:
        from src.models.orm.ai_usage import AIModelPricing

        result = await session.execute(
            select(AIModelPricing).where(
                AIModelPricing.provider == provider,
                AIModelPricing.model == model,
            )
        )
        pricing = result.scalar_one_or_none()

        if pricing:
            input_price = pricing.input_price_per_million
            output_price = pricing.output_price_per_million
            cache_read_price = pricing.cache_read_price_per_million
            cache_write_price = pricing.cache_write_price_per_million

            # Cache the result
            try:
                cache_data = {
                    "input_price": str(input_price) if input_price is not None else None,
                    "output_price": str(output_price) if output_price is not None else None,
                    "cache_read_price": (
                        str(cache_read_price) if cache_read_price is not None else None
                    ),
                    "cache_write_price": (
                        str(cache_write_price) if cache_write_price is not None else None
                    ),
                }
                await redis_client.setex(cache_key, PRICING_TTL, json.dumps(cache_data))
            except Exception as e:
                logger.warning(f"Redis cache write failed for pricing: {e}")

            return input_price, output_price, cache_read_price, cache_write_price

        # Cache the "not found" result to avoid repeated DB lookups
        try:
            cache_data = {
                "input_price": None,
                "output_price": None,
                "cache_read_price": None,
                "cache_write_price": None,
            }
            await redis_client.setex(cache_key, PRICING_TTL, json.dumps(cache_data))
        except Exception as e:
            logger.warning(f"Redis cache write failed for empty pricing: {e}")

        return None, None, None, None

    except Exception as e:
        logger.warning(f"Failed to get pricing from DB: {e}")
        return None, None, None, None


def calculate_cost(
    input_tokens: int,
    output_tokens: int,
    input_price_per_million: Decimal | None,
    output_price_per_million: Decimal | None,
    *,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    cache_read_price_per_million: Decimal | None = None,
    cache_write_price_per_million: Decimal | None = None,
) -> Decimal | None:
    """
    Calculate total cost based on token counts and pricing.

    Args:
        input_tokens: Number of input tokens
        output_tokens: Number of output tokens
        input_price_per_million: Price per million input tokens
        output_price_per_million: Price per million output tokens

    Returns:
        Total cost as Decimal, or None if pricing not available
    """
    if all(
        price is None
        for price in (
            input_price_per_million,
            output_price_per_million,
            cache_read_price_per_million,
            cache_write_price_per_million,
        )
    ):
        return None

    total = Decimal(0)

    regular_input_tokens = input_tokens
    if cache_read_price_per_million is not None:
        priced_cache_reads = min(cache_read_tokens, regular_input_tokens)
        regular_input_tokens -= priced_cache_reads
        total += _calculate_partial_cost(
            priced_cache_reads, cache_read_price_per_million
        )
    if cache_write_price_per_million is not None:
        priced_cache_writes = min(cache_write_tokens, regular_input_tokens)
        regular_input_tokens -= priced_cache_writes
        total += _calculate_partial_cost(
            priced_cache_writes, cache_write_price_per_million
        )

    if input_price_per_million is not None:
        total += _calculate_partial_cost(
            regular_input_tokens, input_price_per_million
        )

    if output_price_per_million is not None:
        total += _calculate_partial_cost(output_tokens, output_price_per_million)

    return total


def _calculate_partial_cost(tokens: int, price_per_million: Decimal) -> Decimal:
    """Calculate cost for a given number of tokens at a price per million."""
    return (Decimal(tokens) / Decimal(1_000_000)) * price_per_million


async def get_usage_totals(
    redis_client: redis.Redis,
    session: AsyncSession,
    execution_id: UUID | None = None,
    conversation_id: UUID | None = None,
    agent_run_id: UUID | None = None,
) -> dict:
    """
    Get aggregated AI usage totals for an execution, conversation, or agent run.

    Returns:
        {
            "input_tokens": int,
            "output_tokens": int,
            "total_cost": Decimal | None,
            "call_count": int
        }

    Cache key:
        - ai_usage_totals:{execution_id} for executions
        - ai_usage_totals:conv:{conversation_id} for conversations
        - ai_usage_totals:run:{agent_run_id} for agent runs
    No TTL - invalidate on new AI call
    """
    if not execution_id and not conversation_id and not agent_run_id:
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "provider_cost": None,
            "total_cost": None,
            "call_count": 0,
        }

    # Build cache key
    if execution_id:
        cache_key = f"{USAGE_TOTALS_KEY_PREFIX}{execution_id}"
    elif conversation_id:
        cache_key = f"{USAGE_TOTALS_CONV_KEY_PREFIX}{conversation_id}"
    else:
        cache_key = f"{USAGE_TOTALS_RUN_KEY_PREFIX}{agent_run_id}"

    # Check cache
    try:
        cached = await redis_client.get(cache_key)
        if cached:
            data = json.loads(cached)
            # Convert cost back to Decimal if present
            if data.get("total_cost") is not None:
                data["total_cost"] = Decimal(data["total_cost"])
            if data.get("provider_cost") is not None:
                data["provider_cost"] = Decimal(data["provider_cost"])
            return data
    except Exception as e:
        logger.warning(f"Redis cache read failed for usage totals: {e}")

    # Query database
    from src.models.orm.ai_usage import AIUsage

    query = select(
        func.sum(AIUsage.input_tokens).label("input_tokens"),
        func.sum(AIUsage.output_tokens).label("output_tokens"),
        func.sum(AIUsage.cache_read_tokens).label("cache_read_tokens"),
        func.sum(AIUsage.cache_write_tokens).label("cache_write_tokens"),
        func.sum(AIUsage.provider_cost).label("provider_cost"),
        func.sum(AIUsage.cost).label("total_cost"),
        func.count(AIUsage.id).label("call_count"),
    )

    if execution_id:
        query = query.where(AIUsage.execution_id == execution_id)
    elif conversation_id:
        query = query.where(AIUsage.conversation_id == conversation_id)
    else:
        query = query.where(AIUsage.agent_run_id == agent_run_id)

    result = await session.execute(query)
    row = result.one_or_none()

    if row:
        totals = {
            "input_tokens": row.input_tokens or 0,
            "output_tokens": row.output_tokens or 0,
            "cache_read_tokens": row.cache_read_tokens or 0,
            "cache_write_tokens": row.cache_write_tokens or 0,
            "provider_cost": row.provider_cost,
            "total_cost": row.total_cost,
            "call_count": row.call_count or 0,
        }
    else:
        totals = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "provider_cost": None,
            "total_cost": None,
            "call_count": 0,
        }

    # Cache the result (no TTL - invalidate on write)
    try:
        cache_data = totals.copy()
        if cache_data.get("total_cost") is not None:
            cache_data["total_cost"] = str(cache_data["total_cost"])
        if cache_data.get("provider_cost") is not None:
            cache_data["provider_cost"] = str(cache_data["provider_cost"])
        await redis_client.set(cache_key, json.dumps(cache_data))
    except Exception as e:
        logger.warning(f"Redis cache write failed for usage totals: {e}")

    return totals


async def invalidate_usage_cache(
    redis_client: redis.Redis,
    execution_id: UUID | None = None,
    conversation_id: UUID | None = None,
    agent_run_id: UUID | None = None,
) -> None:
    """Invalidate the totals cache for an execution, conversation, or agent run."""
    try:
        if execution_id:
            cache_key = f"{USAGE_TOTALS_KEY_PREFIX}{execution_id}"
            await redis_client.delete(cache_key)

        if conversation_id:
            cache_key = f"{USAGE_TOTALS_CONV_KEY_PREFIX}{conversation_id}"
            await redis_client.delete(cache_key)

        if agent_run_id:
            cache_key = f"{USAGE_TOTALS_RUN_KEY_PREFIX}{agent_run_id}"
            await redis_client.delete(cache_key)
    except Exception as e:
        logger.warning(f"Failed to invalidate usage cache: {e}")


async def invalidate_pricing_cache(
    redis_client: redis.Redis,
    provider: str,
    model: str,
) -> None:
    """Invalidate pricing cache when pricing is updated."""
    try:
        cache_key = f"{PRICING_KEY_PREFIX}{provider}:{model}"
        await redis_client.delete(cache_key)
        logger.debug(f"Invalidated pricing cache for {provider}:{model}")
    except Exception as e:
        logger.warning(f"Failed to invalidate pricing cache: {e}")


async def get_used_models(
    redis_client: redis.Redis,
    session: AsyncSession,
) -> list[dict]:
    """
    Get list of all models that have been used.

    Uses a Redis SET for efficient incremental updates:
    - Members are stored as "provider:model" strings
    - Falls back to DB query on cold start (empty set)

    Cache key: ai_used_models (Redis SET)

    Returns:
        [{"provider": "openai", "model": "gpt-4o"}, ...]
    """
    # Check Redis SET first
    try:
        members = await cast(Awaitable[set[str]], redis_client.smembers(USED_MODELS_KEY))
        if members:
            models = []
            for member in sorted(members):
                # Handle both bytes and string returns from redis
                if isinstance(member, bytes):
                    member = member.decode("utf-8")
                if ":" in member:
                    provider, model = member.split(":", 1)
                    models.append({"provider": provider, "model": model})
            return models
    except Exception as e:
        logger.warning(f"Redis SET read failed for used models: {e}")

    # Cold start: Query database and populate Redis SET
    from src.models.orm.ai_usage import AIUsage

    result = await session.execute(
        select(AIUsage.provider, AIUsage.model)
        .distinct()
        .order_by(AIUsage.provider, AIUsage.model)
    )

    models = [{"provider": row.provider, "model": row.model} for row in result]

    # Populate Redis SET from DB results
    try:
        if models:
            member_strs = [f"{m['provider']}:{m['model']}" for m in models]
            await cast(Awaitable[int], redis_client.sadd(USED_MODELS_KEY, *member_strs))
    except Exception as e:
        logger.warning(f"Redis SET write failed for used models: {e}")

    return models


async def _add_used_model(
    redis_client: redis.Redis,
    provider: str,
    model: str,
) -> None:
    """Add a model to the used_models Redis SET."""
    try:
        # Use SADD for O(1) incremental updates instead of cache invalidation
        await cast(Awaitable[int], redis_client.sadd(USED_MODELS_KEY, f"{provider}:{model}"))
    except Exception as e:
        logger.warning(f"Failed to add model to used models set: {e}")


async def _notify_missing_pricing(
    redis_client: redis.Redis,
    provider: str,
    model: str,
) -> None:
    """
    Notify admins that a model is being used without configured pricing.

    Uses Redis for deduplication - only notifies once per model per 24 hours.
    Errors are logged but don't propagate - notification failures should not
    block AI usage recording.

    Args:
        redis_client: Redis connection
        provider: LLM provider (e.g., 'openai', 'anthropic')
        model: Model identifier (e.g., 'gpt-4o')
    """
    cache_key = f"{PRICING_NOTIFIED_KEY_PREFIX}{provider}:{model}"

    try:
        # Check if we've already notified for this model today
        already_notified = await redis_client.exists(cache_key)
        if already_notified:
            return

        # Set the deduplication key (24-hour TTL)
        await redis_client.setex(cache_key, PRICING_NOTIFIED_TTL, "1")

        # Create admin notification
        from src.models.contracts.notifications import (
            NotificationCategory,
            NotificationCreate,
            NotificationStatus,
        )
        from src.services.notification_service import get_notification_service

        notification_service = get_notification_service()

        await notification_service.create_notification(
            user_id="system",
            request=NotificationCreate(
                category=NotificationCategory.SYSTEM,
                title="AI Model Missing Pricing",
                description=f"Model {provider}/{model} used without configured pricing",
                metadata={
                    "provider": provider,
                    "model": model,
                    "action": "configure_pricing",
                    "action_label": "Configure Pricing",
                    "action_url": "/settings/ai",
                },
            ),
            for_admins=True,
            initial_status=NotificationStatus.AWAITING_ACTION,
        )

        logger.info(f"Created missing pricing notification for {provider}/{model}")

    except Exception as e:
        # Don't let notification failures break AI usage recording
        logger.warning(f"Failed to create missing pricing notification: {e}")


async def backfill_model_costs(
    session: AsyncSession,
    redis_client: redis.Redis,
    provider: str,
    model: str,
    input_price_per_million: Decimal,
    output_price_per_million: Decimal,
    cache_read_price_per_million: Decimal | None = None,
    cache_write_price_per_million: Decimal | None = None,
) -> int:
    """
    Backfill costs for historical AI usage records that have cost=NULL.

    Called when pricing is first configured for a model that was previously
    used without pricing. Only updates records where cost IS NULL (not
    records with existing costs from previous pricing).

    Args:
        session: Database session
        redis_client: Redis connection
        provider: LLM provider (e.g., 'openai', 'anthropic')
        model: Model identifier (e.g., 'gpt-4o')
        input_price_per_million: Price per million input tokens
        output_price_per_million: Price per million output tokens

    Returns:
        Number of records updated
    """
    from sqlalchemy import update

    from src.models.orm.ai_usage import AIUsage

    try:
        # Find all records for this model with NULL cost
        # Use a batch update query for efficiency
        regular_input_tokens = AIUsage.input_tokens
        input_cost = Decimal(0)
        if cache_read_price_per_million is not None:
            regular_input_tokens = regular_input_tokens - AIUsage.cache_read_tokens
            input_cost = input_cost + (
                AIUsage.cache_read_tokens
                * cache_read_price_per_million
                / Decimal(1_000_000)
            )
        if cache_write_price_per_million is not None:
            regular_input_tokens = regular_input_tokens - AIUsage.cache_write_tokens
            input_cost = input_cost + (
                AIUsage.cache_write_tokens
                * cache_write_price_per_million
                / Decimal(1_000_000)
            )
        input_cost = input_cost + (
            regular_input_tokens * input_price_per_million / Decimal(1_000_000)
        )

        result = await session.execute(
            update(AIUsage)
            .where(
                AIUsage.provider == provider,
                AIUsage.model == model,
                AIUsage.cost.is_(None),
            )
            .values(
                cost=(
                    input_cost
                    + (AIUsage.output_tokens * output_price_per_million / Decimal(1_000_000))
                )
            )
        )

        updated_count = result.rowcount
        await session.flush()

        if updated_count > 0:
            logger.info(
                f"Backfilled costs for {updated_count} records: "
                f"provider={log_safe(provider)}, model={log_safe(model)}"
            )

            # Invalidate all usage totals caches for affected records
            # Note: This is a simplified approach. For large datasets,
            # a more targeted invalidation might be needed.
            await _invalidate_all_usage_caches(redis_client)

        return updated_count

    except Exception as e:
        logger.error(f"Failed to backfill costs for {log_safe(provider)}/{log_safe(model)}: {log_safe(e)}", exc_info=True)
        raise


async def _invalidate_all_usage_caches(redis_client: redis.Redis) -> None:
    """
    Invalidate all usage totals caches after a bulk update.

    Uses SCAN to find and delete matching keys without blocking Redis.
    """
    try:
        cursor = 0
        deleted_count = 0

        while True:
            cursor, keys = await redis_client.scan(
                cursor=cursor,
                match=f"{USAGE_TOTALS_KEY_PREFIX}*",
                count=100,
            )
            if keys:
                await redis_client.delete(*keys)
                deleted_count += len(keys)

            if cursor == 0:
                break

        # Also scan conversation caches
        cursor = 0
        while True:
            cursor, keys = await redis_client.scan(
                cursor=cursor,
                match=f"{USAGE_TOTALS_CONV_KEY_PREFIX}*",
                count=100,
            )
            if keys:
                await redis_client.delete(*keys)
                deleted_count += len(keys)

            if cursor == 0:
                break

        if deleted_count > 0:
            logger.debug(f"Invalidated {deleted_count} usage cache keys after backfill")

    except Exception as e:
        logger.warning(f"Failed to invalidate usage caches: {e}")
