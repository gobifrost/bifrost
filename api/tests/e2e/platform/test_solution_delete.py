"""E2E (live REST + DB read): DELETE /api/solutions/{id}?confirm=<slug> is the
HARD-DELETE path. All owned entities (workflows/apps/forms/agents/tables/config
declarations) are removed via ondelete=CASCADE when the Solution row is deleted.

The S3 artifacts are swept and the git repo is NEVER touched — git-connected
installs are deletable; only the install and its local artifacts go."""
from __future__ import annotations

import uuid
from uuid import UUID

import pytest
from sqlalchemy import select

from src.models.orm.solutions import Solution as SolutionORM
from src.models.orm.tables import Table
from src.models.orm.workflows import Workflow
from src.services.solutions.deploy import solution_entity_id
from tests.e2e.platform.conftest import wait_for_deploy

pytestmark = pytest.mark.e2e


def _create_solution(e2e_client, headers, slug: str) -> str:
    r = e2e_client.post("/api/solutions", headers=headers, json={
        "slug": slug, "name": slug.upper(), "organization_id": None,
    })
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def test_delete_cascades_code_entities(e2e_client, platform_admin, db_session):
    """Pure-code entities (workflows) and config DECLARATIONS cascade via FK."""
    headers = platform_admin.headers
    slug = f"del-e2e-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug)

    wf_id = str(uuid.uuid4())
    dep = e2e_client.post(f"/api/solutions/{sid}/deploy", headers=headers, json={
        "python_files": {
            "workflows/w.py": (
                "from bifrost import workflow\n\n"
                "@workflow\n"
                "async def go():\n"
                "    return 1\n"
            ),
        },
        "workflows": [{
            "id": wf_id, "name": f"go_{slug}", "function_name": "go",
            "path": "workflows/w.py", "type": "workflow",
        }],
        "config_schemas": [{
            "id": str(uuid.uuid4()), "key": "API_KEY", "type": "secret",
            "required": True, "description": "needed", "position": 0,
        }],
    })
    dep = wait_for_deploy(e2e_client, dep, headers)
    assert dep.status_code == 200, dep.text

    r = e2e_client.request("DELETE", f"/api/solutions/{sid}", headers=headers,
                            params={"confirm": slug})
    assert r.status_code in (200, 204), r.text
    body = r.json()
    assert body["solution_id"] == sid
    assert body["workflows_deleted"] >= 1
    assert body["config_declarations_deleted"] >= 1

    # The install is gone.
    g = e2e_client.get(f"/api/solutions/{sid}", headers=headers)
    assert g.status_code == 404, g.text

    # Cascade removed the owned workflow row.
    rows = (
        await db_session.execute(
            select(Workflow).where(Workflow.solution_id == UUID(sid))
        )
    ).scalars().all()
    assert rows == [], f"expected cascade to remove owned workflows, got {len(rows)}"


async def test_delete_cascades_tables(e2e_client, platform_admin, db_session):
    """Owned tables and their documents are cascade-deleted on hard-delete."""
    headers = platform_admin.headers
    slug = f"del-tbl-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug)

    bundle_tid = str(uuid.uuid4())
    dep = e2e_client.post(f"/api/solutions/{sid}/deploy", headers=headers, json={
        "tables": [{
            "id": bundle_tid,
            "name": f"customers_{slug}",
            "description": "customer records",
            "schema": {"columns": [{"name": "email"}]},
            "policies": None,
        }],
    })
    dep = wait_for_deploy(e2e_client, dep, headers)
    assert dep.status_code == 200, dep.text
    real_tid = solution_entity_id(UUID(sid), UUID(bundle_tid))

    r = e2e_client.request("DELETE", f"/api/solutions/{sid}", headers=headers,
                            params={"confirm": slug})
    assert r.status_code in (200, 204), r.text
    body = r.json()
    assert body["tables_deleted"] >= 1, body

    # The install is gone.
    g = e2e_client.get(f"/api/solutions/{sid}", headers=headers)
    assert g.status_code == 404, g.text

    # The Table row was cascaded away (hard-delete path).
    db_session.expire_all()
    tbl = (
        await db_session.execute(select(Table).where(Table.id == real_tid))
    ).scalar_one_or_none()
    assert tbl is None, "table survived hard-delete — cascade did not fire"


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
