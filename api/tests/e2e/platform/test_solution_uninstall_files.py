"""E2E: uninstall (DELETE /api/solutions/{id}) orphans files to the org (O3-revised).

Verifies:
- Files survive the Solution delete — the ondelete=CASCADE FK did NOT wipe them.
- Metadata is re-stamped: solution_id=NULL, organization_id set, origin_solution_*
  set, orphaned_at set.
- A SolutionFileJob(kind='orphan') is enqueued automatically and completes.
- After the job: S3 object is at the org-scoped key, NOT the install-scoped key.
"""
from __future__ import annotations

import time
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

from shared.file_paths import resolve_s3_key
from src.models.orm.file_metadata import FileMetadata
from src.models.orm.organizations import Organization
from src.models.orm.solutions import Solution
from src.models.orm.solution_file_jobs import SolutionFileJob
from src.services.file_storage import FileStorageService
from src.services.solution_files import write_solution_file

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_solution(e2e_client, headers, *, slug: str, org_id: str) -> str:
    """Create an org-scoped solution and return its id."""
    resp = e2e_client.post(
        "/api/solutions",
        headers=headers,
        json={"slug": slug, "name": slug.upper(), "organization_id": org_id},
    )
    assert resp.status_code in (200, 201), f"create solution: {resp.status_code} {resp.text}"
    return resp.json()["id"]


def _poll_file_job(e2e_client, headers, job_id: str, *, timeout_s: float = 30.0) -> dict:
    """Poll GET /api/solutions/file-jobs/{id} until terminal; return final body."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        r = e2e_client.get(f"/api/solutions/file-jobs/{job_id}", headers=headers)
        assert r.status_code == 200, f"poll: {r.status_code} {r.text}"
        body = r.json()
        if body["status"] in ("succeeded", "failed"):
            return body
        time.sleep(0.5)
    pytest.fail(f"file job {job_id} did not reach terminal state within {timeout_s}s")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def org_for_uninstall(db_session):
    """Dedicated Organization for uninstall-files tests."""
    o = Organization(
        id=uuid.uuid4(),
        name=f"uninstall-files-{uuid.uuid4().hex[:6]}",
        created_by="test",
    )
    db_session.add(o)
    await db_session.flush()
    return o


@pytest_asyncio.fixture
async def solution_with_file(db_session, org_for_uninstall):
    """A Solution with one written file; returns (solution, file_path, file_content)."""
    s = Solution(
        id=uuid.uuid4(),
        slug=f"uninstall-files-{uuid.uuid4().hex[:6]}",
        name="Uninstall Files Test",
        organization_id=org_for_uninstall.id,
    )
    db_session.add(s)
    await db_session.flush()

    location = "shared"
    path = f"test/{uuid.uuid4().hex}.txt"
    content = b"uninstall orphan test content"

    await write_solution_file(db_session, s.id, location, path, content, mode="replace")
    # commit so the file is visible to the HTTP layer
    await db_session.commit()
    return s, location, path, content


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_uninstall_orphans_file_metadata_row_survives(
    e2e_client, platform_admin, db_session, solution_with_file
):
    """Key cascade safety assertion: the file metadata row MUST survive Solution delete.

    The in-txn restamp nulls ``solution_id`` BEFORE ``ctx.db.delete(sol)``,
    so the ondelete=CASCADE FK can no longer reach the row.
    """
    sol, location, path, content = solution_with_file
    headers = platform_admin.headers
    sol_id = str(sol.id)
    org_id = sol.organization_id

    # Confirm the file row exists and is solution-scoped before delete.
    row_before = (
        await db_session.execute(
            select(FileMetadata).where(
                FileMetadata.solution_id == sol.id,
                FileMetadata.location == location,
                FileMetadata.path == path,
            )
        )
    ).scalar_one_or_none()
    assert row_before is not None, "pre-condition: file row must exist before delete"

    # Capture sol attributes before rollback invalidates the ORM object.
    sol_uuid = sol.id
    sol_slug = sol.slug

    # DELETE the solution.
    del_resp = e2e_client.delete(f"/api/solutions/{sol_id}", headers=headers)
    assert del_resp.status_code in (200, 204), f"delete failed: {del_resp.status_code} {del_resp.text}"

    summary = del_resp.json()
    assert summary["solution_id"] == sol_id
    assert summary["files_orphaned"] == 1, (
        f"expected files_orphaned=1 in delete summary, got: {summary}"
    )

    # Expire the session cache so we see the committed DB state.
    await db_session.rollback()

    # The row MUST still exist (cascade did not wipe it).
    row_after = (
        await db_session.execute(
            select(FileMetadata).where(
                FileMetadata.organization_id == org_id,
                FileMetadata.location == location,
                FileMetadata.path == path,
            )
        )
    ).scalar_one_or_none()
    assert row_after is not None, (
        "file metadata row was wiped by the cascade — restamp must run BEFORE Solution delete"
    )

    # Metadata re-stamp assertions.
    assert row_after.solution_id is None, "solution_id must be NULL after orphan restamp"
    assert row_after.organization_id == org_id, "organization_id must be set to the install's org"
    assert row_after.origin_solution_id == sol_uuid, "origin_solution_id must be set"
    assert row_after.origin_solution_slug == sol_slug, "origin_solution_slug must be set"
    assert row_after.orphaned_at is not None, "orphaned_at must be set"


@pytest.mark.asyncio
async def test_uninstall_enqueues_s3_move_job(
    e2e_client, platform_admin, db_session, solution_with_file
):
    """DELETE enqueues a SolutionFileJob(kind='orphan') that moves the S3 bytes."""
    sol, location, path, content = solution_with_file
    headers = platform_admin.headers
    sol_id = str(sol.id)
    org_id = sol.organization_id

    # Capture old S3 key before delete.
    old_s3_key = resolve_s3_key(location, str(sol.id), path)

    # DELETE.
    del_resp = e2e_client.delete(f"/api/solutions/{sol_id}", headers=headers)
    assert del_resp.status_code in (200, 204), f"delete: {del_resp.status_code} {del_resp.text}"

    # A SolutionFileJob(kind='orphan') must have been created for this install.
    # Expire the cache and query the DB.
    await db_session.rollback()

    job_row = (
        await db_session.execute(
            select(SolutionFileJob).where(
                SolutionFileJob.install_id == sol.id,
                SolutionFileJob.kind == "orphan",
            )
        )
    ).scalar_one_or_none()
    assert job_row is not None, "SolutionFileJob(kind='orphan') must be created on uninstall"
    job_id = str(job_row.id)

    # Poll the job until it succeeds.
    final = _poll_file_job(e2e_client, headers, job_id)
    assert final["status"] == "succeeded", f"orphan job failed: {final}"
    assert final["result"]["files_orphaned"] == 1, (
        f"expected files_orphaned=1 in job result, got: {final['result']}"
    )

    # Expire cache again to see the job's DB writes.
    await db_session.rollback()

    # S3: bytes must be at the org-scoped key.
    new_s3_key = resolve_s3_key(location, str(org_id), path)
    storage = FileStorageService(db_session)
    new_bytes = await storage.read_uploaded_file(new_s3_key)
    assert new_bytes == content, (
        f"expected {content!r} at org-scoped key {new_s3_key!r}, got {new_bytes!r}"
    )

    # S3: old install-scoped key must be gone.
    with pytest.raises(Exception):
        await storage.read_uploaded_file(old_s3_key)

    # DB: s3_key updated to the new org-scoped key.
    row = (
        await db_session.execute(
            select(FileMetadata).where(
                FileMetadata.organization_id == org_id,
                FileMetadata.location == location,
                FileMetadata.path == path,
            )
        )
    ).scalar_one_or_none()
    assert row is not None
    assert row.s3_key == new_s3_key, (
        f"s3_key not updated by job: expected {new_s3_key!r}, got {row.s3_key!r}"
    )
