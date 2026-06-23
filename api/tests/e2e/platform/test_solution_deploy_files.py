"""E2E: SolutionDeployer writes bundle file sidecars (replace/skip, no mirror — O1).

Task 7 of solution-scoped-files (global Task 20):
  - Deploy a bundle WITH files → files are present under the install scope.
  - Redeploy with a file DROPPED from the bundle → the old file SURVIVES (O1 no-mirror).
  - replace mode overwrites; skip mode preserves a pre-existing (user-modified) file.
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from src.models.orm.organizations import Organization
from src.models.orm.solutions import Solution
from src.services.solution_files import (
    SolutionFileEntry,
    enumerate_solution_files,
    read_solution_file,
    write_solution_file,
)
from src.services.solutions.deploy import SolutionBundle, SolutionDeployer

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def org(db_session):
    """Real Organization row so Solution FK resolves."""
    o = Organization(id=uuid.uuid4(), name=f"deploy-files-{uuid.uuid4().hex[:6]}", created_by="t")
    db_session.add(o)
    await db_session.flush()
    return o


@pytest_asyncio.fixture
async def install(db_session, org):
    """Minimal Solution (install) row to own the bundle."""
    s = Solution(
        id=uuid.uuid4(),
        slug=f"deploy-files-{uuid.uuid4().hex[:6]}",
        name="Deploy Files Test",
        organization_id=org.id,
    )
    db_session.add(s)
    await db_session.flush()
    return s


def _make_bundle(install: Solution, files: list[SolutionFileEntry]) -> SolutionBundle:
    """Return a minimal SolutionBundle carrying the given file sidecars."""
    return SolutionBundle(solution=install, solution_files=files)


def _entry(location: str, path: str, content: bytes) -> SolutionFileEntry:
    return SolutionFileEntry(
        location=location,
        path=path,
        sha256=None,
        size=len(content),
        content_bytes=content,
    )


async def _deploy(db_session, bundle: SolutionBundle, *, file_mode: str = "replace") -> None:
    """Deploy the bundle and run finalize_s3 (the file-write phase)."""
    deployer = SolutionDeployer(db_session)
    result = await deployer.deploy(bundle, file_mode=file_mode)
    await db_session.commit()
    await result.finalize_s3()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deploy_bundle_with_files_writes_files(db_session, install):
    """Deploy a bundle carrying two file sidecars → both are present + readable."""
    content_a = b"alpha deploy file content"
    content_b = b"beta deploy file content"
    bundle = _make_bundle(install, [
        _entry("shared", f"deploy/{uuid.uuid4().hex}.txt", content_a),
        _entry("shared", f"deploy/{uuid.uuid4().hex}.txt", content_b),
    ])

    await _deploy(db_session, bundle)

    entries = await enumerate_solution_files(db_session, install.id)
    # Both sidecars must be present.
    assert len(entries) == 2

    # All entries are readable and carry the correct bytes.
    for entry in entries:
        data = await read_solution_file(db_session, install.id, entry.location, entry.path)
        assert data in (content_a, content_b), f"unexpected content for {entry.path!r}"


@pytest.mark.asyncio
async def test_redeploy_dropped_file_survives_o1(db_session, install):
    """O1 no-mirror: a file absent from a re-deploy bundle MUST survive.

    This is the key O1 assertion: the reconcile sweep that deletes stale
    entities (workflows, tables, etc.) must NOT apply to files — dropped
    files are preserved across redeployments.
    """
    kept_path = f"deploy/{uuid.uuid4().hex}.txt"
    dropped_path = f"deploy/{uuid.uuid4().hex}.txt"
    kept_content = b"keep me across redeployments"
    dropped_content = b"i was in the first bundle but not the second"

    # First deploy: two files.
    bundle1 = _make_bundle(install, [
        _entry("shared", kept_path, kept_content),
        _entry("shared", dropped_path, dropped_content),
    ])
    await _deploy(db_session, bundle1)

    entries_after_first = await enumerate_solution_files(db_session, install.id)
    assert len(entries_after_first) == 2

    # Second deploy: only one file (dropped_path is absent from the bundle).
    bundle2 = _make_bundle(install, [
        _entry("shared", kept_path, kept_content),
    ])
    await _deploy(db_session, bundle2)

    # O1: both files must still be present — dropped file must NOT be deleted.
    entries_after_second = await enumerate_solution_files(db_session, install.id)
    paths_after = {e.path for e in entries_after_second}
    assert kept_path in paths_after, "kept file was deleted by redeploy (O1 violation)"
    assert dropped_path in paths_after, (
        "dropped file was deleted by redeploy — O1 no-mirror is broken: "
        "files absent from the bundle MUST survive"
    )

    # The kept file's content is still correct.
    kept_bytes = await read_solution_file(db_session, install.id, "shared", kept_path)
    assert kept_bytes == kept_content


@pytest.mark.asyncio
async def test_replace_mode_overwrites_file(db_session, install):
    """file_mode='replace' (default) overwrites an existing file on redeploy."""
    path = f"deploy/{uuid.uuid4().hex}.txt"
    location = "shared"
    original_content = b"original content"
    updated_content = b"updated content after redeploy"

    # First deploy: write the file.
    bundle1 = _make_bundle(install, [_entry(location, path, original_content)])
    await _deploy(db_session, bundle1, file_mode="replace")

    data = await read_solution_file(db_session, install.id, location, path)
    assert data == original_content

    # Second deploy with replace: content is overwritten.
    bundle2 = _make_bundle(install, [_entry(location, path, updated_content)])
    await _deploy(db_session, bundle2, file_mode="replace")

    data_after = await read_solution_file(db_session, install.id, location, path)
    assert data_after == updated_content, (
        "replace mode must overwrite the existing file"
    )


@pytest.mark.asyncio
async def test_skip_mode_preserves_existing_file(db_session, install):
    """file_mode='skip' preserves an existing (e.g. user-modified) file on redeploy."""
    path = f"deploy/{uuid.uuid4().hex}.txt"
    location = "shared"
    user_content = b"user-modified content"
    bundle_content = b"bundle wants to overwrite but skip prevents it"

    # Write the 'user-modified' file directly (as if the user uploaded it).
    await write_solution_file(
        db_session, install.id, location, path, user_content, mode="replace"
    )

    # Deploy with skip: the user-modified content must be preserved.
    bundle = _make_bundle(install, [_entry(location, path, bundle_content)])
    await _deploy(db_session, bundle, file_mode="skip")

    data = await read_solution_file(db_session, install.id, location, path)
    assert data == user_content, (
        "skip mode must preserve the existing file; bundle overwrote it"
    )


@pytest.mark.asyncio
async def test_skip_mode_writes_new_file(db_session, install):
    """file_mode='skip' still writes a file that doesn't yet exist."""
    path = f"deploy/{uuid.uuid4().hex}.txt"
    location = "shared"
    content = b"new file via skip deploy"

    bundle = _make_bundle(install, [_entry(location, path, content)])
    await _deploy(db_session, bundle, file_mode="skip")

    data = await read_solution_file(db_session, install.id, location, path)
    assert data == content, "skip mode must write a new (non-existing) file"


@pytest.mark.asyncio
async def test_deploy_no_files_in_bundle_is_noop(db_session, install):
    """A bundle with no solution_files leaves any pre-existing files untouched."""
    path = f"deploy/{uuid.uuid4().hex}.txt"
    location = "shared"
    content = b"pre-existing file"

    # Write a file before the deploy.
    await write_solution_file(
        db_session, install.id, location, path, content, mode="replace"
    )

    # Deploy an empty bundle (no solution_files).
    empty_bundle = _make_bundle(install, [])
    await _deploy(db_session, empty_bundle)

    # The pre-existing file must still be there.
    data = await read_solution_file(db_session, install.id, location, path)
    assert data == content, "empty bundle deploy must not delete pre-existing files"
