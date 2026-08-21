"""
Unit tests for DataProviderRepository.

Tests the database operations for data provider registry.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from src.repositories.data_providers import DataProviderRepository


class TestDataProviderRepository:
    """Tests for DataProviderRepository methods."""

    @pytest.fixture
    def mock_session(self):
        """Create a mock database session."""
        session = AsyncMock()
        session.execute = AsyncMock()
        return session

    @pytest.fixture
    def org_id(self):
        """Create a test organization ID."""
        return uuid4()

    @pytest.fixture
    def repository(self, mock_session, org_id):
        """Create repository with mock session and org context."""
        return DataProviderRepository(mock_session, org_id=org_id, bypass_resource_admission=True)

    @pytest.fixture
    def mock_provider(self):
        """Create a mock data provider object."""
        provider = MagicMock()
        provider.id = uuid4()
        provider.name = "test-provider"
        provider.description = "Test provider"
        provider.path = "/workspace/providers/test.py"
        provider.is_active = True
        provider.type = "data_provider"
        provider.organization_id = None
        return provider

    async def test_get_by_name_found(self, repository, mock_session, mock_provider):
        """Test getting provider by name when found."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_provider
        mock_session.execute.return_value = mock_result

        result = await repository.get_by_name("test-provider")

        assert result == mock_provider
        mock_session.execute.assert_called()

    async def test_get_by_name_not_found(self, repository, mock_session):
        """Test getting provider by name when not found."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        result = await repository.get_by_name("nonexistent")

        assert result is None

    async def test_get_all_active(self, repository, mock_session, mock_provider):
        """Test getting all active providers."""
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [mock_provider]
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute.return_value = mock_result

        result = await repository.get_all_active()

        assert len(result) == 1
        assert result[0] == mock_provider

    async def test_count_active(self, repository, mock_session):
        """Test counting active providers."""
        mock_result = MagicMock()
        mock_result.scalar.return_value = 3
        mock_session.execute.return_value = mock_result

        result = await repository.count_active()

        assert result == 3

    async def test_count_active_returns_zero_on_none(self, repository, mock_session):
        """Test count returns 0 when result is None."""
        mock_result = MagicMock()
        mock_result.scalar.return_value = None
        mock_session.execute.return_value = mock_result

        result = await repository.count_active()

        assert result == 0

    async def test_search_with_query(self, repository, mock_session, mock_provider):
        """Test search with text query."""
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [mock_provider]
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute.return_value = mock_result

        result = await repository.search(query="test")

        assert len(result) == 1

    async def test_search_with_limit_offset(self, repository, mock_session, mock_provider):
        """Test search with pagination."""
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [mock_provider]
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute.return_value = mock_result

        result = await repository.search(limit=10, offset=5)

        assert len(result) == 1
        mock_session.execute.assert_called_once()
