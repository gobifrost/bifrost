"""
Unit tests for AI Usage Service.

Tests AI usage recording, pricing lookup, and caching functionality.
"""

import json
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.services.ai_usage_service import (
    PRICING_KEY_PREFIX,
    USED_MODELS_KEY,
    USAGE_TOTALS_CONV_KEY_PREFIX,
    USAGE_TOTALS_KEY_PREFIX,
    backfill_model_costs,
    calculate_cost,
    get_cached_price,
    get_usage_totals,
    get_used_models,
    invalidate_pricing_cache,
    invalidate_usage_cache,
    record_ai_usage,
)


class TestCalculateCost:
    """Tests for calculate_cost function."""

    def test_calculates_cost_correctly(self):
        """Test basic cost calculation with known prices."""
        # GPT-4o pricing: $5/1M input, $15/1M output
        result = calculate_cost(
            input_tokens=1000,
            output_tokens=500,
            input_price_per_million=Decimal("5.00"),
            output_price_per_million=Decimal("15.00"),
        )

        # 1000/1M * $5 = $0.005
        # 500/1M * $15 = $0.0075
        # Total = $0.0125
        expected = Decimal("0.0125")
        assert result == expected

    def test_returns_none_when_no_pricing(self):
        """Test returns None when both prices are None."""
        result = calculate_cost(
            input_tokens=1000,
            output_tokens=500,
            input_price_per_million=None,
            output_price_per_million=None,
        )

        assert result is None

    def test_handles_partial_pricing_input_only(self):
        """Test handles case where only input pricing is set."""
        result = calculate_cost(
            input_tokens=1000,
            output_tokens=500,
            input_price_per_million=Decimal("5.00"),
            output_price_per_million=None,
        )

        expected = Decimal("0.005")
        assert result == expected

    def test_handles_partial_pricing_output_only(self):
        """Test handles case where only output pricing is set."""
        result = calculate_cost(
            input_tokens=1000,
            output_tokens=500,
            input_price_per_million=None,
            output_price_per_million=Decimal("15.00"),
        )

        expected = Decimal("0.0075")
        assert result == expected

    def test_zero_tokens_returns_zero_cost(self):
        """Test zero tokens results in zero cost."""
        result = calculate_cost(
            input_tokens=0,
            output_tokens=0,
            input_price_per_million=Decimal("5.00"),
            output_price_per_million=Decimal("15.00"),
        )

        assert result == Decimal("0")

    def test_large_token_counts(self):
        """Test handles large token counts correctly."""
        result = calculate_cost(
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            input_price_per_million=Decimal("5.00"),
            output_price_per_million=Decimal("15.00"),
        )

        # 1M tokens at $5 = $5, 1M tokens at $15 = $15, total = $20
        expected = Decimal("20.00")
        assert result == expected

    def test_prices_cached_input_without_double_counting_it(self):
        """Provider input totals include cache reads, so split them from regular input."""
        result = calculate_cost(
            input_tokens=1_000,
            output_tokens=100,
            cache_read_tokens=800,
            input_price_per_million=Decimal("10"),
            output_price_per_million=Decimal("20"),
            cache_read_price_per_million=Decimal("1"),
        )

        # 200 uncached input at $10/M + 800 cache reads at $1/M +
        # 100 output at $20/M.
        assert result == Decimal("0.0048")

    def test_falls_back_to_regular_input_price_when_cache_rate_is_unknown(self):
        result = calculate_cost(
            input_tokens=1_000,
            output_tokens=0,
            cache_read_tokens=800,
            input_price_per_million=Decimal("10"),
            output_price_per_million=Decimal("20"),
        )

        assert result == Decimal("0.010")


class TestGetCachedPrice:
    """Tests for get_cached_price function."""

    @pytest.fixture
    def mock_redis(self):
        """Create mock Redis client."""
        return AsyncMock()

    @pytest.fixture
    def mock_session(self):
        """Create mock database session."""
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_returns_cached_price(self, mock_redis, mock_session):
        """Test returns price from Redis cache."""
        mock_redis.get.return_value = json.dumps({
            "input_price": "5.00",
            "output_price": "15.00",
        })

        input_price, output_price = await get_cached_price(
            mock_redis, mock_session, "openai", "gpt-4o"
        )

        assert input_price == Decimal("5.00")
        assert output_price == Decimal("15.00")
        mock_redis.get.assert_called_once_with(f"{PRICING_KEY_PREFIX}openai:gpt-4o")
        mock_session.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_falls_back_to_db_on_cache_miss(self, mock_redis, mock_session):
        """Test falls back to database when cache misses."""
        mock_redis.get.return_value = None

        # Mock DB result
        mock_pricing = MagicMock()
        mock_pricing.input_price_per_million = Decimal("5.00")
        mock_pricing.output_price_per_million = Decimal("15.00")

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_pricing
        mock_session.execute.return_value = mock_result

        input_price, output_price = await get_cached_price(
            mock_redis, mock_session, "openai", "gpt-4o"
        )

        assert input_price == Decimal("5.00")
        assert output_price == Decimal("15.00")
        mock_session.execute.assert_called_once()
        # Should cache the result
        mock_redis.setex.assert_called_once()

    @pytest.mark.asyncio
    async def test_caches_not_found_result(self, mock_redis, mock_session):
        """Test caches 'not found' result to avoid repeated DB lookups."""
        mock_redis.get.return_value = None

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        input_price, output_price = await get_cached_price(
            mock_redis, mock_session, "openai", "gpt-4o"
        )

        assert input_price is None
        assert output_price is None
        # Should still cache the "not found" result
        mock_redis.setex.assert_called_once()
        cached_data = json.loads(mock_redis.setex.call_args[0][2])
        assert cached_data["input_price"] is None
        assert cached_data["output_price"] is None

    @pytest.mark.asyncio
    async def test_handles_redis_error_gracefully(self, mock_redis, mock_session):
        """Test handles Redis errors and falls back to DB."""
        mock_redis.get.side_effect = Exception("Redis connection error")

        mock_pricing = MagicMock()
        mock_pricing.input_price_per_million = Decimal("5.00")
        mock_pricing.output_price_per_million = Decimal("15.00")

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_pricing
        mock_session.execute.return_value = mock_result

        input_price, output_price = await get_cached_price(
            mock_redis, mock_session, "openai", "gpt-4o"
        )

        # Should still work via DB
        assert input_price == Decimal("5.00")
        assert output_price == Decimal("15.00")


class TestGetUsageTotals:
    """Tests for get_usage_totals function."""

    @pytest.fixture
    def mock_redis(self):
        """Create mock Redis client."""
        return AsyncMock()

    @pytest.fixture
    def mock_session(self):
        """Create mock database session."""
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_returns_empty_totals_when_no_context(self, mock_redis, mock_session):
        """Test returns zeros when neither execution nor conversation ID provided."""
        result = await get_usage_totals(mock_redis, mock_session)

        assert result == {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "total_cost": None,
            "provider_cost": None,
            "call_count": 0,
        }
        mock_redis.get.assert_not_called()
        mock_session.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_cached_totals_for_execution(self, mock_redis, mock_session):
        """Test returns cached totals for execution."""
        execution_id = uuid4()
        mock_redis.get.return_value = json.dumps({
            "input_tokens": 1000,
            "output_tokens": 500,
            "total_cost": "0.0125",
            "call_count": 5,
        })

        result = await get_usage_totals(mock_redis, mock_session, execution_id=execution_id)

        assert result["input_tokens"] == 1000
        assert result["output_tokens"] == 500
        assert result["total_cost"] == Decimal("0.0125")
        assert result["call_count"] == 5
        mock_redis.get.assert_called_once_with(f"{USAGE_TOTALS_KEY_PREFIX}{execution_id}")

    @pytest.mark.asyncio
    async def test_returns_cached_totals_for_conversation(self, mock_redis, mock_session):
        """Test returns cached totals for conversation."""
        conversation_id = uuid4()
        mock_redis.get.return_value = json.dumps({
            "input_tokens": 2000,
            "output_tokens": 1000,
            "total_cost": "0.025",
            "call_count": 10,
        })

        result = await get_usage_totals(
            mock_redis, mock_session, conversation_id=conversation_id
        )

        assert result["input_tokens"] == 2000
        mock_redis.get.assert_called_once_with(
            f"{USAGE_TOTALS_CONV_KEY_PREFIX}{conversation_id}"
        )

    @pytest.mark.asyncio
    async def test_queries_db_on_cache_miss(self, mock_redis, mock_session):
        """Test queries database when cache misses."""
        execution_id = uuid4()
        mock_redis.get.return_value = None

        mock_row = MagicMock()
        mock_row.input_tokens = 1500
        mock_row.output_tokens = 750
        mock_row.cache_read_tokens = 0
        mock_row.cache_write_tokens = 0
        mock_row.total_cost = Decimal("0.02")
        mock_row.provider_cost = None
        mock_row.call_count = 3

        mock_result = MagicMock()
        mock_result.one_or_none.return_value = mock_row
        mock_session.execute.return_value = mock_result

        result = await get_usage_totals(mock_redis, mock_session, execution_id=execution_id)

        assert result["input_tokens"] == 1500
        assert result["output_tokens"] == 750
        assert result["total_cost"] == Decimal("0.02")
        assert result["call_count"] == 3
        mock_session.execute.assert_called_once()
        mock_redis.set.assert_called_once()


class TestInvalidateUsageCache:
    """Tests for invalidate_usage_cache function."""

    @pytest.fixture
    def mock_redis(self):
        """Create mock Redis client."""
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_deletes_execution_cache(self, mock_redis):
        """Test deletes cache for execution."""
        execution_id = uuid4()

        await invalidate_usage_cache(mock_redis, execution_id=execution_id)

        mock_redis.delete.assert_called_once_with(
            f"{USAGE_TOTALS_KEY_PREFIX}{execution_id}"
        )

    @pytest.mark.asyncio
    async def test_deletes_conversation_cache(self, mock_redis):
        """Test deletes cache for conversation."""
        conversation_id = uuid4()

        await invalidate_usage_cache(mock_redis, conversation_id=conversation_id)

        mock_redis.delete.assert_called_once_with(
            f"{USAGE_TOTALS_CONV_KEY_PREFIX}{conversation_id}"
        )

    @pytest.mark.asyncio
    async def test_deletes_both_caches(self, mock_redis):
        """Test deletes both execution and conversation caches."""
        execution_id = uuid4()
        conversation_id = uuid4()

        await invalidate_usage_cache(
            mock_redis, execution_id=execution_id, conversation_id=conversation_id
        )

        assert mock_redis.delete.call_count == 2

    @pytest.mark.asyncio
    async def test_handles_redis_error_gracefully(self, mock_redis):
        """Test handles Redis errors without raising."""
        mock_redis.delete.side_effect = Exception("Redis error")
        execution_id = uuid4()

        # Should not raise
        await invalidate_usage_cache(mock_redis, execution_id=execution_id)


class TestInvalidatePricingCache:
    """Tests for invalidate_pricing_cache function."""

    @pytest.fixture
    def mock_redis(self):
        """Create mock Redis client."""
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_deletes_pricing_cache(self, mock_redis):
        """Test deletes pricing cache for model."""
        await invalidate_pricing_cache(mock_redis, "openai", "gpt-4o")

        mock_redis.delete.assert_called_once_with(f"{PRICING_KEY_PREFIX}openai:gpt-4o")


class TestGetUsedModels:
    """Tests for get_used_models function."""

    @pytest.fixture
    def mock_redis(self):
        """Create mock Redis client."""
        return AsyncMock()

    @pytest.fixture
    def mock_session(self):
        """Create mock database session."""
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_returns_models_from_redis_set(self, mock_redis, mock_session):
        """Test returns models from Redis SET."""
        # Redis SET members as "provider:model" strings
        mock_redis.smembers.return_value = {
            "anthropic:claude-sonnet-4-20250514",
            "openai:gpt-4o",
        }

        result = await get_used_models(mock_redis, mock_session)

        # Should be sorted by the combined string
        assert len(result) == 2
        assert result[0] == {"provider": "anthropic", "model": "claude-sonnet-4-20250514"}
        assert result[1] == {"provider": "openai", "model": "gpt-4o"}
        mock_redis.smembers.assert_called_once_with(USED_MODELS_KEY)
        mock_session.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_bytes_from_redis(self, mock_redis, mock_session):
        """Test handles bytes returned from Redis (decode_responses=False)."""
        mock_redis.smembers.return_value = {
            b"openai:gpt-4o",
            b"anthropic:claude-sonnet-4",
        }

        result = await get_used_models(mock_redis, mock_session)

        assert len(result) == 2
        # Should decode bytes to strings
        assert any(m["provider"] == "openai" for m in result)
        assert any(m["provider"] == "anthropic" for m in result)

    @pytest.mark.asyncio
    async def test_queries_db_on_empty_set(self, mock_redis, mock_session):
        """Test queries database when Redis SET is empty (cold start)."""
        mock_redis.smembers.return_value = set()

        mock_rows = [
            MagicMock(provider="openai", model="gpt-4o"),
            MagicMock(provider="anthropic", model="claude-sonnet-4-20250514"),
        ]
        mock_session.execute.return_value = mock_rows

        result = await get_used_models(mock_redis, mock_session)

        assert len(result) == 2
        assert result[0]["provider"] == "openai"
        mock_session.execute.assert_called_once()
        # Should populate Redis SET with DB results
        mock_redis.sadd.assert_called_once_with(
            USED_MODELS_KEY,
            "openai:gpt-4o",
            "anthropic:claude-sonnet-4-20250514",
        )

    @pytest.mark.asyncio
    async def test_handles_redis_error_gracefully(self, mock_redis, mock_session):
        """Test falls back to DB on Redis error."""
        mock_redis.smembers.side_effect = Exception("Redis connection error")

        mock_rows = [
            MagicMock(provider="openai", model="gpt-4o"),
        ]
        mock_session.execute.return_value = mock_rows

        result = await get_used_models(mock_redis, mock_session)

        assert len(result) == 1
        assert result[0]["provider"] == "openai"


class TestRecordAIUsage:
    """Tests for record_ai_usage function."""

    @pytest.fixture
    def mock_redis(self):
        """Create mock Redis client."""
        redis = AsyncMock()
        redis.exists.return_value = False
        return redis

    @pytest.fixture
    def mock_session(self):
        """Create mock database session."""
        session = AsyncMock()
        session.add = MagicMock()
        return session

    @pytest.mark.asyncio
    async def test_records_usage_with_pricing(self, mock_redis, mock_session):
        """Test records AI usage when pricing is available."""
        execution_id = uuid4()

        # Mock pricing lookup
        mock_redis.get.return_value = json.dumps({
            "input_price": "5.00",
            "output_price": "15.00",
        })

        # Patch at the ORM model module level where AIUsage is defined
        with patch("src.models.orm.ai_usage.AIUsage") as MockAIUsage, \
             patch("src.services.model_registry.get_display_name") as mock_get_display_name:
            mock_usage = MagicMock()
            MockAIUsage.return_value = mock_usage
            mock_get_display_name.return_value = "gpt-4o"

            await record_ai_usage(
                session=mock_session,
                redis_client=mock_redis,
                provider="openai",
                model="gpt-4o",
                input_tokens=1000,
                output_tokens=500,
                duration_ms=150,
                execution_id=execution_id,
            )

            # Should have created AIUsage with cost
            MockAIUsage.assert_called_once()
            call_kwargs = MockAIUsage.call_args[1]
            assert call_kwargs["provider"] == "openai"
            assert call_kwargs["model"] == "gpt-4o"
            assert call_kwargs["input_tokens"] == 1000
            assert call_kwargs["output_tokens"] == 500
            assert call_kwargs["cost"] is not None

            mock_session.add.assert_called_once_with(mock_usage)
            mock_session.flush.assert_called_once()

    @pytest.mark.asyncio
    async def test_openrouter_uses_exact_cost_and_recovers_missing_catalog_price(
        self, mock_redis, mock_session
    ):
        """Exact response cost wins while catalog recovery fills future pricing."""
        exact_cost = Decimal("0.00012345")
        recovered_pricing = (
            Decimal("0.20"),
            Decimal("0.80"),
            Decimal("0.02"),
            None,
        )

        with (
            patch("src.models.orm.ai_usage.AIUsage") as MockAIUsage,
            patch(
                "src.services.ai_usage_service.get_cached_pricing",
                new=AsyncMock(
                    side_effect=[
                        (None, None, None, None),
                        recovered_pricing,
                    ]
                ),
            ) as pricing_lookup,
            patch(
                "src.services.model_pricing.discover_openrouter_pricing",
                new=AsyncMock(return_value=True),
            ) as discover,
        ):
            await record_ai_usage(
                session=mock_session,
                redis_client=mock_redis,
                provider="openrouter",
                model="deepseek/deepseek-v4-flash",
                input_tokens=10_000,
                output_tokens=500,
                cache_read_tokens=8_000,
                provider_cost=exact_cost,
                execution_id=uuid4(),
            )

        discover.assert_awaited_once_with(
            mock_session,
            mock_redis,
            "deepseek/deepseek-v4-flash",
        )
        assert pricing_lookup.await_count == 2
        call_kwargs = MockAIUsage.call_args.kwargs
        assert call_kwargs["provider"] == "openrouter"
        assert call_kwargs["model"] == "deepseek/deepseek-v4-flash"
        assert call_kwargs["cache_read_tokens"] == 8_000
        assert call_kwargs["provider_cost"] == exact_cost
        assert call_kwargs["cost"] == exact_cost

    @pytest.mark.asyncio
    async def test_converts_versioned_model_to_display_name(self, mock_redis, mock_session):
        """Test converts versioned model ID to display name before storing."""
        execution_id = uuid4()

        # Mock pricing lookup
        mock_redis.get.return_value = json.dumps({
            "input_price": "3.00",
            "output_price": "15.00",
        })

        with patch("src.models.orm.ai_usage.AIUsage") as MockAIUsage, \
             patch("src.services.model_registry.get_display_name") as mock_get_display_name:
            mock_usage = MagicMock()
            MockAIUsage.return_value = mock_usage
            # Simulate display name lookup returning the human-readable name
            mock_get_display_name.return_value = "Claude Opus 4.5"

            await record_ai_usage(
                session=mock_session,
                redis_client=mock_redis,
                provider="anthropic",
                model="claude-opus-4-5-20251101",  # Versioned ID from API
                input_tokens=2000,
                output_tokens=1000,
                execution_id=execution_id,
                api_key="test-key",
            )

            # Should have called get_display_name with the versioned model ID
            mock_get_display_name.assert_called_once_with(
                mock_redis, "anthropic", "claude-opus-4-5-20251101", "test-key"
            )

            # Should store the display name, not the versioned ID
            call_kwargs = MockAIUsage.call_args[1]
            assert call_kwargs["model"] == "Claude Opus 4.5"

    @pytest.mark.asyncio
    async def test_records_usage_without_pricing(self, mock_redis, mock_session):
        """Test records AI usage with null cost when pricing unavailable."""
        execution_id = uuid4()

        # Mock no pricing in cache or DB
        mock_redis.get.return_value = json.dumps({
            "input_price": None,
            "output_price": None,
        })

        with patch("src.models.orm.ai_usage.AIUsage") as MockAIUsage, \
             patch("src.services.model_registry.get_display_name") as mock_get_display_name:
            mock_usage = MagicMock()
            MockAIUsage.return_value = mock_usage
            mock_get_display_name.return_value = "unknown-model"

            await record_ai_usage(
                session=mock_session,
                redis_client=mock_redis,
                provider="openai",
                model="unknown-model",
                input_tokens=1000,
                output_tokens=500,
                execution_id=execution_id,
            )

            call_kwargs = MockAIUsage.call_args[1]
            assert call_kwargs["cost"] is None

    @pytest.mark.asyncio
    async def test_invalidates_cache_after_recording(self, mock_redis, mock_session):
        """Test invalidates usage totals cache after recording."""
        execution_id = uuid4()

        mock_redis.get.return_value = json.dumps({
            "input_price": "5.00",
            "output_price": "15.00",
        })

        with patch("src.models.orm.ai_usage.AIUsage"), \
             patch("src.services.model_registry.get_display_name") as mock_get_display_name:
            mock_get_display_name.return_value = "gpt-4o"

            await record_ai_usage(
                session=mock_session,
                redis_client=mock_redis,
                provider="openai",
                model="gpt-4o",
                input_tokens=1000,
                output_tokens=500,
                execution_id=execution_id,
            )

            # Should invalidate execution cache
            mock_redis.delete.assert_any_call(f"{USAGE_TOTALS_KEY_PREFIX}{execution_id}")

    @pytest.mark.asyncio
    async def test_adds_model_to_used_models_set(self, mock_redis, mock_session):
        """Test adds model to Redis SET after recording."""
        execution_id = uuid4()

        mock_redis.get.return_value = json.dumps({
            "input_price": "5.00",
            "output_price": "15.00",
        })

        with patch("src.models.orm.ai_usage.AIUsage"), \
             patch("src.services.model_registry.get_display_name") as mock_get_display_name:
            mock_get_display_name.return_value = "gpt-4o"

            await record_ai_usage(
                session=mock_session,
                redis_client=mock_redis,
                provider="openai",
                model="gpt-4o",
                input_tokens=1000,
                output_tokens=500,
                execution_id=execution_id,
            )

            # Should add model to used_models Redis SET via SADD
            mock_redis.sadd.assert_called_once_with(USED_MODELS_KEY, "openai:gpt-4o")

    @pytest.mark.asyncio
    async def test_handles_errors_gracefully(self, mock_redis, mock_session):
        """Test handles errors without raising."""
        mock_redis.get.return_value = json.dumps({
            "input_price": "5.00",
            "output_price": "15.00",
        })
        mock_session.flush.side_effect = Exception("DB error")

        # Should not raise
        with patch("src.models.orm.ai_usage.AIUsage"), \
             patch("src.services.model_registry.get_display_name") as mock_get_display_name:
            mock_get_display_name.return_value = "gpt-4o"

            await record_ai_usage(
                session=mock_session,
                redis_client=mock_redis,
                provider="openai",
                model="gpt-4o",
                input_tokens=1000,
                output_tokens=500,
            )


class TestBackfillModelCosts:
    """Tests for backfill_model_costs function."""

    @pytest.fixture
    def mock_redis(self):
        """Create mock Redis client."""
        redis = AsyncMock()
        # Default: no keys to scan
        redis.scan.return_value = (0, [])
        return redis

    @pytest.fixture
    def mock_session(self):
        """Create mock database session."""
        session = AsyncMock()
        return session

    @pytest.mark.asyncio
    async def test_updates_records_with_null_cost(self, mock_redis, mock_session):
        """Test updates records that have NULL cost."""
        # Mock update result with 5 rows updated
        mock_result = MagicMock()
        mock_result.rowcount = 5
        mock_session.execute.return_value = mock_result

        count = await backfill_model_costs(
            session=mock_session,
            redis_client=mock_redis,
            provider="openai",
            model="gpt-4o",
            input_price_per_million=Decimal("5.00"),
            output_price_per_million=Decimal("15.00"),
        )

        assert count == 5
        mock_session.execute.assert_called_once()
        mock_session.flush.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_records_to_update(self, mock_redis, mock_session):
        """Test returns 0 when no records have NULL cost."""
        mock_result = MagicMock()
        mock_result.rowcount = 0
        mock_session.execute.return_value = mock_result

        count = await backfill_model_costs(
            session=mock_session,
            redis_client=mock_redis,
            provider="openai",
            model="gpt-4o",
            input_price_per_million=Decimal("5.00"),
            output_price_per_million=Decimal("15.00"),
        )

        assert count == 0

    @pytest.mark.asyncio
    async def test_invalidates_caches_when_records_updated(self, mock_redis, mock_session):
        """Test invalidates usage caches when records are updated."""
        mock_result = MagicMock()
        mock_result.rowcount = 10
        mock_session.execute.return_value = mock_result

        # Mock scan returning some keys to delete
        mock_redis.scan.side_effect = [
            (0, [f"{USAGE_TOTALS_KEY_PREFIX}exec1", f"{USAGE_TOTALS_KEY_PREFIX}exec2"]),
        ]

        await backfill_model_costs(
            session=mock_session,
            redis_client=mock_redis,
            provider="openai",
            model="gpt-4o",
            input_price_per_million=Decimal("5.00"),
            output_price_per_million=Decimal("15.00"),
        )

        # Should have scanned for keys to delete
        mock_redis.scan.assert_called()
        # Should have deleted the found keys
        mock_redis.delete.assert_called()

    @pytest.mark.asyncio
    async def test_does_not_invalidate_caches_when_no_updates(self, mock_redis, mock_session):
        """Test does not invalidate caches when no records were updated."""
        mock_result = MagicMock()
        mock_result.rowcount = 0
        mock_session.execute.return_value = mock_result

        await backfill_model_costs(
            session=mock_session,
            redis_client=mock_redis,
            provider="openai",
            model="gpt-4o",
            input_price_per_million=Decimal("5.00"),
            output_price_per_million=Decimal("15.00"),
        )

        # Should not scan/delete when no updates
        mock_redis.scan.assert_not_called()
        mock_redis.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_raises_on_db_error(self, mock_redis, mock_session):
        """Test raises exception on database error."""
        mock_session.execute.side_effect = Exception("DB connection error")

        with pytest.raises(Exception, match="DB connection error"):
            await backfill_model_costs(
                session=mock_session,
                redis_client=mock_redis,
                provider="openai",
                model="gpt-4o",
                input_price_per_million=Decimal("5.00"),
                output_price_per_million=Decimal("15.00"),
            )
