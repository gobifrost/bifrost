"""E2E tests for ``src.services.solution_files``.

Exercises write → enumerate → read round-trip and replace/skip semantics.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from uuid import uuid4
from src.models.orm.organizations import Organization
from src.models.orm.solutions import Solution
from src.services.solution_files import (
    enumerate_solution_files,
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
