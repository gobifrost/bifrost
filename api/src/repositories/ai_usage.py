"""
AI Usage Repository

Database operations for AI usage tracking and model pricing.
"""

import logging
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import desc, func, select

from src.models.contracts.ai_usage import (
    AIModelPricingPublic,
    AIUsageByModel,
    AIUsagePublic,
    AIUsageTotals,
)
from src.models.orm.ai_usage import AIModelPricing, AIUsage
from src.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class AIPricingRepository(BaseRepository[AIModelPricing]):
    """Repository for AI model pricing operations."""

    model = AIModelPricing

    async def get_by_model(
        self,
        provider: str,
        model: str,
    ) -> AIModelPricing | None:
        """
        Get pricing for a specific provider/model combination.

        Args:
            provider: AI provider (e.g., 'openai', 'anthropic')
            model: Model identifier

        Returns:
            AIModelPricing or None if not found
        """
        result = await self.session.execute(
            select(AIModelPricing).where(
                AIModelPricing.provider == provider,
                AIModelPricing.model == model,
            )
        )
        return result.scalar_one_or_none()

    async def list_all(self) -> list[AIModelPricingPublic]:
        """
        List all pricing configurations.

        Returns:
            List of all pricing records
        """
        result = await self.session.execute(
            select(AIModelPricing).order_by(
                AIModelPricing.provider, AIModelPricing.model
            )
        )
        return [
            AIModelPricingPublic.model_validate(p) for p in result.scalars().all()
        ]

    async def create_pricing(
        self,
        provider: str,
        model: str,
        input_price_per_million: Decimal,
        output_price_per_million: Decimal,
        cache_read_price_per_million: Decimal | None = None,
        cache_write_price_per_million: Decimal | None = None,
        effective_date: datetime | None = None,
    ) -> AIModelPricing:
        """
        Create a new pricing record.

        Args:
            provider: AI provider
            model: Model identifier
            input_price_per_million: Cost per million input tokens
            output_price_per_million: Cost per million output tokens
            effective_date: Optional date when pricing takes effect

        Returns:
            Created pricing record
        """
        pricing = AIModelPricing(
            provider=provider,
            model=model,
            input_price_per_million=input_price_per_million,
            output_price_per_million=output_price_per_million,
            cache_read_price_per_million=cache_read_price_per_million,
            cache_write_price_per_million=cache_write_price_per_million,
        )
        if effective_date:
            pricing.effective_date = effective_date  # type: ignore[assignment]

        self.session.add(pricing)
        await self.session.flush()
        await self.session.refresh(pricing)

        logger.info(f"Created pricing for {provider}/{model}")
        return pricing

    async def update_pricing(
        self,
        pricing_id: int,
        input_price_per_million: Decimal | None = None,
        output_price_per_million: Decimal | None = None,
        cache_read_price_per_million: Decimal | None = None,
        cache_write_price_per_million: Decimal | None = None,
        effective_date: datetime | None = None,
    ) -> AIModelPricing | None:
        """
        Update an existing pricing record.

        Args:
            pricing_id: ID of pricing record to update
            input_price_per_million: New input price (optional)
            output_price_per_million: New output price (optional)
            effective_date: New effective date (optional)

        Returns:
            Updated pricing record or None if not found
        """
        result = await self.session.execute(
            select(AIModelPricing).where(AIModelPricing.id == pricing_id)
        )
        pricing = result.scalar_one_or_none()

        if not pricing:
            return None

        if input_price_per_million is not None:
            pricing.input_price_per_million = input_price_per_million
        if output_price_per_million is not None:
            pricing.output_price_per_million = output_price_per_million
        if cache_read_price_per_million is not None:
            pricing.cache_read_price_per_million = cache_read_price_per_million
        if cache_write_price_per_million is not None:
            pricing.cache_write_price_per_million = cache_write_price_per_million
        if effective_date is not None:
            pricing.effective_date = effective_date  # type: ignore[assignment]

        await self.session.flush()
        await self.session.refresh(pricing)

        logger.info(f"Updated pricing {pricing_id}")
        return pricing

    async def list_used_models(self) -> list[tuple[str, str]]:
        """
        List all provider/model combinations that have been used.

        Returns:
            List of (provider, model) tuples
        """
        result = await self.session.execute(
            select(AIUsage.provider, AIUsage.model)
            .distinct()
            .order_by(AIUsage.provider, AIUsage.model)
        )
        return [(row.provider, row.model) for row in result.all()]


class AIUsageRepository(BaseRepository[AIUsage]):
    """Repository for AI usage tracking operations."""

    model = AIUsage

    async def create_usage(
        self,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        provider_cost: Decimal | None = None,
        execution_id: UUID | None = None,
        conversation_id: UUID | None = None,
        message_id: UUID | None = None,
        cost: Decimal | None = None,
        duration_ms: int | None = None,
        sequence: int = 1,
        organization_id: UUID | None = None,
        user_id: UUID | None = None,
    ) -> AIUsage:
        """
        Create a new AI usage record.

        Args:
            provider: AI provider (e.g., 'openai', 'anthropic')
            model: Model identifier
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens
            execution_id: Optional execution ID (for workflow context)
            conversation_id: Optional conversation ID (for chat context)
            message_id: Optional message ID
            cost: Pre-calculated cost (if not provided, can be calculated later)
            duration_ms: Request duration in milliseconds
            sequence: Sequence number for ordering within context
            organization_id: Optional organization ID
            user_id: Optional user ID

        Returns:
            Created usage record
        """
        usage = AIUsage(
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            provider_cost=provider_cost,
            execution_id=execution_id,
            conversation_id=conversation_id,
            message_id=message_id,
            cost=cost,
            duration_ms=duration_ms,
            sequence=sequence,
            organization_id=organization_id,
            user_id=user_id,
        )

        self.session.add(usage)
        await self.session.flush()
        await self.session.refresh(usage)

        logger.debug(
            f"Created AI usage: {provider}/{model} - "
            f"{input_tokens} in / {output_tokens} out"
        )
        return usage

    async def list_by_execution(
        self,
        execution_id: UUID,
        limit: int = 100,
    ) -> list[AIUsagePublic]:
        """
        List all AI usage for a specific execution.

        Args:
            execution_id: Execution UUID
            limit: Maximum number of records to return

        Returns:
            List of usage records
        """
        result = await self.session.execute(
            select(AIUsage)
            .where(AIUsage.execution_id == execution_id)
            .order_by(AIUsage.sequence)
            .limit(limit)
        )
        return [AIUsagePublic.model_validate(u) for u in result.scalars().all()]

    async def list_by_conversation(
        self,
        conversation_id: UUID,
        limit: int = 100,
    ) -> list[AIUsagePublic]:
        """
        List all AI usage for a specific conversation.

        Args:
            conversation_id: Conversation UUID
            limit: Maximum number of records to return

        Returns:
            List of usage records
        """
        result = await self.session.execute(
            select(AIUsage)
            .where(AIUsage.conversation_id == conversation_id)
            .order_by(AIUsage.sequence)
            .limit(limit)
        )
        return [AIUsagePublic.model_validate(u) for u in result.scalars().all()]

    async def get_totals_by_execution(
        self,
        execution_id: UUID,
    ) -> AIUsageTotals:
        """
        Get aggregated usage totals for an execution.

        Args:
            execution_id: Execution UUID

        Returns:
            Aggregated usage totals
        """
        result = await self.session.execute(
            select(
                func.coalesce(func.sum(AIUsage.input_tokens), 0).label("total_input"),
                func.coalesce(func.sum(AIUsage.output_tokens), 0).label("total_output"),
                func.coalesce(func.sum(AIUsage.cache_read_tokens), 0).label("total_cache_read"),
                func.coalesce(func.sum(AIUsage.cache_write_tokens), 0).label("total_cache_write"),
                func.coalesce(func.sum(AIUsage.provider_cost), Decimal("0")).label("total_provider_cost"),
                func.coalesce(func.sum(AIUsage.cost), Decimal("0")).label("total_cost"),
                func.coalesce(func.sum(AIUsage.duration_ms), 0).label("total_duration"),
                func.count().label("call_count"),
            ).where(AIUsage.execution_id == execution_id)
        )
        row = result.one()

        return AIUsageTotals(
            total_input_tokens=row.total_input,
            total_output_tokens=row.total_output,
            total_cache_read_tokens=row.total_cache_read,
            total_cache_write_tokens=row.total_cache_write,
            total_provider_cost=row.total_provider_cost,
            total_cost=row.total_cost,
            total_duration_ms=row.total_duration,
            call_count=row.call_count,
        )

    async def get_totals_by_conversation(
        self,
        conversation_id: UUID,
    ) -> AIUsageTotals:
        """
        Get aggregated usage totals for a conversation.

        Args:
            conversation_id: Conversation UUID

        Returns:
            Aggregated usage totals
        """
        result = await self.session.execute(
            select(
                func.coalesce(func.sum(AIUsage.input_tokens), 0).label("total_input"),
                func.coalesce(func.sum(AIUsage.output_tokens), 0).label("total_output"),
                func.coalesce(func.sum(AIUsage.cache_read_tokens), 0).label("total_cache_read"),
                func.coalesce(func.sum(AIUsage.cache_write_tokens), 0).label("total_cache_write"),
                func.coalesce(func.sum(AIUsage.provider_cost), Decimal("0")).label("total_provider_cost"),
                func.coalesce(func.sum(AIUsage.cost), Decimal("0")).label("total_cost"),
                func.coalesce(func.sum(AIUsage.duration_ms), 0).label("total_duration"),
                func.count().label("call_count"),
            ).where(AIUsage.conversation_id == conversation_id)
        )
        row = result.one()

        return AIUsageTotals(
            total_input_tokens=row.total_input,
            total_output_tokens=row.total_output,
            total_cache_read_tokens=row.total_cache_read,
            total_cache_write_tokens=row.total_cache_write,
            total_provider_cost=row.total_provider_cost,
            total_cost=row.total_cost,
            total_duration_ms=row.total_duration,
            call_count=row.call_count,
        )

    async def get_usage_by_model(
        self,
        execution_id: UUID | None = None,
        conversation_id: UUID | None = None,
        organization_id: UUID | None = None,
    ) -> list[AIUsageByModel]:
        """
        Get usage breakdown by model.

        At least one of execution_id, conversation_id, or organization_id must be provided.

        Args:
            execution_id: Optional execution filter
            conversation_id: Optional conversation filter
            organization_id: Optional organization filter

        Returns:
            Usage breakdown by provider/model
        """
        query = select(
            AIUsage.provider,
            AIUsage.model,
            func.sum(AIUsage.input_tokens).label("input_tokens"),
            func.sum(AIUsage.output_tokens).label("output_tokens"),
            func.sum(AIUsage.cache_read_tokens).label("cache_read_tokens"),
            func.sum(AIUsage.cache_write_tokens).label("cache_write_tokens"),
            func.coalesce(func.sum(AIUsage.provider_cost), Decimal("0")).label("provider_cost"),
            func.coalesce(func.sum(AIUsage.cost), Decimal("0")).label("cost"),
            func.count().label("call_count"),
        ).group_by(AIUsage.provider, AIUsage.model)

        if execution_id:
            query = query.where(AIUsage.execution_id == execution_id)
        if conversation_id:
            query = query.where(AIUsage.conversation_id == conversation_id)
        if organization_id:
            query = query.where(AIUsage.organization_id == organization_id)

        query = query.order_by(desc("cost"))

        result = await self.session.execute(query)
        return [
            AIUsageByModel(
                provider=row.provider,
                model=row.model,
                input_tokens=row.input_tokens,
                output_tokens=row.output_tokens,
                cache_read_tokens=row.cache_read_tokens,
                cache_write_tokens=row.cache_write_tokens,
                provider_cost=row.provider_cost,
                cost=row.cost,
                call_count=row.call_count,
            )
            for row in result.all()
        ]

    async def get_next_sequence(
        self,
        execution_id: UUID | None = None,
        conversation_id: UUID | None = None,
    ) -> int:
        """
        Get the next sequence number for a given context.

        Args:
            execution_id: Optional execution ID
            conversation_id: Optional conversation ID

        Returns:
            Next sequence number (1-based)
        """
        query = select(func.coalesce(func.max(AIUsage.sequence), 0) + 1)

        if execution_id:
            query = query.where(AIUsage.execution_id == execution_id)
        elif conversation_id:
            query = query.where(AIUsage.conversation_id == conversation_id)
        else:
            return 1

        result = await self.session.execute(query)
        return result.scalar() or 1


def calculate_cost(
    input_tokens: int,
    output_tokens: int,
    input_price_per_million: Decimal,
    output_price_per_million: Decimal,
) -> Decimal:
    """
    Calculate the cost for a given token count.

    Args:
        input_tokens: Number of input tokens
        output_tokens: Number of output tokens
        input_price_per_million: Price per million input tokens
        output_price_per_million: Price per million output tokens

    Returns:
        Total cost in USD
    """
    input_cost = (Decimal(input_tokens) / Decimal(1_000_000)) * input_price_per_million
    output_cost = (Decimal(output_tokens) / Decimal(1_000_000)) * output_price_per_million
    return input_cost + output_cost
