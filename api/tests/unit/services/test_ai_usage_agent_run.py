"""
Unit tests for AI Usage Service — agent_run_id paths.

Tests that record_ai_usage, get_usage_totals, and invalidate_usage_cache
correctly handle the agent_run_id parameter (cache keys, ORM fields, invalidation).
"""

import json
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.services.ai_usage_service import (
    USAGE_TOTALS_RUN_KEY_PREFIX,
    get_usage_totals,
    invalidate_usage_cache,
    record_ai_usage,
)


class TestRecordAIUsageWithAgentRunId:
    """Tests for record_ai_usage with agent_run_id."""

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
    async def test_record_ai_usage_with_agent_run_id(self, mock_redis, mock_session):
        """Verify AIUsage object is created with correct agent_run_id."""
        agent_run_id = uuid4()

        mock_redis.get.return_value = json.dumps({
            "input_price": "3.00",
            "output_price": "15.00",
        })

        with patch("src.models.orm.ai_usage.AIUsage") as MockAIUsage, \
             patch("src.services.model_registry.get_display_name") as mock_get_display_name:
            mock_usage = MagicMock()
            MockAIUsage.return_value = mock_usage
            mock_get_display_name.return_value = "claude-sonnet-4-20250514"

            await record_ai_usage(
                session=mock_session,
                redis_client=mock_redis,
                provider="anthropic",
                model="claude-sonnet-4-20250514",
                input_tokens=2000,
                output_tokens=800,
                duration_ms=320,
                agent_run_id=agent_run_id,
            )

            # Verify AIUsage was created with agent_run_id
            MockAIUsage.assert_called_once()
            call_kwargs = MockAIUsage.call_args[1]
            assert call_kwargs["agent_run_id"] == agent_run_id
            assert call_kwargs["provider"] == "anthropic"
            assert call_kwargs["model"] == "claude-sonnet-4-20250514"
            assert call_kwargs["input_tokens"] == 2000
            assert call_kwargs["output_tokens"] == 800
            assert call_kwargs["cost"] is not None

            # Verify DB operations
            mock_session.add.assert_called_once_with(mock_usage)
            mock_session.flush.assert_called_once()

            # Verify cache invalidation includes the run key
            mock_redis.delete.assert_called_once_with(
                f"{USAGE_TOTALS_RUN_KEY_PREFIX}{agent_run_id}"
            )


class TestGetUsageTotalsWithAgentRunId:
    """Tests for get_usage_totals with agent_run_id."""

    @pytest.fixture
    def mock_redis(self):
        """Create mock Redis client."""
        return AsyncMock()

    @pytest.fixture
    def mock_session(self):
        """Create mock database session."""
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_get_usage_totals_with_agent_run_id(self, mock_redis, mock_session):
        """Verify correct cache key with 'run:' prefix and DB fallback."""
        agent_run_id = uuid4()

        # Cache miss — force DB query path
        mock_redis.get.return_value = None

        mock_row = MagicMock()
        mock_row.input_tokens = 5000
        mock_row.output_tokens = 2000
        mock_row.cache_read_tokens = 0
        mock_row.cache_write_tokens = 0
        mock_row.provider_cost = None
        mock_row.total_cost = Decimal("0.045")
        mock_row.call_count = 7

        mock_result = MagicMock()
        mock_result.one_or_none.return_value = mock_row
        mock_session.execute.return_value = mock_result

        result = await get_usage_totals(
            mock_redis, mock_session, agent_run_id=agent_run_id
        )

        # Verify correct aggregated values
        assert result["input_tokens"] == 5000
        assert result["output_tokens"] == 2000
        assert result["total_cost"] == Decimal("0.045")
        assert result["call_count"] == 7

        # Verify cache key uses "run:" prefix
        expected_key = f"{USAGE_TOTALS_RUN_KEY_PREFIX}{agent_run_id}"
        mock_redis.get.assert_called_once_with(expected_key)

        # Verify the result is cached with the correct key
        mock_redis.set.assert_called_once()
        cached_key = mock_redis.set.call_args[0][0]
        assert cached_key == expected_key
        assert "run:" in cached_key

    @pytest.mark.asyncio
    async def test_get_usage_totals_agent_run_id_cache_hit(self, mock_redis, mock_session):
        """Verify cached totals are returned for agent_run_id."""
        agent_run_id = uuid4()

        mock_redis.get.return_value = json.dumps({
            "input_tokens": 3000,
            "output_tokens": 1500,
            "total_cost": "0.03",
            "call_count": 4,
        })

        result = await get_usage_totals(
            mock_redis, mock_session, agent_run_id=agent_run_id
        )

        assert result["input_tokens"] == 3000
        assert result["output_tokens"] == 1500
        assert result["total_cost"] == Decimal("0.03")
        assert result["call_count"] == 4

        mock_redis.get.assert_called_once_with(
            f"{USAGE_TOTALS_RUN_KEY_PREFIX}{agent_run_id}"
        )
        mock_session.execute.assert_not_called()


class TestInvalidateUsageCacheAgentRunId:
    """Tests for invalidate_usage_cache with agent_run_id."""

    @pytest.fixture
    def mock_redis(self):
        """Create mock Redis client."""
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_invalidate_usage_cache_agent_run_id(self, mock_redis):
        """Verify correct key is deleted for agent_run_id."""
        agent_run_id = uuid4()

        await invalidate_usage_cache(mock_redis, agent_run_id=agent_run_id)

        expected_key = f"{USAGE_TOTALS_RUN_KEY_PREFIX}{agent_run_id}"
        mock_redis.delete.assert_called_once_with(expected_key)
        assert f"run:{agent_run_id}" in expected_key

    @pytest.mark.asyncio
    async def test_invalidate_usage_cache_all_three_ids(self, mock_redis):
        """Verify all three cache keys are deleted when all IDs provided."""
        execution_id = uuid4()
        conversation_id = uuid4()
        agent_run_id = uuid4()

        await invalidate_usage_cache(
            mock_redis,
            execution_id=execution_id,
            conversation_id=conversation_id,
            agent_run_id=agent_run_id,
        )

        assert mock_redis.delete.call_count == 3
        delete_calls = [call.args[0] for call in mock_redis.delete.call_args_list]
        assert f"ai_usage_totals:{execution_id}" in delete_calls
        assert f"ai_usage_totals:conv:{conversation_id}" in delete_calls
        assert f"ai_usage_totals:run:{agent_run_id}" in delete_calls
