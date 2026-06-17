"""End-to-end: the deploy workflow-name preflight refuses a bundle whose
``function_name`` is not defined in the carried source.

That bundle would persist a Workflow.name the execution engine can't resolve
("Executable not found"). The deploy endpoint catches it up front and returns
422 with actionable guidance. A manifest slug that merely differs from the
decorated name is NOT a failure (import resolves to the decorated name), so
that case deploys cleanly.
"""
from __future__ import annotations

import uuid

import pytest

from tests.e2e.platform.conftest import wait_for_deploy

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


def test_deploy_rejects_missing_function(e2e_client, platform_admin):
    """function_name points at a function the source does not define → 422."""
    headers = platform_admin.headers
    slug = f"sol-preflight-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug=slug)

    # Source defines `other`, but the bundle entry points at `main`.
    source = (
        "from bifrost import workflow\n\n"
        '@workflow(name="Sandbox Ticket Snapshot")\n'
        "async def other():\n"
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
                    "name": "hello",
                    "function_name": "main",
                    "path": "workflows/snap.py",
                    "type": "workflow",
                    "source": source,
                }
            ],
        },
    )
    resp = wait_for_deploy(e2e_client, resp, headers)
    assert resp.status_code == 422, f"expected 422, got {resp.status_code}: {resp.text}"
    detail = resp.json()["detail"]
    assert "main" in detail
    assert "workflows/snap.py" in detail


def test_deploy_accepts_slug_differing_from_decorator(e2e_client, platform_admin):
    """A manifest slug that differs from the decorated name still deploys —
    import resolves to the decorated name, so preflight must not block it."""
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
                    # Slug diverges from the decorated name — legitimate, not blocked.
                    "name": "hello-slug",
                    "function_name": "main",
                    "path": "workflows/snap.py",
                    "type": "workflow",
                    "source": source,
                }
            ],
        },
    )
    resp = wait_for_deploy(e2e_client, resp, headers)
    assert resp.status_code in (200, 201), f"deploy failed: {resp.status_code} {resp.text}"
