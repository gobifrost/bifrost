"""
Unit tests for WorkflowRepository.

Tests the database operations for workflow registry.
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from src.repositories.workflows import WorkflowRepository


class TestWorkflowRepository:
    """Tests for WorkflowRepository methods."""

    @pytest.fixture
    def mock_session(self):
        """Create a mock database session."""
        session = AsyncMock()
        session.execute = AsyncMock()
        session.flush = AsyncMock()
        return session

    @pytest.fixture
    def repository(self, mock_session):
        """Create repository with mock session.

        Uses org_id=None and is_superuser=True for system-level access
        (like old BaseRepository behavior).
        """
        return WorkflowRepository(mock_session, org_id=None, is_superuser=True)

    @pytest.fixture
    def mock_workflow(self):
        """Create a mock workflow object."""
        workflow = MagicMock()
        workflow.id = uuid4()
        workflow.name = "test-workflow"
        workflow.description = "Test workflow"
        workflow.category = "Testing"
        workflow.path = "/workspace/test.py"
        workflow.is_active = True
        workflow.endpoint_enabled = True
        workflow.api_key_hash = "abc123"
        workflow.api_key_enabled = True
        workflow.api_key_expires_at = None
        return workflow

    # ==========================================================================
    # resolve() tests
    # ==========================================================================

    async def test_resolve_by_uuid(self, repository, mock_workflow):
        """Test resolve() with a valid UUID string."""
        with patch.object(repository, 'get', return_value=mock_workflow) as mock_get:
            result = await repository.resolve(str(mock_workflow.id))

        assert result == mock_workflow
        mock_get.assert_called_once_with(id=mock_workflow.id)

    async def test_resolve_bare_name_returns_none(self, repository):
        """resolve() does not support bare names — only UUID and path::function_name."""
        with patch.object(repository, 'get') as mock_get:
            result = await repository.resolve("my_workflow")

        assert result is None
        mock_get.assert_not_called()

    async def test_resolve_uuid_not_found(self, repository):
        """Test resolve() returns None when UUID not found."""
        fake_uuid = str(uuid4())
        with patch.object(repository, 'get', return_value=None):
            result = await repository.resolve(fake_uuid)

        assert result is None

    async def test_resolve_by_path_ref(self, repository, mock_workflow):
        """Test resolve() with path::function_name format."""
        with patch.object(repository, '_resolve_by_path_ref', return_value=mock_workflow) as mock_resolve:
            result = await repository.resolve("workflows/customers.py::list_customers")

        assert result == mock_workflow
        mock_resolve.assert_called_once_with(
            "workflows/customers.py::list_customers", solution_scope=None
        )

    async def test_resolve_by_path_ref_with_feature_prefix(self, repository, mock_workflow):
        """Test resolve() with feature-prefixed path::function_name format."""
        ref = "features/project-management-demo/workflows/customers.py::list_customers_demo"
        with patch.object(repository, '_resolve_by_path_ref', return_value=mock_workflow) as mock_resolve:
            result = await repository.resolve(ref)

        assert result == mock_workflow
        mock_resolve.assert_called_once_with(ref, solution_scope=None)

    async def test_resolve_by_path_ref_not_found(self, repository, mock_session):
        """Test resolve() returns None when path::function_name not found."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        result = await repository.resolve("workflows/missing.py::nonexistent")

        assert result is None

    async def test_resolve_path_ref_unscoped_ambiguous_solution_rows_refused(
        self, repository, mock_session
    ):
        """An UNSCOPED caller resolving a path that matches 2+ solution rows and
        NO _repo/ row must get None — never an arbitrary install's workflow."""
        row_a = MagicMock()
        row_a.solution_id = uuid4()
        row_b = MagicMock()
        row_b.solution_id = uuid4()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [row_a, row_b]
        mock_session.execute.return_value = mock_result

        result = await repository.resolve("workflows/main.py::main")

        assert result is None

    async def test_resolve_path_ref_unscoped_single_solution_row_resolves(
        self, repository, mock_session
    ):
        """Regression guard: exactly ONE visible solution row (no _repo/ row)
        still resolves for an unscoped caller — nothing ambiguous about it."""
        row = MagicMock()
        row.solution_id = uuid4()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [row]
        mock_session.execute.return_value = mock_result

        result = await repository.resolve("workflows/main.py::main")

        assert result == row

    async def test_resolve_path_ref_prefers_over_name_lookup(self, repository, mock_workflow):
        """Test that :: in identifier triggers path ref lookup, not name lookup."""
        with patch.object(repository, '_resolve_by_path_ref', return_value=mock_workflow) as mock_path_ref:
            with patch.object(repository, 'get') as mock_get:
                result = await repository.resolve("some/path.py::func")

        # Should use path ref, not name lookup
        mock_path_ref.assert_called_once()
        mock_get.assert_not_called()
        assert result == mock_workflow

    # ==========================================================================
    # get_by_name() tests
    # ==========================================================================

    async def test_get_by_name_found(self, repository, mock_session, mock_workflow):
        """Test getting workflow by name when found."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_workflow
        mock_session.execute.return_value = mock_result

        result = await repository.get_by_name("test-workflow")

        assert result == mock_workflow
        mock_session.execute.assert_called_once()

    async def test_get_by_name_not_found(self, repository, mock_session):
        """Test getting workflow by name when not found."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        result = await repository.get_by_name("nonexistent")

        assert result is None

    async def test_get_all_active(self, repository, mock_session, mock_workflow):
        """Test getting all active workflows."""
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [mock_workflow]
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute.return_value = mock_result

        result = await repository.get_all_active()

        assert len(result) == 1
        assert result[0] == mock_workflow

    async def test_get_endpoint_enabled(self, repository, mock_session, mock_workflow):
        """Test getting workflows with endpoint enabled."""
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [mock_workflow]
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute.return_value = mock_result

        result = await repository.get_endpoint_enabled()

        assert len(result) == 1
        assert result[0].endpoint_enabled is True

    async def test_count_active(self, repository, mock_session):
        """Test counting active workflows."""
        mock_result = MagicMock()
        mock_result.scalar.return_value = 5
        mock_session.execute.return_value = mock_result

        result = await repository.count_active()

        assert result == 5

    async def test_count_active_returns_zero_on_none(self, repository, mock_session):
        """Test count returns 0 when result is None."""
        mock_result = MagicMock()
        mock_result.scalar.return_value = None
        mock_session.execute.return_value = mock_result

        result = await repository.count_active()

        assert result == 0

    async def test_search_with_query(self, repository, mock_session, mock_workflow):
        """Test search with text query."""
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [mock_workflow]
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute.return_value = mock_result

        result = await repository.search(query="test")

        assert len(result) == 1

    async def test_search_with_category(self, repository, mock_session, mock_workflow):
        """Test search with category filter."""
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [mock_workflow]
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute.return_value = mock_result

        result = await repository.search(category="Testing")

        assert len(result) == 1

    async def test_get_by_api_key_hash(self, repository, mock_session, mock_workflow):
        """Test getting workflow by API key hash."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_workflow
        mock_session.execute.return_value = mock_result

        result = await repository.get_by_api_key_hash("abc123")

        assert result == mock_workflow

    async def test_validate_api_key_valid(self, repository, mock_session, mock_workflow):
        """Test validating a valid API key."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_workflow
        mock_session.execute.return_value = mock_result

        is_valid, workflow_id = await repository.validate_api_key("abc123")

        assert is_valid is True
        assert workflow_id == mock_workflow.id

    async def test_validate_api_key_expired(self, repository, mock_session, mock_workflow):
        """Test validating an expired API key."""
        mock_workflow.api_key_expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_workflow
        mock_session.execute.return_value = mock_result

        is_valid, workflow_id = await repository.validate_api_key("abc123")

        assert is_valid is False
        assert workflow_id is None

    async def test_validate_api_key_wrong_workflow(self, repository, mock_session, mock_workflow):
        """Test validating API key for wrong workflow (different ID)."""
        from uuid import uuid4
        # When filtering by a different workflow_id, no result is found
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        is_valid, workflow_id = await repository.validate_api_key(
            "abc123", workflow_id=uuid4()
        )

        assert is_valid is False
        assert workflow_id is None

    async def test_validate_api_key_not_found(self, repository, mock_session):
        """Test validating API key that doesn't exist."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        is_valid, workflow_id = await repository.validate_api_key("nonexistent")

        assert is_valid is False
        assert workflow_id is None

    async def test_set_api_key(self, repository, mock_session, mock_workflow):
        """Test setting API key for a workflow."""
        # Mock get() method (OrgScopedRepository uses get(id=...) not get_by_id)
        with patch.object(repository, 'get', return_value=mock_workflow):
            result = await repository.set_api_key(
                workflow_id=mock_workflow.id,
                key_hash="new_hash",
                description="Test key",
                created_by="admin",
            )

        assert result == mock_workflow
        assert mock_workflow.api_key_hash == "new_hash"
        assert mock_workflow.api_key_enabled is True
        mock_session.flush.assert_called_once()

    async def test_revoke_api_key(self, repository, mock_session, mock_workflow):
        """Test revoking API key."""
        # Mock get() method (OrgScopedRepository uses get(id=...) not get_by_id)
        with patch.object(repository, 'get', return_value=mock_workflow):
            result = await repository.revoke_api_key(mock_workflow.id)

        assert result == mock_workflow
        assert mock_workflow.api_key_enabled is False
        mock_session.flush.assert_called_once()
