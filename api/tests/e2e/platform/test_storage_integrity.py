"""
Storage Integrity Tests — verify dual-write consistency across file_index + S3 + entity DB.

These tests exercise the FileIndexService and RepoStorage directly to ensure
that writes, updates, and deletes produce consistent state across all stores.
The reconciler is also tested for healing drift.
"""

import hashlib
import logging

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.models.orm.file_index import FileIndex
from src.models.orm.workflows import Workflow
from src.services.file_index_service import FileIndexService
from src.services.repo_storage import RepoStorage

logger = logging.getLogger(__name__)


# =============================================================================
# Fixtures
# =============================================================================


@pytest_asyncio.fixture
async def repo_storage():
    """Real RepoStorage against test S3-compatible storage."""
    settings = get_settings()
    return RepoStorage(settings)


@pytest_asyncio.fixture
async def file_index_svc(db_session: AsyncSession, repo_storage: RepoStorage):
    """Real FileIndexService with DB + S3."""
    return FileIndexService(db_session, repo_storage)


@pytest_asyncio.fixture(autouse=True)
async def cleanup_repo(db_session: AsyncSession, repo_storage: RepoStorage):
    """Wipe test paths from _repo/ and file_index between tests."""
    yield

    # Clean up file_index rows with test prefixes
    await db_session.execute(
        delete(FileIndex).where(
            FileIndex.path.like("test_storage_%")
        )
    )
    # Clean up any test workflows
    await db_session.execute(
        delete(Workflow).where(
            Workflow.path.like("test_storage_%")
        )
    )
    await db_session.commit()

    # Clean up S3 test objects
    try:
        paths = await repo_storage.list("test_storage_")
        for path in paths:
            await repo_storage.delete(path)
    except Exception as e:
        # Best-effort fixture cleanup - object storage may be unreachable / list may fail
        logger.debug(f"cleanup_repo S3 sweep skipped: {e}")


# =============================================================================
# Workflow Tests
# =============================================================================


SAMPLE_WORKFLOW = b'''\
from bifrost import workflow

@workflow(name="Storage Test Workflow")
def storage_test_workflow(message: str):
    """A test workflow for storage integrity."""
    return {"result": message}
'''

SAMPLE_WORKFLOW_UPDATED = b'''\
from bifrost import workflow

@workflow(name="Storage Test Workflow Updated")
def storage_test_workflow(message: str, count: int = 5):
    """Updated test workflow."""
    return {"result": message, "count": count}
'''


@pytest.mark.e2e
@pytest.mark.asyncio
class TestWorkflowStorageIntegrity:
    """Verify workflow writes are consistent across file_index + S3."""

    async def test_write_workflow_populates_file_index_and_s3(
        self,
        db_session: AsyncSession,
        file_index_svc: FileIndexService,
        repo_storage: RepoStorage,
    ):
        """Write a @workflow file → file_index has correct content/hash, S3 has matching content."""
        path = "test_storage_wf_write.py"
        content_hash = await file_index_svc.write(path, SAMPLE_WORKFLOW)

        # file_index should have the content
        result = await db_session.execute(
            select(FileIndex).where(FileIndex.path == path)
        )
        fi = result.scalar_one()
        assert fi.content == SAMPLE_WORKFLOW.decode("utf-8")
        assert fi.content_hash == content_hash

        # S3 should have matching content
        s3_content = await repo_storage.read(path)
        assert s3_content == SAMPLE_WORKFLOW

        # Hash should match SHA-256 of content
        expected_hash = hashlib.sha256(SAMPLE_WORKFLOW).hexdigest()
        assert content_hash == expected_hash

    async def test_update_workflow_preserves_identity(
        self,
        db_session: AsyncSession,
        file_index_svc: FileIndexService,
        repo_storage: RepoStorage,
    ):
        """Update same file → file_index content updated, S3 updated, path unchanged."""
        path = "test_storage_wf_update.py"

        # Write initial version
        hash1 = await file_index_svc.write(path, SAMPLE_WORKFLOW)

        # Write updated version
        hash2 = await file_index_svc.write(path, SAMPLE_WORKFLOW_UPDATED)

        # Hashes should differ
        assert hash1 != hash2

        # file_index should have updated content
        result = await db_session.execute(
            select(FileIndex).where(FileIndex.path == path)
        )
        fi = result.scalar_one()
        assert fi.content == SAMPLE_WORKFLOW_UPDATED.decode("utf-8")
        assert fi.content_hash == hash2

        # S3 should have updated content
        s3_content = await repo_storage.read(path)
        assert s3_content == SAMPLE_WORKFLOW_UPDATED

    async def test_delete_workflow_cleans_up(
        self,
        db_session: AsyncSession,
        file_index_svc: FileIndexService,
        repo_storage: RepoStorage,
    ):
        """Delete → file_index entry removed, S3 object removed."""
        path = "test_storage_wf_delete.py"

        # Write then delete
        await file_index_svc.write(path, SAMPLE_WORKFLOW)
        await file_index_svc.delete(path)

        # file_index should be empty
        result = await db_session.execute(
            select(FileIndex).where(FileIndex.path == path)
        )
        assert result.scalar_one_or_none() is None

        # S3 should not have the file
        exists = await repo_storage.exists(path)
        assert exists is False


# =============================================================================
# Module & Text File Tests
# =============================================================================


SAMPLE_MODULE = b'''\
"""Shared utility functions."""

def format_name(first: str, last: str) -> str:
    return f"{first} {last}"
'''

SAMPLE_TEXT = b"This is a plain text configuration file.\nLine 2.\n"


@pytest.mark.e2e
@pytest.mark.asyncio
class TestModuleStorageIntegrity:
    """Verify module and text file writes are consistent."""

    async def test_write_module_populates_file_index_and_s3(
        self,
        db_session: AsyncSession,
        file_index_svc: FileIndexService,
        repo_storage: RepoStorage,
    ):
        """Python without decorators → file_index has content, S3 has content, NOT in workflows."""
        path = "test_storage_modules/shared/utils.py"
        content_hash = await file_index_svc.write(path, SAMPLE_MODULE)

        # file_index should have content
        result = await db_session.execute(
            select(FileIndex).where(FileIndex.path == path)
        )
        fi = result.scalar_one()
        assert fi.content == SAMPLE_MODULE.decode("utf-8")
        assert fi.content_hash == content_hash

        # S3 should have matching content
        s3_content = await repo_storage.read(path)
        assert s3_content == SAMPLE_MODULE

        # Should NOT be in workflows table (no @workflow decorator)
        result = await db_session.execute(
            select(Workflow).where(Workflow.path == path)
        )
        assert result.scalar_one_or_none() is None

    async def test_write_text_file_populates_file_index_and_s3(
        self,
        db_session: AsyncSession,
        file_index_svc: FileIndexService,
        repo_storage: RepoStorage,
    ):
        """.txt → file_index has content, S3 has content."""
        path = "test_storage_readme.txt"
        content_hash = await file_index_svc.write(path, SAMPLE_TEXT)

        # file_index should have content
        result = await db_session.execute(
            select(FileIndex).where(FileIndex.path == path)
        )
        fi = result.scalar_one()
        assert fi.content == SAMPLE_TEXT.decode("utf-8")
        assert fi.content_hash == content_hash

        # S3 should have matching content
        s3_content = await repo_storage.read(path)
        assert s3_content == SAMPLE_TEXT


# =============================================================================
# Reconciler Tests
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
class TestReconciler:
    """Verify file_index reconciler heals drift between S3 and DB."""

    async def test_reconciler_adds_missing_file_index_entries(
        self,
        db_session: AsyncSession,
        repo_storage: RepoStorage,
    ):
        """Write directly to S3 → reconciler heals file_index."""
        from src.services.file_index_reconciler import reconcile_file_index

        path = "test_storage_reconcile_add.py"
        content = b"# Written directly to S3\nprint('hello')\n"

        # Write directly to S3, bypassing file_index
        await repo_storage.write(path, content)

        # Verify NOT in file_index yet
        result = await db_session.execute(
            select(FileIndex).where(FileIndex.path == path)
        )
        assert result.scalar_one_or_none() is None

        # Run reconciler
        stats = await reconcile_file_index(db_session, repo_storage)

        # file_index should now have the entry
        result = await db_session.execute(
            select(FileIndex).where(FileIndex.path == path)
        )
        fi = result.scalar_one()
        assert fi.content == content.decode("utf-8")
        assert fi.content_hash == hashlib.sha256(content).hexdigest()
        assert stats["added"] >= 1

    async def test_reconciler_removes_orphaned_entries(
        self,
        db_session: AsyncSession,
        repo_storage: RepoStorage,
    ):
        """Insert orphan file_index row with content → reconciler reverse-syncs to S3."""
        from sqlalchemy.dialects.postgresql import insert
        from src.services.file_index_reconciler import reconcile_file_index

        path = "test_storage_reconcile_orphan.py"

        # Insert orphan directly into file_index (no matching S3 object)
        stmt = insert(FileIndex).values(
            path=path,
            content="# Orphaned content",
            content_hash="deadbeef" * 4,
        ).on_conflict_do_nothing()
        await db_session.execute(stmt)
        await db_session.commit()

        # Verify it's in file_index
        result = await db_session.execute(
            select(FileIndex).where(FileIndex.path == path)
        )
        assert result.scalar_one_or_none() is not None

        # Run reconciler
        stats = await reconcile_file_index(db_session, repo_storage)

        # file_index should still have the entry (reverse-synced to S3)
        result = await db_session.execute(
            select(FileIndex).where(FileIndex.path == path)
        )
        assert result.scalar_one_or_none() is not None
        assert stats["reverse_synced"] >= 1

        # S3 should now have the content too
        s3_content = await repo_storage.read(path)
        assert s3_content == b"# Orphaned content"


# =============================================================================
# Multi-Entity Tests
# =============================================================================


SAMPLE_DUAL_DECORATOR = b'''\
from bifrost import workflow, data_provider

@workflow(name="Dual Test Workflow")
def dual_test_workflow(data: dict):
    """A workflow that also provides data."""
    return data

@data_provider(name="Dual Test Provider")
def dual_test_provider():
    """Data provider in the same file."""
    return [{"id": 1, "name": "test"}]
'''


@pytest.mark.e2e
@pytest.mark.asyncio
class TestMultiEntity:
    """Verify files with multiple entity decorators produce correct state."""

    async def test_file_with_workflow_and_data_provider(
        self,
        db_session: AsyncSession,
        file_index_svc: FileIndexService,
    ):
        """Both decorators → single file_index entry, content is stored."""
        path = "test_storage_dual.py"
        await file_index_svc.write(path, SAMPLE_DUAL_DECORATOR)

        # Single file_index entry
        result = await db_session.execute(
            select(FileIndex).where(FileIndex.path == path)
        )
        fi = result.scalar_one()
        assert fi.content == SAMPLE_DUAL_DECORATOR.decode("utf-8")

        # file_index stores the file once regardless of how many decorators
        # Entity detection happens at a higher level (file_ops indexers)
        count_result = await db_session.execute(
            select(FileIndex).where(FileIndex.path == path)
        )
        assert len(count_result.all()) == 1
