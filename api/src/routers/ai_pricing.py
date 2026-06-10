"""
AI Pricing Router

Provides CRUD endpoints for AI model pricing configuration.
Platform admin only.

Endpoint Structure:
- GET /api/settings/ai/pricing - List all model pricing
- POST /api/settings/ai/pricing - Create new pricing entry
- PUT /api/settings/ai/pricing/{pricing_id} - Update existing pricing
- DELETE /api/settings/ai/pricing/{pricing_id} - Delete pricing entry
"""

import logging
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from src.core.auth import CurrentActiveUser, RequirePlatformAdmin
from src.core.cache import get_shared_redis
from src.core.db_deps import DbSession
from src.core.log_safety import log_safe
from src.models import (
    AIModelPricingCreate,
    AIModelPricingListItem,
    AIModelPricingListResponse,
    AIModelPricingPublic,
    AIModelPricingUpdate,
)
from src.models.orm import AIModelPricing, AIUsage
from src.services.ai_usage_service import backfill_model_costs

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings/ai", tags=["AI Settings"])


@router.get(
    "/pricing",
    response_model=AIModelPricingListResponse,
    summary="List model pricing",
    description="List all model pricing plus models used without pricing configured.",
    dependencies=[RequirePlatformAdmin],
)
async def list_pricing(
    user: CurrentActiveUser,
    db: DbSession,
) -> AIModelPricingListResponse:
    """
    List all model pricing configurations.

    Returns list with pricing info and a flag for models that have been used
    but don't have pricing configured.
    """
    # Get all pricing entries
    pricing_query = select(AIModelPricing).order_by(
        AIModelPricing.provider, AIModelPricing.model
    )
    pricing_result = await db.execute(pricing_query)
    pricing_entries = pricing_result.scalars().all()

    # Get distinct models that have been used (from ai_usage table)
    # The ai_usage.model column should already contain display names (not model IDs)
    used_models_query = select(
        AIUsage.provider, AIUsage.model
    ).distinct()
    used_result = await db.execute(used_models_query)
    used_models: set[tuple[str, str]] = {
        (row.provider, row.model) for row in used_result.all()
    }

    # Build pricing list with is_used flag
    pricing_list: list[AIModelPricingListItem] = []
    configured_models: set[tuple[str, str]] = set()

    for entry in pricing_entries:
        model_key = (entry.provider, entry.model)
        configured_models.add(model_key)

        pricing_list.append(
            AIModelPricingListItem(
                id=entry.id,
                provider=entry.provider,
                model=entry.model,
                input_price_per_million=entry.input_price_per_million,
                output_price_per_million=entry.output_price_per_million,
                effective_date=entry.effective_date,
                created_at=entry.created_at,
                updated_at=entry.updated_at,
                is_used=model_key in used_models,
            )
        )

    # Find models that have been used but don't have pricing
    models_without_pricing = [
        f"{provider}/{model}"
        for provider, model in used_models
        if (provider, model) not in configured_models
    ]

    return AIModelPricingListResponse(
        pricing=pricing_list,
        models_without_pricing=sorted(models_without_pricing),
    )


@router.post(
    "/pricing",
    response_model=AIModelPricingPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Create model pricing",
    description="Create a new model pricing entry.",
    dependencies=[RequirePlatformAdmin],
)
async def create_pricing(
    user: CurrentActiveUser,
    db: DbSession,
    data: AIModelPricingCreate,
) -> AIModelPricingPublic:
    """Create new pricing entry."""
    # Check if pricing already exists for this provider/model
    existing_query = select(AIModelPricing).where(
        AIModelPricing.provider == data.provider,
        AIModelPricing.model == data.model,
    )
    existing_result = await db.execute(existing_query)
    if existing_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Pricing already exists for {data.provider}/{data.model}",
        )

    # Create new pricing entry
    pricing = AIModelPricing(
        provider=data.provider,
        model=data.model,
        input_price_per_million=data.input_price_per_million,
        output_price_per_million=data.output_price_per_million,
        effective_date=data.effective_date or datetime.now(timezone.utc).date(),
    )
    db.add(pricing)
    await db.flush()
    await db.refresh(pricing)

    logger.info(f"Created pricing for {log_safe(data.provider)}/{log_safe(data.model)} by {user.email}")

    # Backfill costs for historical usage records with NULL cost
    # This updates past executions/chats that used this model before pricing was configured
    try:
        redis_client = await get_shared_redis()
        backfilled_count = await backfill_model_costs(
            session=db,
            redis_client=redis_client,
            provider=data.provider,
            model=data.model,
            input_price_per_million=Decimal(str(data.input_price_per_million)),
            output_price_per_million=Decimal(str(data.output_price_per_million)),
        )
        if backfilled_count > 0:
            logger.info(
                f"Backfilled costs for {backfilled_count} historical records "
                f"for {log_safe(data.provider)}/{log_safe(data.model)}"
            )
    except Exception as e:
        # Log but don't fail the pricing creation if backfill fails
        logger.warning(f"Failed to backfill costs for {log_safe(data.provider)}/{log_safe(data.model)}: {log_safe(e)}")

    return AIModelPricingPublic.model_validate(pricing)


@router.put(
    "/pricing/{pricing_id}",
    response_model=AIModelPricingPublic,
    summary="Update model pricing",
    description="Update an existing model pricing entry.",
    dependencies=[RequirePlatformAdmin],
)
async def update_pricing(
    pricing_id: int,
    user: CurrentActiveUser,
    db: DbSession,
    data: AIModelPricingUpdate,
) -> AIModelPricingPublic:
    """Update existing pricing entry."""
    # Find pricing entry
    pricing_query = select(AIModelPricing).where(AIModelPricing.id == pricing_id)
    pricing_result = await db.execute(pricing_query)
    pricing = pricing_result.scalar_one_or_none()

    if not pricing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pricing entry {pricing_id} not found",
        )

    # Update fields if provided
    if data.input_price_per_million is not None:
        pricing.input_price_per_million = data.input_price_per_million
    if data.output_price_per_million is not None:
        pricing.output_price_per_million = data.output_price_per_million
    if data.effective_date is not None:
        pricing.effective_date = data.effective_date

    pricing.updated_at = datetime.now(timezone.utc)

    await db.flush()
    await db.refresh(pricing)

    logger.info(f"Updated pricing {log_safe(pricing_id)} for {log_safe(pricing.provider)}/{log_safe(pricing.model)} by {user.email}")

    return AIModelPricingPublic.model_validate(pricing)


@router.delete(
    "/pricing/{pricing_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete model pricing",
    description="Delete an existing model pricing entry.",
    dependencies=[RequirePlatformAdmin],
)
async def delete_pricing(
    pricing_id: int,
    user: CurrentActiveUser,
    db: DbSession,
) -> None:
    """Delete pricing entry."""
    # Find pricing entry
    pricing_query = select(AIModelPricing).where(AIModelPricing.id == pricing_id)
    pricing_result = await db.execute(pricing_query)
    pricing = pricing_result.scalar_one_or_none()

    if not pricing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pricing entry {pricing_id} not found",
        )

    provider = pricing.provider
    model = pricing.model

    await db.delete(pricing)
    await db.flush()

    logger.info(f"Deleted pricing {log_safe(pricing_id)} for {log_safe(provider)}/{log_safe(model)} by {user.email}")
