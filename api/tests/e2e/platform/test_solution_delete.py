"""E2E (live REST + DB read): DELETE /api/solutions/{id}?confirm=<slug> is the
HARD-DELETE path. All owned entities (workflows/apps/forms/agents/tables/config
declarations) are removed via ondelete=CASCADE when the Solution row is deleted.

The S3 artifacts are swept and the git repo is NEVER touched — git-connected
installs are deletable; only the install and its local artifacts go."""
from __future__ import annotations

import uuid

import pytest

from src.models.orm.solutions import Solution as SolutionORM

pytestmark = pytest.mark.e2e


def _create_solution(e2e_client, headers, slug: str) -> str:
    r = e2e_client.post("/api/solutions", headers=headers, json={
        "slug": slug, "name": slug.upper(), "organization_id": None,
    })
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def test_delete_missing_is_404(e2e_client, platform_admin):
    headers = platform_admin.headers
    r = e2e_client.request("DELETE", f"/api/solutions/{uuid.uuid4()}", headers=headers,
                            params={"confirm": "any-slug"})
    assert r.status_code == 404, r.text


def test_delete_wrong_confirm_is_422(e2e_client, platform_admin):
    """A wrong confirm token is rejected before anything is touched."""
    headers = platform_admin.headers
    slug = f"del-conf-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug)

    r = e2e_client.request("DELETE", f"/api/solutions/{sid}", headers=headers,
                            params={"confirm": "wrong-slug"})
    assert r.status_code in (400, 422), r.text

    # Install still exists.
    g = e2e_client.get(f"/api/solutions/{sid}", headers=headers)
    assert g.status_code == 200, "solution was deleted despite confirm mismatch"


async def test_delete_git_connected_allowed(e2e_client, platform_admin, db_session):
    """git-connected installs ARE deletable from the API (unlike deploy/zip-install
    which refuse them). The upstream repo is external — nothing is asserted about it
    because the endpoint never touches a git repo."""
    headers = platform_admin.headers
    sid = uuid.uuid4()
    slug = f"del-git-{uuid.uuid4().hex[:8]}"
    db_session.add(SolutionORM(
        id=sid,
        slug=slug,
        name="GIT",
        organization_id=None,
        git_connected=True,
        git_repo_url="https://example.com/repo.git",
    ))
    await db_session.commit()

    r = e2e_client.request("DELETE", f"/api/solutions/{sid}", headers=headers,
                            params={"confirm": slug})
    assert r.status_code in (200, 204), r.text

    g = e2e_client.get(f"/api/solutions/{sid}", headers=headers)
    assert g.status_code == 404, g.text
