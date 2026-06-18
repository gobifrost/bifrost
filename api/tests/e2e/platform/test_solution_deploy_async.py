"""End-to-end: solution deploy is async + observable.

The deploy endpoint enqueues a background job and returns a ``deploy_job_id``
immediately; the operator polls ``GET /api/solutions/deploy-jobs/{id}`` until
the job reaches a terminal status. This decouples the (sometimes >100s) deploy
from the HTTP request, which previously timed out client-side while the server
completed successfully (Task 7 bug).
"""
from __future__ import annotations

import time
import uuid

import pytest

pytestmark = pytest.mark.e2e


def _create_solution(e2e_client, headers, *, slug: str) -> str:
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
    assert resp.status_code in (200, 201), f"create failed: {resp.status_code} {resp.text}"
    return resp.json()["id"]


def test_async_deploy_completes(e2e_client, platform_admin):
    headers = platform_admin.headers
    slug = f"sol-async-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug=slug)

    wf_name = f"Snap {slug}"
    source = (
        "from bifrost import workflow\n\n"
        f'@workflow(name="{wf_name}")\n'
        "async def main():\n"
        "    return 1\n"
    )
    resp = e2e_client.post(
        f"/api/solutions/{sid}/deploy",
        headers=headers,
        json={
            "python_files": {"workflows/snap.py": source},
            "workflows": [
                {
                    "id": str(uuid.uuid4()),
                    "name": wf_name,
                    "function_name": "main",
                    "path": "workflows/snap.py",
                    "type": "workflow",
                    "source": source,
                }
            ],
        },
    )
    assert resp.status_code == 202, f"expected 202, got {resp.status_code}: {resp.text}"
    job_id = resp.json()["deploy_job_id"]
    assert job_id

    status = None
    for _ in range(60):
        st = e2e_client.get(f"/api/solutions/deploy-jobs/{job_id}", headers=headers)
        assert st.status_code == 200, f"status fetch failed: {st.status_code} {st.text}"
        body = st.json()
        status = body["status"]
        if status in ("succeeded", "failed"):
            break
        time.sleep(0.5)

    assert status == "succeeded", f"deploy job did not succeed: {body}"
    assert body["install_id"] == sid


def test_async_deploy_reports_failure(e2e_client, platform_admin):
    """A bundle whose declared ``function_name`` does not exist in the carried
    source fails preflight inside the job — the job reaches ``failed`` with the
    error, not a 500 at enqueue time.

    Note: a manifest *name* (slug) that merely differs from the decorated name is
    NOT a failure — import persists the decorated name regardless of the slug, so
    preflight only blocks the genuinely execution-breaking case (the named
    function is absent from the source). This mirrors the unit/e2e preflight
    contract in test_deploy_preflight.py / test_solution_deploy_preflight_e2e.py.
    """
    headers = platform_admin.headers
    slug = f"sol-async-fail-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug=slug)

    # Source defines `main`, but the bundle points function_name at `missing`.
    source = (
        "from bifrost import workflow\n\n"
        '@workflow(name="Real Name")\n'
        "async def main():\n"
        "    return 1\n"
    )
    resp = e2e_client.post(
        f"/api/solutions/{sid}/deploy",
        headers=headers,
        json={
            "python_files": {"workflows/snap.py": source},
            "workflows": [
                {
                    "id": str(uuid.uuid4()),
                    "name": "snap",
                    "function_name": "missing",
                    "path": "workflows/snap.py",
                    "type": "workflow",
                    "source": source,
                }
            ],
        },
    )
    assert resp.status_code == 202, f"expected 202, got {resp.status_code}: {resp.text}"
    job_id = resp.json()["deploy_job_id"]

    status = None
    for _ in range(60):
        st = e2e_client.get(f"/api/solutions/deploy-jobs/{job_id}", headers=headers)
        assert st.status_code == 200
        body = st.json()
        status = body["status"]
        if status in ("succeeded", "failed"):
            break
        time.sleep(0.5)

    assert status == "failed", f"expected failed, got {body}"
    assert body["error"]
    assert "missing" in body["error"]
