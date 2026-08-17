"""
Integration tests for MCP tool scoped entity lookups.

Tests that the remaining direct get_form MCP tool correctly handles the case
where entities with the same name exist in organization and global scopes.

Agent lookup moved to the canonical REST/RefResolver path and is covered by
``test_mcp_parity.py`` plus the REST access suite.

The fix ensures:
1. Name-based lookups prioritize org-specific entities over global entities
2. When only a global entity exists, it is returned
3. ID-based lookups work correctly with cascade filter (IDs are unique)

Issue: MultipleResultsFound error when same name exists in both scopes
Fix: Use ORDER BY organization_id DESC NULLS LAST LIMIT 1 for name-based lookups
"""

import pytest
import pytest_asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncGenerator
from unittest.mock import patch
from uuid import UUID, uuid4

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm import Form, Organization


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def _patch_db_context(db_session: AsyncSession):
    """Patch get_db_context so MCP tools use the test's db session and event loop."""
    @asynccontextmanager
    async def _use_test_session():
        yield db_session

    with patch("src.core.database.get_db_context", _use_test_session):
        yield


@pytest.fixture
def test_org_id() -> UUID:
    """A test organization ID."""
    return UUID("00000000-0000-4000-8000-000000000100")


@pytest.fixture
def other_org_id() -> UUID:
    """A different organization ID for cross-org tests."""
    return UUID("00000000-0000-4000-8000-000000000200")


@pytest_asyncio.fixture
async def test_organization(
    db_session: AsyncSession, test_org_id: UUID
) -> AsyncGenerator[Organization, None]:
    """Create the test organization required for org-scoped entities.

    Commits to make data visible to MCP tools which use separate sessions.
    Cleans up after test.
    """
    org = Organization(
        id=test_org_id,
        name="Test Organization",
        domain="test.example.com",
        is_active=True,
        is_provider=False,
        settings={},
        created_by="test@example.com",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(org)
    await db_session.commit()

    yield org

    # Cleanup - delete forms first (FK constraints)
    await db_session.execute(delete(Form).where(Form.organization_id == test_org_id))
    await db_session.execute(delete(Organization).where(Organization.id == test_org_id))
    await db_session.commit()


@pytest_asyncio.fixture
async def global_form(db_session: AsyncSession) -> AsyncGenerator[Form, None]:
    """Create a global form (organization_id = None)."""
    form_id = uuid4()
    form = Form(
        id=form_id,
        name="shared_form",
        description="A global form accessible to all orgs",
        workflow_id=None,
        access_level="role_based",
        organization_id=None,  # Global
        is_active=True,
        created_by="test@example.com",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(form)
    await db_session.commit()

    yield form

    # Cleanup
    await db_session.execute(delete(Form).where(Form.id == form_id))
    await db_session.commit()


@pytest_asyncio.fixture
async def org_form(
    db_session: AsyncSession, test_org_id: UUID, test_organization: Organization
) -> AsyncGenerator[Form, None]:
    """Create an org-scoped form with the same name as global_form."""
    form_id = uuid4()
    form = Form(
        id=form_id,
        name="shared_form",  # Same name as global_form
        description="An org-specific form",
        workflow_id=None,
        access_level="role_based",
        organization_id=test_org_id,  # Org-scoped
        is_active=True,
        created_by="test@example.com",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(form)
    await db_session.commit()

    yield form

    # Cleanup
    await db_session.execute(delete(Form).where(Form.id == form_id))
    await db_session.commit()


@pytest_asyncio.fixture
async def global_only_form(db_session: AsyncSession) -> AsyncGenerator[Form, None]:
    """Create a global form with a unique name."""
    form_id = uuid4()
    form = Form(
        id=form_id,
        name="unique_global_form",
        description="A global form with unique name",
        workflow_id=None,
        access_level="role_based",
        organization_id=None,  # Global
        is_active=True,
        created_by="test@example.com",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(form)
    await db_session.commit()

    yield form

    # Cleanup
    await db_session.execute(delete(Form).where(Form.id == form_id))
    await db_session.commit()


# =============================================================================
# Mock MCP Context
# =============================================================================


class MockMCPContext:
    """Mock MCP context for testing tool functions."""

    def __init__(
        self,
        user_id: UUID | str,
        org_id: UUID | str | None = None,
        is_platform_admin: bool = False,
        is_external: bool = False,
        user_email: str = "test@example.com",
        user_name: str = "Test User",
    ):
        self.user_id = user_id
        self.org_id = org_id
        self.is_platform_admin = is_platform_admin
        self.is_external = is_external
        self.user_email = user_email
        self.user_name = user_name
        self.accessible_namespaces: list[str] = []
        self.session = None


# =============================================================================
# Form Tests
# =============================================================================


class TestGetFormScopedLookup:
    """Tests for get_form MCP tool with scoped lookups."""

    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_get_form_by_name_returns_org_specific_when_both_exist(
        self,
        db_session: AsyncSession,
        global_form: Form,
        org_form: Form,
        test_org_id: UUID,
    ):
        """
        When same name exists in both org and global scope,
        should return org-specific form.
        """
        from src.services.mcp_server.tools.forms import get_form

        # Create context for org user
        context = MockMCPContext(
            user_id=uuid4(),
            org_id=test_org_id,
            is_platform_admin=False,
        )

        # Call get_form by name
        result = await get_form(context, form_name="shared_form")
        data = result.structured_content

        # Should return org-specific form, not global
        assert "error" not in data
        assert data["id"] == str(org_form.id)
        assert data["organization_id"] == str(test_org_id)
        assert data["description"] == "An org-specific form"

    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_get_form_by_name_returns_global_when_only_global_exists(
        self,
        db_session: AsyncSession,
        global_only_form: Form,
        test_org_id: UUID,
        test_organization: Organization,
    ):
        """
        When only global form exists with the name,
        should return the global form.
        """
        from src.services.mcp_server.tools.forms import get_form

        # Create context for org user
        context = MockMCPContext(
            user_id=uuid4(),
            org_id=test_org_id,
            is_platform_admin=False,
        )

        # Call get_form by name
        result = await get_form(context, form_name="unique_global_form")
        data = result.structured_content

        # Should return global form
        assert "error" not in data
        assert data["id"] == str(global_only_form.id)
        assert data["organization_id"] is None  # Global form
        assert data["description"] == "A global form with unique name"

    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_get_form_by_id_works_with_cascade_filter(
        self,
        db_session: AsyncSession,
        org_form: Form,
        test_org_id: UUID,
    ):
        """
        ID-based lookup should work correctly with cascade filter.
        """
        from src.services.mcp_server.tools.forms import get_form

        # Create context for org user
        context = MockMCPContext(
            user_id=uuid4(),
            org_id=test_org_id,
            is_platform_admin=False,
        )

        # Call get_form by ID
        result = await get_form(context, form_id=str(org_form.id))
        data = result.structured_content

        # Should return the form by ID
        assert "error" not in data
        assert data["id"] == str(org_form.id)

    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_get_form_by_name_no_org_context_returns_global_only(
        self,
        db_session: AsyncSession,
        global_form: Form,
        org_form: Form,
    ):
        """
        When user has no org context (not platform admin),
        should only return global form.
        """
        from src.services.mcp_server.tools.forms import get_form

        # Create context for user with no org (and not platform admin)
        context = MockMCPContext(
            user_id=uuid4(),
            org_id=None,
            is_platform_admin=False,
        )

        # Call get_form by name
        result = await get_form(context, form_name="shared_form")
        data = result.structured_content

        # Should return global form (can't access org-specific)
        assert "error" not in data
        assert data["id"] == str(global_form.id)
        assert data["organization_id"] is None

    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_get_form_platform_admin_sees_all(
        self,
        db_session: AsyncSession,
        global_form: Form,
        org_form: Form,
    ):
        """
        Platform admin should be able to access both forms.
        """
        from src.services.mcp_server.tools.forms import get_form

        # Create context for platform admin
        context = MockMCPContext(
            user_id=uuid4(),
            org_id=None,
            is_platform_admin=True,
        )

        # Platform admin by ID should work
        result = await get_form(context, form_id=str(org_form.id))
        data = result.structured_content
        assert "error" not in data
        assert data["id"] == str(org_form.id)


# =============================================================================
# Error Case Tests
# =============================================================================


class TestScopedLookupErrorCases:
    """Tests for error cases in scoped lookups."""

    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_get_form_not_found(
        self,
        db_session: AsyncSession,
        test_org_id: UUID,
        test_organization: Organization,
    ):
        """Should return error when form not found."""
        from src.services.mcp_server.tools.forms import get_form

        context = MockMCPContext(
            user_id=uuid4(),
            org_id=test_org_id,
            is_platform_admin=False,
        )

        result = await get_form(context, form_name="nonexistent_form")
        data = result.structured_content

        assert "error" in data
        assert "not found" in data["error"].lower()

    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_get_form_invalid_uuid(
        self,
        db_session: AsyncSession,
        test_org_id: UUID,
        test_organization: Organization,
    ):
        """Should return error for invalid UUID."""
        from src.services.mcp_server.tools.forms import get_form

        context = MockMCPContext(
            user_id=uuid4(),
            org_id=test_org_id,
            is_platform_admin=False,
        )

        result = await get_form(context, form_id="not-a-uuid")
        data = result.structured_content

        assert "error" in data
        assert "not a valid UUID" in data["error"]
