"""Live REST checks for Solution table namespace and scoped row access.

Schema changes preserving rows are covered through SolutionDeployer with a
real database in test_solution_table_deploy.py.
"""
from __future__ import annotations

import uuid
from uuid import UUID

import pytest

from src.services.solutions.deploy import solution_entity_id
from tests.e2e.platform.conftest import wait_for_deploy

pytestmark = pytest.mark.e2e


def _create_solution(e2e_client, headers, slug: str) -> str:
    r = e2e_client.post("/api/solutions", headers=headers, json={
        "slug": slug, "name": slug.upper(), "organization_id": None,
    })
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def test_repo_table_coexists_with_solution_table_same_name(e2e_client, platform_admin):
    """Bug #5: a _repo table must be creatable even when a solution-managed table
    of the SAME name already exists in the same scope. The schema (migration
    20260606_table_name_solution_scope) uses separate partial unique indexes per
    source so the two coexist; the _repo create-time duplicate check must only see
    the _repo namespace (solution_id IS NULL)."""
    headers = platform_admin.headers
    slug = f"coexist-{uuid.uuid4().hex[:8]}"
    name = f"coexist_{slug.replace('-', '_')}"
    sid = _create_solution(e2e_client, headers, slug)
    tid = str(uuid.uuid4())

    # 1) Install a SOLUTION-managed table first (global scope).
    dep = e2e_client.post(f"/api/solutions/{sid}/deploy", headers=headers, json={
        "tables": [{"id": tid, "name": name, "schema": {"columns": [{"name": "email"}]}, "policies": None}],
    })
    dep = wait_for_deploy(e2e_client, dep, headers)
    assert dep.status_code in (200, 201), dep.text
    sol_table_id = str(solution_entity_id(UUID(sid), UUID(tid)))

    # 2) Now create a normal _repo table of the SAME name in the same (global) scope.
    #    This previously 409'd because the duplicate check saw the solution row.
    create = e2e_client.post("/api/tables?scope=global", headers=headers, json={
        "name": name, "schema": {"columns": [{"name": "phone"}]},
    })
    assert create.status_code in (200, 201), f"_repo create blocked by solution row: {create.text}"
    repo_table_id = create.json()["id"]

    # 3) Both rows exist independently.
    assert repo_table_id != sol_table_id
    sol = e2e_client.get(f"/api/tables/{sol_table_id}", headers=headers)
    assert sol.status_code == 200, sol.text
    assert sol.json()["solution_id"] == sid
    rep = e2e_client.get(f"/api/tables/{repo_table_id}", headers=headers)
    assert rep.status_code == 200, rep.text
    assert rep.json().get("solution_id") is None


def test_solution_app_and_workflow_resolve_their_table_by_name(e2e_client, platform_admin):
    """An app header and a workflow's solution query each resolve the install's
    table by name; an unscoped request cannot access it."""
    headers = platform_admin.headers
    slug = f"tbln-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug)
    tid, app_manifest_id = str(uuid.uuid4()), str(uuid.uuid4())
    table_name = f"people_{slug}"

    dep = e2e_client.post(f"/api/solutions/{sid}/deploy", headers=headers, json={
        "tables": [{"id": tid, "name": table_name,
                    "schema": {"columns": [{"name": "email"}]}, "policies": None}],
        "apps": [{"id": app_manifest_id, "slug": f"app-{slug}", "name": "App",
                  "app_model": "standalone_v2", "dist_files": {"index.html": "<html></html>"}}],
    })
    dep = wait_for_deploy(e2e_client, dep, headers)
    assert dep.status_code in (200, 201), dep.text
    app_id = str(solution_entity_id(UUID(sid), UUID(app_manifest_id)))

    # WITHOUT the app header: the name cascade excludes the solution table → 404.
    no_app = e2e_client.post(
        f"/api/tables/{table_name}/documents", headers=headers,
        json={"id": "r1", "data": {"email": "a@x.com"}},
    )
    assert no_app.status_code == 404, f"expected 404 w/o app header, got {no_app.text}"

    # WITH the app header: resolves the install's own table by name → row op works.
    app_headers = {**headers, "X-Bifrost-App": app_id}
    with_app = e2e_client.post(
        f"/api/tables/{table_name}/documents", headers=app_headers,
        json={"id": "r1", "data": {"email": "a@x.com"}},
    )
    assert with_app.status_code in (200, 201), f"app-scoped row op failed: {with_app.text}"
    got = e2e_client.get(
        f"/api/tables/{table_name}/documents/r1", headers=app_headers
    )
    assert got.status_code == 200, got.text
    assert got.json()["data"]["email"] == "a@x.com"

    # A solution workflow uses ?solution=<install_id> for the same table lookup.
    with_scope = e2e_client.post(
        f"/api/tables/{table_name}/documents?solution={sid}", headers=headers,
        json={"id": "r2", "data": {"email": "b@x.com"}},
    )
    assert with_scope.status_code in (200, 201), f"workflow-scoped row op failed: {with_scope.text}"
    got = e2e_client.get(
        f"/api/tables/{table_name}/documents/r2?solution={sid}", headers=headers
    )
    assert got.status_code == 200, got.text
    assert got.json()["data"]["email"] == "b@x.com"
