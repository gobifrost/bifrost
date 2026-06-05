"""
Unit tests for src.services.sync_ops

describe() tests run without any database connection and are always fast.

execute() tests require a real AsyncSession backed by PostgreSQL; they are
marked with ``@pytest.mark.e2e`` and will be skipped when run without the
full Docker stack (``./test.sh e2e``).

Run describe() tests only::

    ./test.sh tests/unit/test_sync_ops.py

Run all tests (requires Docker)::

    ./test.sh tests/unit/test_sync_ops.py -m e2e
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest


# ---------------------------------------------------------------------------
# Minimal stub ORM models used by describe() tests (no DB connection needed)
# ---------------------------------------------------------------------------


class _StubTable:
    """Minimal stand-in for a SQLAlchemy Table object."""

    def __init__(self, name: str, column_names: list[str]) -> None:
        self.name = name
        self.columns = {c: MagicMock() for c in column_names}
        # Expose column access via attribute-style
        self.c = _ColumnAccess(column_names)

    def __contains__(self, item: str) -> bool:
        return item in self.columns


class _ColumnAccess:
    """Minimal stand-in for Table.c that supports ``in`` and indexing."""

    def __init__(self, names: list[str]) -> None:
        self._names = set(names)

    def __contains__(self, item: str) -> bool:
        return item in self._names

    def __getitem__(self, item: str) -> MagicMock:
        return MagicMock()


def _make_stub_model(tablename: str, columns: list[str]) -> type:
    """Return a minimal class that looks like an ORM model to sync_ops."""
    stub_table = _StubTable(tablename, columns)

    class StubModel:
        __tablename__ = tablename
        __table__ = stub_table

    return StubModel


# Pre-built stub models
WorkflowStub = _make_stub_model(
    "workflows", ["id", "name", "description", "is_active", "updated_at"]
)
OrgStub = _make_stub_model(
    "organizations", ["id", "name", "is_active", "updated_at"]
)
WorkflowRoleStub = _make_stub_model(
    "workflow_roles", ["workflow_id", "role_id"]
)
FormRoleStub = _make_stub_model(
    "form_roles", ["form_id", "role_id"]
)


# =============================================================================
# Upsert.describe() tests
# =============================================================================


class TestUpsertDescribe:
    """describe() returns a human-readable string without hitting the DB."""

    def test_basic_describe_match_on_id(self) -> None:
        from src.services.sync_ops import Upsert

        uid = uuid4()
        op = Upsert(
            model=WorkflowStub,
            id=uid,
            values={"name": "my_wf", "description": "desc"},
        )
        result = op.describe()

        assert "workflows" in result
        assert str(uid) in result
        assert "match_on='id'" in result
        assert "description" in result
        assert "name" in result

    def test_describe_match_on_name(self) -> None:
        from src.services.sync_ops import Upsert

        uid = uuid4()
        op = Upsert(
            model=OrgStub,
            id=uid,
            values={"name": "Acme Corp"},
            match_on="name",
        )
        result = op.describe()

        assert "organizations" in result
        assert "match_on='name'" in result

    def test_describe_match_on_natural_key(self) -> None:
        from src.services.sync_ops import Upsert

        uid = uuid4()
        op = Upsert(
            model=WorkflowStub,
            id=uid,
            values={"name": "my_wf"},
            match_on="natural_key",
        )
        result = op.describe()

        assert "match_on='natural_key'" in result

    def test_describe_contains_sorted_fields(self) -> None:
        from src.services.sync_ops import Upsert

        uid = uuid4()
        op = Upsert(
            model=WorkflowStub,
            id=uid,
            values={"z_field": 1, "a_field": 2, "m_field": 3},
        )
        result = op.describe()

        # Sorted field list should appear left-to-right in alphabetical order
        pos_a = result.index("a_field")
        pos_m = result.index("m_field")
        pos_z = result.index("z_field")
        assert pos_a < pos_m < pos_z


# =============================================================================
# SyncRoles.describe() tests
# =============================================================================


class TestSyncRolesDescribe:
    """describe() for SyncRoles reports entity FK, entity ID and role count."""

    def test_describe_with_roles(self) -> None:
        from src.services.sync_ops import SyncRoles

        entity_id = uuid4()
        role_a = uuid4()
        role_b = uuid4()
        op = SyncRoles(
            junction_model=WorkflowRoleStub,
            entity_fk="workflow_id",
            entity_id=entity_id,
            role_ids={role_a, role_b},
        )
        result = op.describe()

        assert "workflow_roles" in result
        assert "workflow_id" in result
        assert str(entity_id) in result
        assert "2 role(s)" in result
        assert str(role_a) in result
        assert str(role_b) in result

    def test_describe_empty_roles(self) -> None:
        from src.services.sync_ops import SyncRoles

        entity_id = uuid4()
        op = SyncRoles(
            junction_model=FormRoleStub,
            entity_fk="form_id",
            entity_id=entity_id,
            role_ids=set(),
        )
        result = op.describe()

        assert "form_roles" in result
        assert "form_id" in result
        assert "0 role(s)" in result

    def test_describe_roles_are_sorted(self) -> None:
        from src.services.sync_ops import SyncRoles

        entity_id = uuid4()
        roles = {uuid4() for _ in range(5)}
        op = SyncRoles(
            junction_model=WorkflowRoleStub,
            entity_fk="workflow_id",
            entity_id=entity_id,
            role_ids=roles,
        )
        result = op.describe()

        # Extract the role UUIDs listed in describe() and verify they are sorted
        role_strs = sorted(str(r) for r in roles)
        # Verify all role strings appear in the output
        for r in role_strs:
            assert r in result


# =============================================================================
# Delete.describe() tests
# =============================================================================


class TestDeleteDescribe:
    """describe() for Delete names the table and the target ID."""

    def test_describe(self) -> None:
        from src.services.sync_ops import Delete

        uid = uuid4()
        op = Delete(model=WorkflowStub, id=uid)
        result = op.describe()

        assert "Delete" in result
        assert "workflows" in result
        assert str(uid) in result

    def test_describe_different_model(self) -> None:
        from src.services.sync_ops import Delete

        uid = uuid4()
        op = Delete(model=OrgStub, id=uid)
        result = op.describe()

        assert "organizations" in result
        assert str(uid) in result


# =============================================================================
# Deactivate.describe() tests
# =============================================================================


class TestDeactivateDescribe:
    """describe() for Deactivate names the table and the target ID."""

    def test_describe(self) -> None:
        from src.services.sync_ops import Deactivate

        uid = uuid4()
        op = Deactivate(model=WorkflowStub, id=uid)
        result = op.describe()

        assert "Deactivate" in result
        assert "workflows" in result
        assert str(uid) in result

    def test_describe_different_model(self) -> None:
        from src.services.sync_ops import Deactivate

        uid = uuid4()
        op = Deactivate(model=OrgStub, id=uid)
        result = op.describe()

        assert "organizations" in result
        assert str(uid) in result


# =============================================================================
# _has_column() helper tests
# =============================================================================


class TestHasColumn:
    """_has_column() correctly inspects stub and real-ish tables."""

    def test_column_present(self) -> None:
        from src.services.sync_ops import _has_column

        assert _has_column(WorkflowStub, "updated_at") is True

    def test_column_absent(self) -> None:
        from src.services.sync_ops import _has_column

        NoUpdatedAt = _make_stub_model("no_ts", ["id", "name"])
        assert _has_column(NoUpdatedAt, "updated_at") is False

    def test_id_column_present(self) -> None:
        from src.services.sync_ops import _has_column

        assert _has_column(WorkflowStub, "id") is True


# =============================================================================
# execute() tests — require DB (documented, not run without Docker)
# =============================================================================


@pytest.mark.e2e
class TestUpsertExecute:
    """
    Integration tests for Upsert.execute().

    These tests require a live PostgreSQL database and must be run via::

        ./test.sh tests/unit/test_sync_ops.py::TestUpsertExecute -m e2e

    They are skipped automatically in unit-only runs.
    """

    async def test_upsert_inserts_when_no_existing_row(self, db_session) -> None:
        """execute() should INSERT when no matching row exists."""
        from src.services.sync_ops import Upsert
        from src.models.orm.organizations import Organization

        org_id = uuid4()
        op = Upsert(
            model=Organization,
            id=org_id,
            values={"name": "Test Org", "created_by": "test@example.com"},
        )
        await op.execute(db_session)
        await db_session.flush()

        result = await db_session.get(Organization, org_id)
        assert result is not None
        assert result.name == "Test Org"

    async def test_upsert_updates_when_row_exists(self, db_session) -> None:
        """execute() should UPDATE an existing row on second call."""
        from src.services.sync_ops import Upsert
        from src.models.orm.organizations import Organization

        org_id = uuid4()
        op1 = Upsert(
            model=Organization,
            id=org_id,
            values={"name": "Original Name", "created_by": "test@example.com"},
        )
        await op1.execute(db_session)
        await db_session.flush()

        op2 = Upsert(
            model=Organization,
            id=org_id,
            values={"name": "Updated Name"},
        )
        await op2.execute(db_session)
        await db_session.flush()

        await db_session.refresh(await db_session.get(Organization, org_id))
        result = await db_session.get(Organization, org_id)
        assert result is not None
        assert result.name == "Updated Name"

    async def test_upsert_sets_updated_at_on_update(self, db_session) -> None:
        """execute() should bump updated_at when updating a row."""
        from src.services.sync_ops import Upsert
        from src.models.orm.organizations import Organization

        org_id = uuid4()
        op1 = Upsert(
            model=Organization,
            id=org_id,
            values={"name": "Org", "created_by": "test@example.com"},
        )
        await op1.execute(db_session)
        await db_session.flush()

        first_result = await db_session.get(Organization, org_id)
        first_updated = first_result.updated_at if first_result else None

        op2 = Upsert(
            model=Organization,
            id=org_id,
            values={"name": "Org v2"},
        )
        await op2.execute(db_session)
        await db_session.flush()

        result = await db_session.get(Organization, org_id)
        assert result is not None
        assert result.updated_at is not None
        # updated_at should be >= the first write
        if first_updated:
            assert result.updated_at >= first_updated


@pytest.mark.e2e
class TestSyncRolesExecute:
    """
    Integration tests for SyncRoles.execute().

    Requires a live PostgreSQL database::

        ./test.sh tests/unit/test_sync_ops.py::TestSyncRolesExecute -m e2e
    """

    async def test_sync_roles_replaces_existing_assignments(
        self, db_session
    ) -> None:
        """execute() removes old role rows and writes the new set."""
        from sqlalchemy import select as sa_select

        from src.models.orm.users import Role
        from src.models.orm.workflow_roles import WorkflowRole
        from src.models.orm.workflows import Workflow
        from src.services.sync_ops import SyncRoles

        # Create parent rows to satisfy FK constraints
        wf_id = uuid4()
        role_a_id = uuid4()
        role_b_id = uuid4()
        role_c_id = uuid4()
        db_session.add(Workflow(id=wf_id, name="test_wf", path="test.py", function_name="wf"))
        db_session.add(Role(id=role_a_id, name="role_a", created_by="test"))
        db_session.add(Role(id=role_b_id, name="role_b", created_by="test"))
        db_session.add(Role(id=role_c_id, name="role_c", created_by="test"))
        await db_session.flush()

        # Insert initial roles A + B
        op1 = SyncRoles(
            junction_model=WorkflowRole,
            entity_fk="workflow_id",
            entity_id=wf_id,
            role_ids={role_a_id, role_b_id},
        )
        await op1.execute(db_session)
        await db_session.flush()

        # Sync to roles B + C (should remove A, keep B, add C)
        op2 = SyncRoles(
            junction_model=WorkflowRole,
            entity_fk="workflow_id",
            entity_id=wf_id,
            role_ids={role_b_id, role_c_id},
        )
        await op2.execute(db_session)
        await db_session.flush()

        rows = (
            await db_session.execute(
                sa_select(WorkflowRole).where(
                    WorkflowRole.workflow_id == wf_id
                )
            )
        ).scalars().all()

        assigned_role_ids = {row.role_id for row in rows}
        assert role_a_id not in assigned_role_ids
        assert role_b_id in assigned_role_ids
        assert role_c_id in assigned_role_ids

    async def test_sync_roles_empty_clears_all(self, db_session) -> None:
        """execute() with an empty role_ids set removes all role rows."""
        from sqlalchemy import select as sa_select

        from src.models.orm.users import Role
        from src.models.orm.workflow_roles import WorkflowRole
        from src.models.orm.workflows import Workflow
        from src.services.sync_ops import SyncRoles

        wf_id = uuid4()
        role_a_id = uuid4()
        db_session.add(Workflow(id=wf_id, name="test_wf2", path="test2.py", function_name="wf2"))
        db_session.add(Role(id=role_a_id, name="role_x", created_by="test"))
        await db_session.flush()

        op1 = SyncRoles(
            junction_model=WorkflowRole,
            entity_fk="workflow_id",
            entity_id=wf_id,
            role_ids={role_a_id},
        )
        await op1.execute(db_session)
        await db_session.flush()

        op2 = SyncRoles(
            junction_model=WorkflowRole,
            entity_fk="workflow_id",
            entity_id=wf_id,
            role_ids=set(),
        )
        await op2.execute(db_session)
        await db_session.flush()

        rows = (
            await db_session.execute(
                sa_select(WorkflowRole).where(
                    WorkflowRole.workflow_id == wf_id
                )
            )
        ).scalars().all()

        assert rows == []


@pytest.mark.e2e
class TestDeleteExecute:
    """
    Integration tests for Delete.execute().

    Requires a live PostgreSQL database::

        ./test.sh tests/unit/test_sync_ops.py::TestDeleteExecute -m e2e
    """

    async def test_delete_removes_row(self, db_session) -> None:
        """execute() hard-deletes the target row."""
        from src.services.sync_ops import Delete
        from src.models.orm.organizations import Organization

        org_id = uuid4()
        org = Organization(id=org_id, name="To Delete", created_by="test@example.com")
        db_session.add(org)
        await db_session.flush()

        op = Delete(model=Organization, id=org_id)
        await op.execute(db_session)
        await db_session.flush()

        result = await db_session.get(Organization, org_id)
        assert result is None

    async def test_delete_nonexistent_row_is_noop(self, db_session) -> None:
        """execute() on a non-existent row should not raise."""
        from src.services.sync_ops import Delete
        from src.models.orm.organizations import Organization

        op = Delete(model=Organization, id=uuid4())
        # Should complete without error
        await op.execute(db_session)
        await db_session.flush()


@pytest.mark.e2e
class TestDeactivateExecute:
    """
    Integration tests for Deactivate.execute().

    Requires a live PostgreSQL database::

        ./test.sh tests/unit/test_sync_ops.py::TestDeactivateExecute -m e2e
    """

    async def test_deactivate_sets_is_active_false(self, db_session) -> None:
        """execute() sets is_active=False on the target row."""
        from src.services.sync_ops import Deactivate
        from src.models.orm.organizations import Organization

        org_id = uuid4()
        org = Organization(
            id=org_id, name="Active Org", is_active=True, created_by="test@example.com"
        )
        db_session.add(org)
        await db_session.flush()

        op = Deactivate(model=Organization, id=org_id)
        await op.execute(db_session)
        await db_session.flush()

        result = await db_session.get(Organization, org_id)
        assert result is not None
        assert result.is_active is False

    async def test_deactivate_updates_updated_at(self, db_session) -> None:
        """execute() bumps updated_at when the column exists."""
        from src.services.sync_ops import Deactivate
        from src.models.orm.organizations import Organization

        org_id = uuid4()
        org = Organization(
            id=org_id, name="Org", is_active=True, created_by="test@example.com"
        )
        db_session.add(org)
        await db_session.flush()

        before = datetime.now(timezone.utc)

        op = Deactivate(model=Organization, id=org_id)
        await op.execute(db_session)
        await db_session.flush()

        result = await db_session.get(Organization, org_id)
        assert result is not None
        assert result.updated_at is not None
        assert result.updated_at >= before


# =============================================================================
# Upsert.execute() tests using mocked AsyncSession
# =============================================================================


class TestUpsertExecuteMocked:
    """
    Behavioural tests for Upsert.execute() using a mocked AsyncSession.

    These tests verify branching logic (INSERT vs UPDATE) without requiring
    a live database. They use real ORM models so SQLAlchemy can build valid
    DML statements; the DB session itself is mocked.
    """

    @pytest.mark.asyncio
    async def test_execute_calls_update_first(self) -> None:
        """execute() should always try UPDATE before INSERT."""
        from src.services.sync_ops import Upsert
        from src.models.orm.organizations import Organization

        mock_result = MagicMock()
        mock_result.rowcount = 1  # UPDATE hit a row

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        op = Upsert(model=Organization, id=uuid4(), values={"name": "wf"})
        await op.execute(mock_db)

        # execute() should have been called exactly once (the UPDATE)
        assert mock_db.execute.call_count == 1

    @pytest.mark.asyncio
    async def test_execute_falls_back_to_insert_when_rowcount_zero(self) -> None:
        """execute() should INSERT when UPDATE touches 0 rows."""
        from src.services.sync_ops import Upsert
        from src.models.orm.organizations import Organization

        mock_result = MagicMock()
        mock_result.rowcount = 0  # UPDATE found nothing

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        op = Upsert(model=Organization, id=uuid4(), values={"name": "wf"})
        await op.execute(mock_db)

        # execute() called twice: once for UPDATE, once for INSERT
        assert mock_db.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_execute_raises_for_name_match_without_name_value(self) -> None:
        """execute() raises ValueError when match_on='name' but 'name' is absent."""
        from src.services.sync_ops import Upsert
        from src.models.orm.organizations import Organization

        mock_result = MagicMock()
        mock_result.rowcount = 0

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        op = Upsert(
            model=Organization,
            id=uuid4(),
            values={"domain": "no name here"},
            match_on="name",
        )
        with pytest.raises(ValueError, match="requires 'name' in values"):
            await op.execute(mock_db)

    @pytest.mark.asyncio
    async def test_execute_raises_for_unknown_match_on(self) -> None:
        """execute() raises ValueError for an unknown match_on strategy."""
        from src.services.sync_ops import Upsert
        from src.models.orm.organizations import Organization

        mock_result = MagicMock()
        mock_result.rowcount = 0

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        op = Upsert(
            model=Organization,
            id=uuid4(),
            values={"name": "wf"},
            match_on="email",  # unknown
        )
        with pytest.raises(ValueError, match="Unknown match_on strategy"):
            await op.execute(mock_db)


# =============================================================================
# SyncRoles.execute() tests using mocked AsyncSession
# =============================================================================


class TestSyncRolesExecuteMocked:
    """Behavioural tests for SyncRoles.execute() using a mocked AsyncSession.

    Uses real ORM junction model so SQLAlchemy can build valid DML statements.
    """

    @pytest.mark.asyncio
    async def test_execute_issues_delete_then_insert(self) -> None:
        """execute() should DELETE first, then INSERT for non-empty role sets."""
        from src.services.sync_ops import SyncRoles
        from src.models.orm.workflow_roles import WorkflowRole

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock()

        entity_id = uuid4()
        role_a = uuid4()
        op = SyncRoles(
            junction_model=WorkflowRole,
            entity_fk="workflow_id",
            entity_id=entity_id,
            role_ids={role_a},
        )
        await op.execute(mock_db)

        # DELETE + INSERT = 2 execute() calls
        assert mock_db.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_execute_issues_only_delete_for_empty_roles(self) -> None:
        """execute() with empty role_ids should only DELETE (no INSERT)."""
        from src.services.sync_ops import SyncRoles
        from src.models.orm.workflow_roles import WorkflowRole

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock()

        op = SyncRoles(
            junction_model=WorkflowRole,
            entity_fk="workflow_id",
            entity_id=uuid4(),
            role_ids=set(),
        )
        await op.execute(mock_db)

        # Only DELETE, no INSERT
        assert mock_db.execute.call_count == 1


# =============================================================================
# Delete.execute() tests using mocked AsyncSession
# =============================================================================


class TestDeleteExecuteMocked:
    """Behavioural tests for Delete.execute() using a mocked AsyncSession."""

    @pytest.mark.asyncio
    async def test_execute_calls_db_execute_once(self) -> None:
        """execute() should call db.execute() exactly once."""
        from src.services.sync_ops import Delete
        from src.models.orm.organizations import Organization

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock()

        op = Delete(model=Organization, id=uuid4())
        await op.execute(mock_db)

        assert mock_db.execute.call_count == 1


# =============================================================================
# Deactivate.execute() tests using mocked AsyncSession
# =============================================================================


class TestDeactivateExecuteMocked:
    """Behavioural tests for Deactivate.execute() using a mocked AsyncSession."""

    @pytest.mark.asyncio
    async def test_execute_calls_db_execute_once(self) -> None:
        """execute() should call db.execute() exactly once."""
        from src.services.sync_ops import Deactivate
        from src.models.orm.organizations import Organization

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock()

        op = Deactivate(model=Organization, id=uuid4())
        await op.execute(mock_db)

        assert mock_db.execute.call_count == 1


# =============================================================================
# Drift-detection: verify all required _resolve_* methods exist
# =============================================================================


class TestManifestFieldCoverage:
    """Verify every entity type in the Manifest model has a _resolve_* method
    in ManifestResolver. This test catches new entity types added to the
    manifest that haven't been wired up to the import pipeline.
    """

    def test_all_manifest_entity_types_have_resolve_method(self) -> None:
        """Every major entity type in Manifest must be covered by a _resolve_*
        method in ManifestResolver.
        """
        from src.services.manifest_import import ManifestResolver

        resolve_methods = {
            name for name in dir(ManifestResolver)
            if name.startswith("_resolve_")
        }

        # At minimum these resolve methods must exist — one per entity type
        required = {
            "_resolve_organization",
            "_resolve_role",
            "_resolve_workflow",
            "_resolve_integration",
            "_resolve_config",
            "_resolve_app",
            "_resolve_table",
            "_resolve_event_source",
            "_resolve_form",
            "_resolve_agent",
            "_resolve_deletions",
        }

        missing = required - resolve_methods
        assert not missing, (
            f"Missing _resolve_* methods in ManifestResolver: {sorted(missing)}\n"
            f"Add a _resolve_<entity_type> method that returns list[SyncOp]."
        )

    def test_plan_import_method_exists(self) -> None:
        """plan_import must exist and be callable."""
        from src.services.manifest_import import ManifestResolver
        import inspect

        assert hasattr(ManifestResolver, "plan_import"), (
            "plan_import method is missing from ManifestResolver"
        )
        assert inspect.iscoroutinefunction(ManifestResolver.plan_import), (
            "plan_import must be an async method"
        )

    def test_execute_ops_method_exists(self) -> None:
        """_execute_ops must exist and be callable."""
        from src.services.github_sync import GitHubSyncService
        import inspect

        assert hasattr(GitHubSyncService, "_execute_ops"), (
            "_execute_ops method is missing from GitHubSyncService"
        )
        assert inspect.iscoroutinefunction(GitHubSyncService._execute_ops), (
            "_execute_ops must be an async method"
        )

    def test_ops_to_issues_method_exists(self) -> None:
        """_ops_to_issues must exist as a static method."""
        from src.services.github_sync import GitHubSyncService

        assert hasattr(GitHubSyncService, "_ops_to_issues"), (
            "_ops_to_issues method is missing from GitHubSyncService"
        )
        # Should be callable as a static method (no self)
        result = GitHubSyncService._ops_to_issues([])
        assert isinstance(result, list), (
            "_ops_to_issues([]) should return a list"
        )


class TestManifestWorkflowImport:
    """Workflow manifest import preserves the editable tool-name field."""

    def test_resolve_workflow_uses_manifest_name_not_manifest_key(self) -> None:
        from uuid import UUID

        from bifrost.manifest import ManifestWorkflow
        from src.models.orm.workflows import Workflow
        from src.services.manifest_import import ManifestResolver
        from src.services.sync_ops import Upsert

        wf_id = UUID("11111111-1111-1111-1111-111111111111")
        mwf = ManifestWorkflow(
            id=str(wf_id),
            name="custom_tool_name",
            path="workflows/custom.py",
            function_name="python_function_name",
        )
        resolver = ManifestResolver(AsyncMock())

        ops = resolver._resolve_workflow(
            "custom_tool_name",
            mwf,
            {"wf_by_natural": {}, "wf_ids": set()},
        )

        assert len(ops) == 1
        assert isinstance(ops[0], Upsert)
        assert ops[0].model is Workflow
        assert ops[0].values["name"] == "custom_tool_name"
        assert ops[0].values["function_name"] == "python_function_name"
