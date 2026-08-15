"""
Unit tests for AI Usage Repository.

Tests database operations for AI usage tracking and model pricing.
"""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.repositories.ai_usage import (
    AIPricingRepository,
    AIUsageRepository,
    calculate_cost,
)


class TestCalculateCost:
    """Tests for standalone calculate_cost function."""

    def test_calculates_cost_correctly(self):
        """Test basic cost calculation."""
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

    def test_handles_zero_tokens(self):
        """Test zero tokens results in zero cost."""
        result = calculate_cost(
            input_tokens=0,
            output_tokens=0,
            input_price_per_million=Decimal("5.00"),
            output_price_per_million=Decimal("15.00"),
        )

        assert result == Decimal("0")

    def test_handles_million_tokens(self):
        """Test one million tokens equals exact price."""
        result = calculate_cost(
            input_tokens=1_000_000,
            output_tokens=0,
            input_price_per_million=Decimal("5.00"),
            output_price_per_million=Decimal("15.00"),
        )

        assert result == Decimal("5.00")


class TestAIPricingRepository:
    """Tests for AIPricingRepository."""

    @pytest.fixture
    def mock_session(self):
        """Create mock database session."""
        session = AsyncMock()
        session.add = MagicMock()
        return session

    @pytest.fixture
    def repository(self, mock_session):
        """Create repository with mock session."""
        repo = AIPricingRepository(mock_session)
        return repo

    @pytest.mark.asyncio
    async def test_get_by_model_returns_pricing(self, repository, mock_session):
        """Test get_by_model returns pricing record."""
        mock_pricing = MagicMock()
        mock_pricing.provider = "openai"
        mock_pricing.model = "gpt-4o"
        mock_pricing.input_price_per_million = Decimal("5.00")
        mock_pricing.output_price_per_million = Decimal("15.00")

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_pricing
        mock_session.execute.return_value = mock_result

        result = await repository.get_by_model("openai", "gpt-4o")

        assert result is not None
        assert result.provider == "openai"
        assert result.model == "gpt-4o"
        mock_session.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_by_model_returns_none_when_not_found(
        self, repository, mock_session
    ):
        """Test get_by_model returns None when not found."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        result = await repository.get_by_model("openai", "unknown-model")

        assert result is None

    @pytest.mark.asyncio
    async def test_list_all_returns_all_pricing(self, repository, mock_session):
        """Test list_all returns all pricing records."""
        from datetime import date, datetime
        from src.models.orm.ai_usage import AIModelPricing

        mock_pricing_1 = MagicMock(spec=AIModelPricing)
        mock_pricing_1.id = 1
        mock_pricing_1.provider = "openai"
        mock_pricing_1.model = "gpt-4o"
        mock_pricing_1.input_price_per_million = Decimal("5.00")
        mock_pricing_1.output_price_per_million = Decimal("15.00")
        mock_pricing_1.cache_read_price_per_million = None
        mock_pricing_1.cache_write_price_per_million = None
        mock_pricing_1.effective_date = date(2025, 1, 1)
        mock_pricing_1.created_at = datetime(2025, 1, 1, 0, 0, 0)
        mock_pricing_1.updated_at = datetime(2025, 1, 1, 0, 0, 0)

        mock_pricing_2 = MagicMock(spec=AIModelPricing)
        mock_pricing_2.id = 2
        mock_pricing_2.provider = "anthropic"
        mock_pricing_2.model = "claude-sonnet-4-20250514"
        mock_pricing_2.input_price_per_million = Decimal("3.00")
        mock_pricing_2.output_price_per_million = Decimal("15.00")
        mock_pricing_2.cache_read_price_per_million = None
        mock_pricing_2.cache_write_price_per_million = None
        mock_pricing_2.effective_date = date(2025, 1, 1)
        mock_pricing_2.created_at = datetime(2025, 1, 1, 0, 0, 0)
        mock_pricing_2.updated_at = datetime(2025, 1, 1, 0, 0, 0)

        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [mock_pricing_1, mock_pricing_2]
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute.return_value = mock_result

        result = await repository.list_all()

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_create_pricing_adds_record(self, repository, mock_session):
        """Test create_pricing adds new pricing record."""
        mock_session.refresh = AsyncMock()

        _result = await repository.create_pricing(
            provider="openai",
            model="gpt-4o-mini",
            input_price_per_million=Decimal("0.15"),
            output_price_per_million=Decimal("0.60"),
        )

        mock_session.add.assert_called_once()
        mock_session.flush.assert_called_once()
        mock_session.refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_pricing_modifies_existing(self, repository, mock_session):
        """Test update_pricing modifies existing record."""
        mock_pricing = MagicMock()
        mock_pricing.id = 1
        mock_pricing.input_price_per_million = Decimal("5.00")
        mock_pricing.output_price_per_million = Decimal("15.00")

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_pricing
        mock_session.execute.return_value = mock_result
        mock_session.refresh = AsyncMock()

        _result = await repository.update_pricing(
            pricing_id=1,
            input_price_per_million=Decimal("6.00"),
        )

        assert mock_pricing.input_price_per_million == Decimal("6.00")
        mock_session.flush.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_pricing_returns_none_when_not_found(
        self, repository, mock_session
    ):
        """Test update_pricing returns None when record not found."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        result = await repository.update_pricing(
            pricing_id=999,
            input_price_per_million=Decimal("6.00"),
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_list_used_models_returns_distinct_models(
        self, repository, mock_session
    ):
        """Test list_used_models returns distinct provider/model pairs."""
        mock_rows = [
            MagicMock(provider="openai", model="gpt-4o"),
            MagicMock(provider="anthropic", model="claude-sonnet-4-20250514"),
        ]
        mock_session.execute.return_value = MagicMock(all=MagicMock(return_value=mock_rows))

        result = await repository.list_used_models()

        assert len(result) == 2
        assert ("openai", "gpt-4o") in result
        assert ("anthropic", "claude-sonnet-4-20250514") in result


class TestAIUsageRepository:
    """Tests for AIUsageRepository."""

    @pytest.fixture
    def mock_session(self):
        """Create mock database session."""
        session = AsyncMock()
        session.add = MagicMock()
        return session

    @pytest.fixture
    def repository(self, mock_session):
        """Create repository with mock session."""
        return AIUsageRepository(mock_session)

    @pytest.mark.asyncio
    async def test_create_usage_adds_record(self, repository, mock_session):
        """Test create_usage adds new usage record."""
        execution_id = uuid4()
        mock_session.refresh = AsyncMock()

        _result = await repository.create_usage(
            provider="openai",
            model="gpt-4o",
            input_tokens=1000,
            output_tokens=500,
            execution_id=execution_id,
            cost=Decimal("0.0125"),
            duration_ms=150,
        )

        mock_session.add.assert_called_once()
        mock_session.flush.assert_called_once()
        mock_session.refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_by_execution_returns_usage(self, repository, mock_session):
        """Test list_by_execution returns usage records for execution."""
        from datetime import datetime
        from src.models.orm.ai_usage import AIUsage

        execution_id = uuid4()

        mock_usage = MagicMock(spec=AIUsage)
        mock_usage.id = 1
        mock_usage.provider = "openai"
        mock_usage.model = "gpt-4o"
        mock_usage.input_tokens = 1000
        mock_usage.output_tokens = 500
        mock_usage.cache_read_tokens = 0
        mock_usage.cache_write_tokens = 0
        mock_usage.cost = Decimal("0.0125")
        mock_usage.provider_cost = None
        mock_usage.duration_ms = 150
        mock_usage.execution_id = execution_id
        mock_usage.conversation_id = None
        mock_usage.message_id = None
        mock_usage.organization_id = None
        mock_usage.user_id = None
        mock_usage.timestamp = datetime(2025, 1, 1, 0, 0, 0)
        mock_usage.sequence = 1

        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [mock_usage]
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute.return_value = mock_result

        result = await repository.list_by_execution(execution_id)

        assert len(result) == 1
        mock_session.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_by_conversation_returns_usage(self, repository, mock_session):
        """Test list_by_conversation returns usage records for conversation."""
        from datetime import datetime
        from src.models.orm.ai_usage import AIUsage

        conversation_id = uuid4()

        mock_usage = MagicMock(spec=AIUsage)
        mock_usage.id = 1
        mock_usage.provider = "anthropic"
        mock_usage.model = "claude-sonnet-4-20250514"
        mock_usage.input_tokens = 2000
        mock_usage.output_tokens = 1000
        mock_usage.cache_read_tokens = 0
        mock_usage.cache_write_tokens = 0
        mock_usage.cost = Decimal("0.025")
        mock_usage.provider_cost = None
        mock_usage.duration_ms = 200
        mock_usage.execution_id = None
        mock_usage.conversation_id = conversation_id
        mock_usage.message_id = None
        mock_usage.organization_id = None
        mock_usage.user_id = None
        mock_usage.timestamp = datetime(2025, 1, 1, 0, 0, 0)
        mock_usage.sequence = 1

        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [mock_usage]
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute.return_value = mock_result

        result = await repository.list_by_conversation(conversation_id)

        assert len(result) == 1
        mock_session.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_totals_by_execution_aggregates_correctly(
        self, repository, mock_session
    ):
        """Test get_totals_by_execution returns aggregated values."""
        execution_id = uuid4()

        mock_row = MagicMock()
        mock_row.total_input = 3000
        mock_row.total_output = 1500
        mock_row.total_cache_read = 0
        mock_row.total_cache_write = 0
        mock_row.total_cost = Decimal("0.0375")
        mock_row.total_provider_cost = Decimal("0")
        mock_row.total_duration = 450
        mock_row.call_count = 3

        mock_result = MagicMock()
        mock_result.one.return_value = mock_row
        mock_session.execute.return_value = mock_result

        result = await repository.get_totals_by_execution(execution_id)

        assert result.total_input_tokens == 3000
        assert result.total_output_tokens == 1500
        assert result.total_cost == Decimal("0.0375")
        assert result.total_duration_ms == 450
        assert result.call_count == 3

    @pytest.mark.asyncio
    async def test_get_totals_by_conversation_aggregates_correctly(
        self, repository, mock_session
    ):
        """Test get_totals_by_conversation returns aggregated values."""
        conversation_id = uuid4()

        mock_row = MagicMock()
        mock_row.total_input = 5000
        mock_row.total_output = 2500
        mock_row.total_cache_read = 0
        mock_row.total_cache_write = 0
        mock_row.total_cost = Decimal("0.0625")
        mock_row.total_provider_cost = Decimal("0")
        mock_row.total_duration = 750
        mock_row.call_count = 5

        mock_result = MagicMock()
        mock_result.one.return_value = mock_row
        mock_session.execute.return_value = mock_result

        result = await repository.get_totals_by_conversation(conversation_id)

        assert result.total_input_tokens == 5000
        assert result.total_output_tokens == 2500
        assert result.call_count == 5

    @pytest.mark.asyncio
    async def test_get_usage_by_model_groups_correctly(self, repository, mock_session):
        """Test get_usage_by_model returns grouped usage."""
        execution_id = uuid4()

        mock_rows = [
            MagicMock(
                provider="openai",
                model="gpt-4o",
                input_tokens=2000,
                output_tokens=1000,
                cache_read_tokens=0,
                cache_write_tokens=0,
                cost=Decimal("0.025"),
                provider_cost=Decimal("0"),
                call_count=2,
            ),
            MagicMock(
                provider="anthropic",
                model="claude-sonnet-4-20250514",
                input_tokens=1000,
                output_tokens=500,
                cache_read_tokens=0,
                cache_write_tokens=0,
                cost=Decimal("0.0125"),
                provider_cost=Decimal("0"),
                call_count=1,
            ),
        ]
        mock_result = MagicMock()
        mock_result.all.return_value = mock_rows
        mock_session.execute.return_value = mock_result

        result = await repository.get_usage_by_model(execution_id=execution_id)

        assert len(result) == 2
        assert result[0].provider == "openai"
        assert result[0].call_count == 2

    @pytest.mark.asyncio
    async def test_get_next_sequence_returns_incremented_value(
        self, repository, mock_session
    ):
        """Test get_next_sequence returns next sequence number."""
        execution_id = uuid4()

        mock_result = MagicMock()
        mock_result.scalar.return_value = 4  # Already 3 records, next is 4
        mock_session.execute.return_value = mock_result

        result = await repository.get_next_sequence(execution_id=execution_id)

        assert result == 4

    @pytest.mark.asyncio
    async def test_get_next_sequence_returns_one_for_no_context(self, repository):
        """Test get_next_sequence returns 1 when no context provided."""
        result = await repository.get_next_sequence()

        assert result == 1

    @pytest.mark.asyncio
    async def test_get_next_sequence_handles_empty_result(
        self, repository, mock_session
    ):
        """Test get_next_sequence handles empty result gracefully."""
        execution_id = uuid4()

        mock_result = MagicMock()
        mock_result.scalar.return_value = None
        mock_session.execute.return_value = mock_result

        result = await repository.get_next_sequence(execution_id=execution_id)

        assert result == 1
