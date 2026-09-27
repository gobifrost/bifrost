"""
Integration tests for workspace reindexing and orphaned workflow reconciliation.

Tests that orphaned workflows (those whose source files no longer exist)
are properly marked as inactive during workspace reindexing.
"""

import sys

import pytest
import pytest_asyncio
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Workflow, FileIndex


def get_file_storage_service(db_session):
    """
    Get a fresh FileStorageService with clean libcst state.

    This function clears the libcst module cache before importing FileStorageService
    to ensure the libcst parser is not in a corrupted state from previous tests.
    """
    # Remove all libcst modules from cache
    libcst_modules = [k for k in list(sys.modules.keys()) if k.startswith('libcst')]
    for mod in libcst_modules:
        del sys.modules[mod]

    # Also remove decorator_property_service to force reimport with fresh libcst
    service_key = 'src.services.decorator_property_service'
    if service_key in sys.modules:
        del sys.modules[service_key]

    # Remove file_storage to force reimport with fresh decorator_property_service
    storage_key = 'src.services.file_storage'
    if storage_key in sys.modules:
        del sys.modules[storage_key]

    # Now import FileStorageService fresh
    from src.services.file_storage import FileStorageService
    return FileStorageService(db_session)


# Use hardcoded test workspace path for isolation
TEST_WORKSPACE = Path("/tmp/bifrost/test_workspace_reindex")


@pytest_asyncio.fixture
async def clean_workspace():
    """Create a clean test workspace directory."""
    import shutil

    # Clean before test
    if TEST_WORKSPACE.exists():
        shutil.rmtree(TEST_WORKSPACE)
    TEST_WORKSPACE.mkdir(parents=True, exist_ok=True)

    yield TEST_WORKSPACE

    # Clean after test
    if TEST_WORKSPACE.exists():
        shutil.rmtree(TEST_WORKSPACE)


@pytest_asyncio.fixture
async def clean_tables(db_session: AsyncSession):
    """Fixture that cleans up test data BEFORE and AFTER each test.

    This ensures proper test isolation by:
    1. Cleaning up any leftover state from previous tests BEFORE the test runs
    2. Cleaning up state created by this test AFTER it completes

    We delete executions first to avoid FK violations when deleting workflows.
    """
    from sqlalchemy import delete
    from src.models import Execution
    from src.models.orm.services import ServiceDefinition

    def _test_workflow_ids():
        return select(Workflow.id).where(
            (Workflow.path.like("workflow_%")) |
            (Workflow.path.like("%_workflow.py")) |
            (Workflow.path.like("%_provider.py")) |
            (Workflow.path == "documented_workflow.py")
        )

    # SETUP: Clean up any leftover state from previous tests BEFORE this test
    # Delete executions first (they reference workflows via api_key_id)
    await db_session.execute(
        delete(Execution).where(
            Execution.api_key_id.in_(
                select(Workflow.id).where(
                    (Workflow.path.like("workflow_%")) |
                    (Workflow.path.like("%_workflow.py")) |
                    (Workflow.path.like("%_provider.py")) |
                    (Workflow.path == "documented_workflow.py")
                )
            )
        )
    )
    # Delete service definitions before their workflows (RESTRICT FK;
    # attempts and logs cascade from the definition rows).
    await db_session.execute(
        delete(ServiceDefinition).where(
            ServiceDefinition.workflow_id.in_(_test_workflow_ids())
        )
    )
    # Now delete workflows
    await db_session.execute(
        delete(Workflow).where(Workflow.path.like("workflow_%"))
    )
    await db_session.execute(
        delete(Workflow).where(Workflow.path.like("%_workflow.py"))
    )
    await db_session.execute(
        delete(Workflow).where(Workflow.path.like("%_provider.py"))
    )
    await db_session.execute(
        delete(Workflow).where(Workflow.path == "documented_workflow.py")
    )
    # Delete file_index entries
    await db_session.execute(
        delete(FileIndex).where(FileIndex.path.like("%_workflow.py"))
    )
    await db_session.execute(
        delete(FileIndex).where(FileIndex.path.like("%_provider.py"))
    )
    await db_session.execute(
        delete(FileIndex).where(FileIndex.path.like("%_file.py"))
    )
    await db_session.execute(
        delete(FileIndex).where(FileIndex.path == "documented_workflow.py")
    )
    await db_session.commit()

    # Track IDs created during test (for targeted cleanup if needed)
    created_data = {
        "workflow_ids": [],
        "file_index_paths": [],
    }

    yield created_data

    # TEARDOWN: Clean up state created by this test
    # Delete executions first (they reference workflows via api_key_id)
    await db_session.execute(
        delete(Execution).where(
            Execution.api_key_id.in_(
                select(Workflow.id).where(
                    (Workflow.path.like("workflow_%")) |
                    (Workflow.path.like("%_workflow.py")) |
                    (Workflow.path.like("%_provider.py")) |
                    (Workflow.path == "documented_workflow.py")
                )
            )
        )
    )
    # Delete service definitions before their workflows (RESTRICT FK).
    await db_session.execute(
        delete(ServiceDefinition).where(
            ServiceDefinition.workflow_id.in_(_test_workflow_ids())
        )
    )
    # Now delete workflows
    await db_session.execute(
        delete(Workflow).where(Workflow.path.like("workflow_%"))
    )
    await db_session.execute(
        delete(Workflow).where(Workflow.path.like("%_workflow.py"))
    )
    await db_session.execute(
        delete(Workflow).where(Workflow.path.like("%_provider.py"))
    )
    await db_session.execute(
        delete(Workflow).where(Workflow.path == "documented_workflow.py")
    )
    # Delete file_index entries
    await db_session.execute(
        delete(FileIndex).where(FileIndex.path.like("%_workflow.py"))
    )
    await db_session.execute(
        delete(FileIndex).where(FileIndex.path.like("%_provider.py"))
    )
    await db_session.execute(
        delete(FileIndex).where(FileIndex.path.like("%_file.py"))
    )
    await db_session.execute(
        delete(FileIndex).where(FileIndex.path == "documented_workflow.py")
    )
    await db_session.commit()


@pytest.mark.e2e
@pytest.mark.asyncio
class TestReindexWorkspaceFiles:
    """Integration tests for reindex_workspace_files."""

    async def test_orphaned_workflow_marked_inactive(
        self,
        db_session: AsyncSession,
        clean_workspace: Path,
        clean_tables,
    ):
        """
        Workflows whose source files no longer exist are marked inactive.
        Reindex is enrich-only: it updates existing DB records but does not
        create new ones.  We pre-register both workflows in the DB so reindex
        can reconcile them.
        """
        workspace = clean_workspace

        # 1. Create a workflow file AND a matching DB record (registered)
        workflow_file = workspace / "my_workflow.py"
        workflow_file.write_text("""
from bifrost import workflow

@workflow(name="Test Workflow")
def test_workflow():
    pass
""")
        active_workflow = Workflow(
            id=uuid4(),
            name="Test Workflow",
            function_name="test_workflow",
            path="my_workflow.py",
            is_active=True,
        )
        db_session.add(active_workflow)

        # 2. Create a workflow record in DB that references a non-existent file
        orphaned_workflow = Workflow(
            id=uuid4(),
            name="Orphaned Workflow",
            function_name="orphaned_func",
            path="orphaned_workflow.py",  # This file doesn't exist
            is_active=True,
        )
        db_session.add(orphaned_workflow)
        await db_session.commit()
        orphaned_id = orphaned_workflow.id
        active_id = active_workflow.id

        # 3. Call reindex_workspace_files to index the workspace
        storage = get_file_storage_service(db_session)
        counts = await storage.reindex_workspace_files(workspace)
        await db_session.commit()

        # 4. Verify the orphaned workflow is now inactive
        result = await db_session.execute(
            select(Workflow).where(Workflow.id == orphaned_id)
        )
        workflow = result.scalar_one()
        assert workflow.is_active is False

        # 5. Verify the registered workflow is still active
        result = await db_session.execute(
            select(Workflow).where(Workflow.id == active_id)
        )
        indexed_workflow = result.scalar_one()
        assert indexed_workflow is not None
        assert indexed_workflow.is_active is True

        # 6. Verify counts - at least 1 file indexed and 1 workflow deactivated
        assert counts["files_indexed"] == 1
        assert counts["workflows_deactivated"] >= 1, (
            f"Expected at least 1 deactivated workflow, got {counts['workflows_deactivated']}"
        )

    async def test_orphaned_data_provider_marked_inactive(
        self,
        db_session: AsyncSession,
        clean_workspace: Path,
        clean_tables,
    ):
        """
        Data providers whose source files no longer exist are marked inactive.

        NOTE: Data providers are now stored in the workflows table with type='data_provider'.
        The workflows_deactivated count includes data providers.
        """
        workspace = clean_workspace

        # 1. Create a data provider file
        provider_file = workspace / "my_provider.py"
        provider_file.write_text("""
from bifrost import data_provider

@data_provider(name="Test Provider")
def test_provider():
    return []
""")

        # 2. Create a data provider record in DB that references a non-existent file
        # Data providers are now stored in workflows table with type='data_provider'
        orphaned_provider = Workflow(
            id=uuid4(),
            name="Orphaned Provider",
            function_name="orphaned_func",
            path="orphaned_provider.py",  # This file doesn't exist
            type="data_provider",  # Mark as data provider
            is_active=True,
        )
        db_session.add(orphaned_provider)
        await db_session.commit()
        orphaned_id = orphaned_provider.id

        # 3. Call reindex_workspace_files
        storage = get_file_storage_service(db_session)
        counts = await storage.reindex_workspace_files(workspace)
        await db_session.commit()

        # 4. Verify the orphaned data provider is now inactive
        result = await db_session.execute(
            select(Workflow).where(Workflow.id == orphaned_id)
        )
        provider = result.scalar_one()
        assert provider.is_active is False
        assert provider.type == "data_provider"

        # 5. Verify counts - data providers are now included in workflows_deactivated
        # data_providers_deactivated is kept for backward compatibility but is always 0
        assert counts["workflows_deactivated"] == 1
        assert counts["data_providers_deactivated"] == 0

    async def test_file_index_table_updated(
        self,
        db_session: AsyncSession,
        clean_workspace: Path,
        clean_tables,
    ):
        """
        file_index table is updated to match actual files.
        Missing files are hard-deleted from file_index, present files are upserted.
        """
        workspace = clean_workspace

        # 1. Create a file_index record for a non-existent file
        from datetime import datetime, timezone
        orphaned_file = FileIndex(
            path="orphaned_file.py",
            content_hash="abc123",
            content="# old content",
            updated_at=datetime.now(timezone.utc),
        )
        db_session.add(orphaned_file)
        await db_session.commit()

        # 2. Create an actual file
        real_file = workspace / "real_file.py"
        real_file.write_text("# Real file content")

        # 3. Call reindex_workspace_files
        storage = get_file_storage_service(db_session)
        counts = await storage.reindex_workspace_files(workspace)
        await db_session.commit()

        # 4. Verify the orphaned file record is hard-deleted from file_index
        result = await db_session.execute(
            select(FileIndex).where(FileIndex.path == "orphaned_file.py")
        )
        orphaned = result.scalar_one_or_none()
        assert orphaned is None, "Orphaned file should be hard-deleted from file_index"

        # 5. Verify the real file is indexed
        result = await db_session.execute(
            select(FileIndex).where(FileIndex.path == "real_file.py")
        )
        real = result.scalar_one_or_none()
        assert real is not None, "Real file should exist in file_index"

        # 6. Verify counts
        assert counts["files_indexed"] == 1
        assert counts["files_removed"] == 1

    async def test_empty_workspace_deactivates_all(
        self,
        db_session: AsyncSession,
        clean_workspace: Path,
        clean_tables,
    ):
        """
        Empty workspace results in all test workflows being deactivated.
        """
        workspace = clean_workspace  # Empty directory

        # 1. Create some active workflows with specific IDs to track
        workflow_ids = []
        for i in range(3):
            workflow = Workflow(
                id=uuid4(),
                name=f"Workflow {i}",
                function_name=f"workflow_{i}",
                path=f"workflow_{i}.py",
                is_active=True,
            )
            db_session.add(workflow)
            workflow_ids.append(workflow.id)
        await db_session.commit()

        # 2. Call reindex_workspace_files
        storage = get_file_storage_service(db_session)
        counts = await storage.reindex_workspace_files(workspace)
        await db_session.commit()

        # 3. Verify our test workflows are now inactive
        result = await db_session.execute(
            select(Workflow).where(Workflow.id.in_(workflow_ids))
        )
        test_workflows = result.scalars().all()
        assert len(test_workflows) == 3, "All test workflows should still exist"
        for wf in test_workflows:
            assert wf.is_active is False, f"Workflow {wf.name} should be inactive"

        # 4. Verify counts - at least our 3 were deactivated
        # (may include more from other tests)
        assert counts["files_indexed"] == 0
        assert counts["workflows_deactivated"] >= 3, (
            f"Expected at least 3 deactivated, got {counts['workflows_deactivated']}"
        )

    async def test_metadata_extraction_updates_workflow(
        self,
        db_session: AsyncSession,
        clean_workspace: Path,
        clean_tables,
    ):
        """
        Workflow metadata is extracted from Python files during reindex.
        Reindex is enrich-only: it requires a pre-existing DB record to update.
        """
        workspace = clean_workspace

        # 1. Create a workflow file with specific metadata
        workflow_file = workspace / "documented_workflow.py"
        workflow_file.write_text("""
from bifrost import workflow

@workflow(
    name="Documented Workflow",
    description="A well-documented workflow",
    category="Testing",
    tags=["test", "example"],
)
def documented_workflow(message: str, count: int = 5):
    '''Process messages with count iterations.'''
    pass
""")

        # 2. Pre-register the workflow in DB (reindex is enrich-only)
        pre_wf = Workflow(
            id=uuid4(),
            name="documented_workflow",  # Will be enriched to "Documented Workflow"
            function_name="documented_workflow",
            path="documented_workflow.py",
            is_active=True,
        )
        db_session.add(pre_wf)
        await db_session.commit()

        # 3. Call reindex_workspace_files to enrich metadata
        storage = get_file_storage_service(db_session)
        await storage.reindex_workspace_files(workspace)
        await db_session.commit()

        # 4. Verify workflow was enriched with correct metadata
        await db_session.refresh(pre_wf)

        # The indexer only sets name when it is NULL; since pre_wf was created with
        # name="documented_workflow" (not None), the display name from the decorator
        # is not applied — name stays as the initial value.
        assert pre_wf.name == "documented_workflow"
        assert pre_wf.description == "A well-documented workflow"
        assert pre_wf.category == "Testing"
        assert pre_wf.is_active is True
        assert pre_wf.function_name == "documented_workflow"
        # Parameters should be extracted
        assert len(pre_wf.parameters_schema) == 2
