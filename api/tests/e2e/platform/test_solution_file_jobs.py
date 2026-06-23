"""End-to-end: SolutionFileJob enqueue + poll + C3 cascade safety.

Verifies:
- POST /api/solutions/file-jobs enqueues an orphan job and returns 202.
- GET /api/solutions/file-jobs/{id} polls to 'succeeded' with a count.
- C3: install_id is nullable with NO FK cascade to solutions — deleting the
  referenced Solution row does NOT delete the job row.
"""
from __future__ import annotations

import time
import uuid

import pytest

pytestmark = pytest.mark.e2e


def _create_solution(e2e_client, headers, *, slug: str) -> tuple[str, str | None]:
    """Create a solution and return (id, organization_id)."""
    resp = e2e_client.post(
        "/api/solutions",
        headers=headers,
        json={
            "slug": slug,
            "name": slug.upper(),
            "scope": "global",
            "global_repo_access": False,
        },
    )
    assert resp.status_code in (200, 201), f"create solution failed: {resp.status_code} {resp.text}"
    data = resp.json()
    return data["id"], data.get("organization_id")


def test_orphan_file_job_enqueue_and_poll(e2e_client, platform_admin, org1):
    """Enqueue an orphan job → poll → succeeded with files_orphaned count."""
    headers = platform_admin.headers
    slug = f"sol-filejob-{uuid.uuid4().hex[:8]}"
    sid, _ = _create_solution(e2e_client, headers, slug=slug)
    org_id = org1["id"]

    # Enqueue the orphan job (no files exist yet — count will be 0, but the
    # job must complete successfully).
    resp = e2e_client.post(
        "/api/solutions/file-jobs",
        headers=headers,
        json={
            "install_id": sid,
            "kind": "orphan",
            "org_id": org_id,
            "slug": slug,
        },
    )
    assert resp.status_code == 202, f"expected 202, got {resp.status_code}: {resp.text}"
    body = resp.json()
    job_id = body.get("file_job_id")
    assert job_id, f"missing file_job_id in response: {body}"

    # Poll until terminal.
    result_body = None
    job_status = None
    for _ in range(60):
        st = e2e_client.get(f"/api/solutions/file-jobs/{job_id}", headers=headers)
        assert st.status_code == 200, f"poll failed: {st.status_code} {st.text}"
        result_body = st.json()
        job_status = result_body["status"]
        if job_status in ("succeeded", "failed"):
            break
        time.sleep(0.5)

    assert job_status == "succeeded", f"file job did not succeed: {result_body}"
    assert result_body["install_id"] == sid
    assert result_body["kind"] == "orphan"
    # files_orphaned must be present (0 is fine — no files were written).
    assert "files_orphaned" in result_body["result"], (
        f"result missing files_orphaned: {result_body['result']}"
    )


def test_file_job_survives_solution_deletion(e2e_client, platform_admin, org1):
    """C3: deleting the Solution row must NOT cascade-delete the file job.

    The SolutionFileJob.install_id column has NO FK to solutions.  We verify
    the FK absence empirically: create a solution, enqueue an orphan job, delete
    the solution, then assert the job row is still retrievable.
    """
    headers = platform_admin.headers
    slug = f"sol-filejob-c3-{uuid.uuid4().hex[:8]}"
    sid, _ = _create_solution(e2e_client, headers, slug=slug)
    org_id = org1["id"]

    # Enqueue the job.
    resp = e2e_client.post(
        "/api/solutions/file-jobs",
        headers=headers,
        json={
            "install_id": sid,
            "kind": "orphan",
            "org_id": org_id,
            "slug": slug,
        },
    )
    assert resp.status_code == 202, f"enqueue failed: {resp.status_code} {resp.text}"
    job_id = resp.json()["file_job_id"]

    # Wait for the job to start (so it has a row with install_id set).
    for _ in range(20):
        st = e2e_client.get(f"/api/solutions/file-jobs/{job_id}", headers=headers)
        assert st.status_code == 200
        if st.json()["status"] != "queued":
            break
        time.sleep(0.3)

    # Delete the solution.
    del_resp = e2e_client.delete(f"/api/solutions/{sid}", headers=headers)
    # 200 or 204 depending on delete implementation.
    assert del_resp.status_code in (200, 204), (
        f"solution delete failed: {del_resp.status_code} {del_resp.text}"
    )

    # The job row must still exist — no cascade.
    st = e2e_client.get(f"/api/solutions/file-jobs/{job_id}", headers=headers)
    assert st.status_code == 200, (
        f"job row was deleted after solution deletion (CASCADE bug): "
        f"{st.status_code} {st.text}"
    )
    assert st.json()["install_id"] == sid, (
        f"job install_id changed unexpectedly: {st.json()}"
    )
