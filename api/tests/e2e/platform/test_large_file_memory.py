"""
Integration tests for memory behavior when writing large Python modules.

Tests that memory doesn't accumulate when writing multiple large files
sequentially, which was causing OOM in the scheduler (512Mi limit).
"""

import tracemalloc
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import FileIndex
from src.services.file_storage import FileStorageService
from tests.fixtures.large_module_generator import generate_large_module


@pytest_asyncio.fixture
async def clean_test_modules(db_session: AsyncSession):
    """Clean up test module files before and after each test."""
    await db_session.execute(
        delete(FileIndex).where(FileIndex.path.like("modules/test_mem_%"))
    )
    await db_session.commit()
    yield
    await db_session.execute(
        delete(FileIndex).where(FileIndex.path.like("modules/test_mem_%"))
    )
    await db_session.commit()


class TestLargeFileMemory:
    """Tests for memory behavior with large Python modules."""

    @pytest.mark.asyncio
    @patch("src.services.file_storage.file_ops.set_module", new_callable=AsyncMock)
    @patch("src.services.file_storage.file_ops.invalidate_module", new_callable=AsyncMock)
    async def test_sequential_writes_memory_bounded(
        self, _mock_invalidate, _mock_set, db_session: AsyncSession, clean_test_modules
    ):
        """
        Test that sequential large file writes don't accumulate memory.

        Writes multiple large modules (simulating halopsa.py, sageintacct.py, etc.)
        and verifies:
        1. Current memory after all writes stays low (memory released via db.expire)
        2. Peak memory stays under the scheduler limit (512MB)
        """
        file_storage = FileStorageService(db_session)

        # Write 3x 4MB modules, check peak and current stay bounded. This is the
        # original exposing condition: before the fix these three writes alone
        # retained more than 300MB, so a second smaller-file loop duplicated the
        # same contract while adding roughly half of this test's runtime.
        content_4mb = generate_large_module(target_size_mb=4.0).encode("utf-8")
        assert len(content_4mb) > 3 * 1024 * 1024

        tracemalloc.start()
        try:
            baseline = tracemalloc.get_traced_memory()[0]

            for name in ["test_mem_1.py", "test_mem_2.py", "test_mem_3.py"]:
                await file_storage.write_file(
                    path=f"modules/{name}",
                    content=content_4mb,
                    updated_by="test",
                    force_deactivation=True,
                )

            current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        growth = current - baseline
        print(f"baseline={baseline/1024/1024:.1f}MB, "
              f"current={current/1024/1024:.1f}MB, "
              f"peak={peak/1024/1024:.1f}MB, "
              f"growth={growth/1024/1024:.1f}MB")

        # Without the OOM fix, 3x 4MB files with AST parsing would use 300MB+.
        # With the fix, current memory stays bounded. Dual-write to file_index
        # adds ~12MB overhead, so threshold is 75MB (still 7x under 512MB limit).
        assert current < 75 * 1024 * 1024, f"Memory not released: {current/1024/1024:.1f}MB"
        assert peak < 450 * 1024 * 1024, f"Peak memory {peak/1024/1024:.1f}MB exceeds 450MB"
