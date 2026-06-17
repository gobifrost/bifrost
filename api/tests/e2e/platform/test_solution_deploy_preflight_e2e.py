"""End-to-end: the deploy workflow-name preflight refuses a bundle whose
manifest entry name diverges from the decorated @workflow(name=...).

A bundle that carried such a mismatch would persist a Workflow.name the
execution engine can't resolve ("Executable 'hello' not found"). The deploy
endpoint catches it up front and returns 422 with actionable guidance.
"""
from __future__ import annotations

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


def test_deploy_rejects_workflow_name_mismatch(e2e_client, platform_admin):
    headers = platform_admin.headers
    slug = f"sol-preflight-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug=slug)

    source = (
        "from bifrost import workflow\n\n"
        '@workflow(name="Sandbox Ticket Snapshot")\n'
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
                    # Manifest entry name diverges from the decorated name.
                    "name": "hello",
                    "function_name": "main",
                    "path": "workflows/snap.py",
                    "type": "workflow",
                    "source": source,
                }
            ],
        },
    )
    assert resp.status_code == 422, f"expected 422, got {resp.status_code}: {resp.text}"
    detail = resp.json()["detail"]
    assert "hello" in detail
    assert "Sandbox Ticket Snapshot" in detail
    assert "workflows/snap.py" in detail


def test_deploy_accepts_matching_workflow_name(e2e_client, platform_admin):
    """A bundle whose manifest name matches the decorated name passes preflight."""
    headers = platform_admin.headers
    slug = f"sol-preflight-ok-{uuid.uuid4().hex[:8]}"
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
    assert resp.status_code in (200, 201), f"deploy failed: {resp.status_code} {resp.text}"
