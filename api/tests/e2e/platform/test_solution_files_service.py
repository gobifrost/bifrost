"""E2E tests for ``src.services.solution_files``.

Exercises write → enumerate → read round-trip, replace/skip semantics,
orphan-move (re-stamps metadata + moves S3 key), and the before_flush guard.

The guard test calls ``install_solution_write_guard()`` and then calls
``orphan_solution_files`` — proving the Core-only code path bypasses it.
An ORM mutation of a solution-managed row would raise ``SolutionManagedWriteError``.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from uuid import uuid4
from src.models.orm.organizations import Organization
from src.models.orm.solutions import Solution
from src.services.solution_files import (
    enumerate_solution_files,
    orphan_solution_files,
    read_solution_file,
    write_solution_file,
)

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def org(db_session):
    """A real Organization row so Solution FK resolves."""
    o = Organization(id=uuid4(), name=f"sf-test-{uuid4().hex[:6]}", created_by="t")
    db_session.add(o)
    await db_session.flush()
    return o


@pytest_asyncio.fixture
async def install(db_session, org):
    """A minimal Solution row (install) to own the files."""
    s = Solution(
        id=uuid4(),
        slug=f"sf-slug-{uuid4().hex[:6]}",
        name="SF Test Solution",
        organization_id=org.id,
    )
    db_session.add(s)
    await db_session.flush()
    return s


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_enumerate_read_roundtrip(db_session, install):
    """write → enumerate → read returns the original bytes."""
    content = b"hello from solution files"
    location = "shared"
    path = f"e2e/{uuid4().hex}.txt"

    written = await write_solution_file(
        db_session, install.id, location, path, content, mode="replace"
    )
    assert written is True

    entries = await enumerate_solution_files(db_session, install.id)
    assert any(e.location == location and e.path == path for e in entries)

    back = await read_solution_file(db_session, install.id, location, path)
    assert back == content


@pytest.mark.asyncio
async def test_replace_mode_overwrites(db_session, install):
    """mode='replace' always overwrites; returns True."""
    location = "shared"
    path = f"e2e/{uuid4().hex}.txt"

    await write_solution_file(
        db_session, install.id, location, path, b"original", mode="replace"
    )
    written = await write_solution_file(
        db_session, install.id, location, path, b"updated", mode="replace"
    )
    assert written is True

    back = await read_solution_file(db_session, install.id, location, path)
    assert back == b"updated"


@pytest.mark.asyncio
async def test_skip_mode_preserves_existing(db_session, install):
    """mode='skip' on an existing file returns False and leaves content unchanged."""
    location = "shared"
    path = f"e2e/{uuid4().hex}.txt"

    await write_solution_file(
        db_session, install.id, location, path, b"keep me", mode="replace"
    )
    written = await write_solution_file(
        db_session, install.id, location, path, b"ignore me", mode="skip"
    )
    assert written is False

    back = await read_solution_file(db_session, install.id, location, path)
    assert back == b"keep me"


@pytest.mark.asyncio
async def test_skip_mode_writes_when_absent(db_session, install):
    """mode='skip' on a NEW path still writes; returns True."""
    location = "shared"
    path = f"e2e/{uuid4().hex}.txt"

    written = await write_solution_file(
        db_session, install.id, location, path, b"new file", mode="skip"
    )
    assert written is True

    back = await read_solution_file(db_session, install.id, location, path)
    assert back == b"new file"


@pytest.mark.asyncio
async def test_orphan_move_restamps_and_moves_s3(db_session, install, org):
    """orphan_solution_files re-stamps metadata to org and moves the S3 key.

    After orphaning:
    - solution_id is None
    - organization_id equals org.id
    - origin_solution_id == install.id
    - origin_solution_slug == install.slug
    - orphaned_at is not None
    - Old S3 key is gone (FileNotFoundError)
    - New org-scoped key has the bytes.
    """
    from shared.file_paths import resolve_s3_key
    from src.models.orm.file_metadata import FileMetadata
    from src.services.file_storage import FileStorageService
    from sqlalchemy import select

    content = b"orphan me"
    location = "shared"
    path = f"e2e/{uuid4().hex}.txt"

    await write_solution_file(
        db_session, install.id, location, path, content, mode="replace"
    )

    # Capture the old S3 key before orphaning.
    old_s3_key = resolve_s3_key(location, str(install.id), path)

    count = await orphan_solution_files(
        db_session, install.id, org.id, install.slug
    )
    assert count == 1

    # Metadata row is re-stamped.
    row = (
        await db_session.execute(
            select(FileMetadata).where(
                FileMetadata.organization_id == org.id,
                FileMetadata.location == location,
                FileMetadata.path == path,
            )
        )
    ).scalar_one_or_none()
    assert row is not None
    assert row.solution_id is None
    assert row.organization_id == org.id
    assert row.origin_solution_id == install.id
    assert row.origin_solution_slug == install.slug
    assert row.orphaned_at is not None

    # New key is readable.
    new_s3_key = resolve_s3_key(location, str(org.id), path)
    storage = FileStorageService(db_session)
    new_bytes = await storage.read_uploaded_file(new_s3_key)
    assert new_bytes == content

    # Old key is gone.
    import pytest
    with pytest.raises(Exception):
        await storage.read_uploaded_file(old_s3_key)


@pytest.mark.asyncio
async def test_orphan_returns_count(db_session, install, org):
    """orphan_solution_files returns the number of files moved."""
    location = "shared"

    for i in range(3):
        await write_solution_file(
            db_session,
            install.id,
            location,
            f"e2e/{uuid4().hex}.txt",
            f"file {i}".encode(),
            mode="replace",
        )

    count = await orphan_solution_files(db_session, install.id, org.id, install.slug)
    assert count == 3


@pytest.mark.asyncio
async def test_orphan_empty_install_returns_zero(db_session, install, org):
    """orphan_solution_files on an install with no files returns 0."""
    count = await orphan_solution_files(db_session, install.id, org.id, install.slug)
    assert count == 0


@pytest.mark.asyncio
async def test_guard_active_core_writes_bypass(db_session, install, org):
    """Core writes bypass the before_flush guard — ORM mutations would not.

    This is the prod-faithfulness proof:
    - We install the session-wide before_flush backstop.
    - We write a solution-managed file.
    - We call orphan_solution_files (all Core UPDATE statements).
    - No SolutionManagedWriteError is raised → Core bypasses the guard.
    """
    from src.services.solutions.guard import install_solution_write_guard

    install_solution_write_guard()  # idempotent; installs the backstop

    location = "shared"
    path = f"e2e/{uuid4().hex}.txt"

    # Write phase — Core INSERT, guard should not fire.
    await write_solution_file(
        db_session, install.id, location, path, b"guard test", mode="replace"
    )

    # Orphan phase — Core UPDATE + S3 move, guard should not fire.
    count = await orphan_solution_files(
        db_session, install.id, org.id, install.slug
    )
    assert count == 1  # guard did not raise → Core path bypassed the backstop
