"""E2E: solution lifecycle — uninstall (status flip) vs hard-delete (confirmed cascade).

uninstall = POST /{id}/uninstall → flips status to 'inactive', data frozen in place.
  - owned Table row still has solution_id == sid (NOT orphaned/nulled)
  - Solution row still exists after uninstall
  - idempotent: a second uninstall returns 200

hard-delete = DELETE /{id}?confirm=<slug>
  - confirm mismatch → 422, nothing touched
  - confirm match → Solution row gone, owned Table row gone (cascade)
"""
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


def _deploy_with_table(e2e_client, headers, sid: str, slug: str) -> UUID:
    """Deploy a minimal bundle containing one table; return the real table UUID."""
    bundle_tid = str(uuid.uuid4())
    dep = e2e_client.post(f"/api/solutions/{sid}/deploy", headers=headers, json={
        "tables": [{
            "id": bundle_tid,
            "name": f"customers_{slug}",
            "description": "test table",
            "schema": {"columns": [{"name": "email"}]},
            "policies": None,
        }],
    })
    dep = wait_for_deploy(e2e_client, dep, headers)
    assert dep.status_code == 200, dep.text
    return solution_entity_id(UUID(sid), UUID(bundle_tid))


async def test_uninstall_flips_status_and_freezes_data(
    e2e_client, platform_admin, db_session
):
    """Uninstall sets status='inactive', leaves all owned data frozen in place."""
    headers = platform_admin.headers
    slug = f"uninst-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug)
    real_tid = _deploy_with_table(e2e_client, headers, sid, slug)

    r = e2e_client.post(f"/api/solutions/{sid}/uninstall", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "inactive"

    # Solution row STILL EXISTS (not deleted).
    db_session.expire_all()
    sol = await db_session.get(SolutionORM, UUID(sid))
    assert sol is not None, "solution row was deleted on uninstall — should only flip status"
    assert sol.status == "inactive"

    # Owned table still has solution_id == sid (NOT orphaned).
    tbl = (
        await db_session.execute(select(Table).where(Table.id == real_tid))
    ).scalar_one_or_none()
    assert tbl is not None, "table row deleted on uninstall — data was destroyed"
    assert tbl.solution_id == UUID(sid), (
        f"solution_id was cleared on uninstall (got {tbl.solution_id}) — "
        "uninstall must NOT mutate owned entities"
    )


async def test_uninstall_is_idempotent(e2e_client, platform_admin, db_session):
    """Uninstalling an already-inactive install returns 200 without error."""
    headers = platform_admin.headers
    slug = f"uninst-idem-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug)

    r1 = e2e_client.post(f"/api/solutions/{sid}/uninstall", headers=headers)
    assert r1.status_code == 200, r1.text

    r2 = e2e_client.post(f"/api/solutions/{sid}/uninstall", headers=headers)
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "inactive"


async def test_hard_delete_requires_confirm(e2e_client, platform_admin, db_session):
    """A wrong confirm token → 422; nothing is deleted."""
    headers = platform_admin.headers
    slug = f"hdel-confirm-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug)

    bad = e2e_client.request("DELETE", f"/api/solutions/{sid}", headers=headers,
                              params={"confirm": "wrong-slug"})
    assert bad.status_code in (400, 422), f"expected 4xx on mismatch, got {bad.status_code}"

    # Install must still exist.
    g = e2e_client.get(f"/api/solutions/{sid}", headers=headers)
    assert g.status_code == 200, "solution was deleted despite confirm mismatch"


async def test_hard_delete_cascades_all_owned_rows(e2e_client, platform_admin, db_session):
    """Confirmed hard-delete removes the Solution row and all owned entities via cascade."""
    headers = platform_admin.headers
    slug = f"hdel-cas-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug)

    # Deploy a bundle with a table + a workflow so we exercise both cascade paths.
    bundle_tid = str(uuid.uuid4())
    wf_id = str(uuid.uuid4())
    dep = e2e_client.post(f"/api/solutions/{sid}/deploy", headers=headers, json={
        "python_files": {
            "workflows/w.py": (
                "from bifrost import workflow\n\n"
                "@workflow\nasync def go():\n    return 1\n"
            ),
        },
        "workflows": [{
            "id": wf_id, "name": f"go_{slug}", "function_name": "go",
            "path": "workflows/w.py", "type": "workflow",
        }],
        "tables": [{
            "id": bundle_tid,
            "name": f"rows_{slug}",
            "description": "test",
            "schema": {"columns": [{"name": "val"}]},
            "policies": None,
        }],
    })
    dep = wait_for_deploy(e2e_client, dep, headers)
    assert dep.status_code == 200, dep.text

    real_tid = solution_entity_id(UUID(sid), UUID(bundle_tid))

    ok = e2e_client.request("DELETE", f"/api/solutions/{sid}", headers=headers,
                             params={"confirm": slug})
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["solution_id"] == sid
    assert body["tables_deleted"] >= 1
    assert body["workflows_deleted"] >= 1

    # Solution row is gone.
    g = e2e_client.get(f"/api/solutions/{sid}", headers=headers)
    assert g.status_code == 404, "solution row still exists after hard-delete"

    # Owned Table row cascaded away.
    db_session.expire_all()
    tbl = (
        await db_session.execute(select(Table).where(Table.id == real_tid))
    ).scalar_one_or_none()
    assert tbl is None, "owned table survived hard-delete — cascade did not fire"

    # Owned Workflow row cascaded away.
    wf_rows = (
        await db_session.execute(select(Workflow).where(Workflow.solution_id == UUID(sid)))
    ).scalars().all()
    assert wf_rows == [], f"owned workflows survived hard-delete: {len(wf_rows)}"


async def test_deletion_summary_returns_counts(e2e_client, platform_admin, db_session):
    """GET /deletion-summary returns counts of owned entities before any delete."""
    headers = platform_admin.headers
    slug = f"summ-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug)

    bundle_tid = str(uuid.uuid4())
    dep = e2e_client.post(f"/api/solutions/{sid}/deploy", headers=headers, json={
        "tables": [{
            "id": bundle_tid,
            "name": f"sum_tbl_{slug}",
            "description": "summary test",
            "schema": {"columns": [{"name": "x"}]},
            "policies": None,
        }],
    })
    dep = wait_for_deploy(e2e_client, dep, headers)
    assert dep.status_code == 200, dep.text

    r = e2e_client.get(f"/api/solutions/{sid}/deletion-summary", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["solution_id"] == sid
    assert body["tables"] >= 1
