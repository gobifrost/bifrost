"""End-to-end (live REST): deploy a Solution table, seed rows, redeploy with a
changed schema, and confirm rows are preserved (criterion 11)."""
from __future__ import annotations

import uuid
from uuid import UUID

import pytest

from src.services.solutions.deploy import solution_entity_id

pytestmark = pytest.mark.e2e


def _create_solution(e2e_client, headers, slug: str) -> str:
    r = e2e_client.post("/api/solutions", headers=headers, json={
        "slug": slug, "name": slug.upper(), "organization_id": None,
    })
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def test_table_deploy_preserves_rows_across_schema_change(e2e_client, platform_admin):
    headers = platform_admin.headers
    slug = f"tbl-e2e-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug)
    tid = str(uuid.uuid4())

    # Deploy v1 (schema with one column).
    dep = e2e_client.post(f"/api/solutions/{sid}/deploy", headers=headers, json={
        "tables": [{"id": tid, "name": f"people_{slug}", "schema": {"columns": [{"name": "email"}]}, "policies": None}],
    })
    assert dep.status_code in (200, 201), dep.text
    assert dep.json()["tables_upserted"] == 1

    # Deploy remaps the manifest id to uuid5(install_id, manifest_id); the table is
    # addressable only by the remapped id.
    real_id = str(solution_entity_id(UUID(sid), UUID(tid)))

    # Seed a runtime row via the documents API (this is NOT part of the bundle).
    doc = e2e_client.post(f"/api/tables/{real_id}/documents", headers=headers, json={
        "id": "row-1", "data": {"email": "a@x.com"},
    })
    assert doc.status_code in (200, 201), doc.text

    # Redeploy with a CHANGED schema (added column).
    dep2 = e2e_client.post(f"/api/solutions/{sid}/deploy", headers=headers, json={
        "tables": [{"id": tid, "name": f"people_{slug}", "schema": {"columns": [{"name": "email"}, {"name": "phone"}]}, "policies": None}],
    })
    assert dep2.status_code in (200, 201), dep2.text

    # Row survives the schema migration.
    got = e2e_client.get(f"/api/tables/{real_id}/documents/row-1", headers=headers)
    assert got.status_code == 200, got.text
    assert got.json()["data"]["email"] == "a@x.com"


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


def test_solution_table_coexists_with_existing_repo_table(e2e_client, platform_admin):
    """Reverse order (already worked, regression guard): create a _repo table, then
    install a solution shipping the same name → both coexist."""
    headers = platform_admin.headers
    slug = f"corev-{uuid.uuid4().hex[:8]}"
    name = f"corev_{slug.replace('-', '_')}"

    create = e2e_client.post("/api/tables?scope=global", headers=headers, json={
        "name": name, "schema": {"columns": [{"name": "phone"}]},
    })
    assert create.status_code in (200, 201), create.text
    repo_table_id = create.json()["id"]

    sid = _create_solution(e2e_client, headers, slug)
    tid = str(uuid.uuid4())
    dep = e2e_client.post(f"/api/solutions/{sid}/deploy", headers=headers, json={
        "tables": [{"id": tid, "name": name, "schema": {"columns": [{"name": "email"}]}, "policies": None}],
    })
    assert dep.status_code in (200, 201), f"solution deploy blocked by _repo row: {dep.text}"
    sol_table_id = str(solution_entity_id(UUID(sid), UUID(tid)))

    assert repo_table_id != sol_table_id
    assert e2e_client.get(f"/api/tables/{repo_table_id}", headers=headers).status_code == 200
    assert e2e_client.get(f"/api/tables/{sol_table_id}", headers=headers).status_code == 200


def test_solution_app_resolves_its_table_by_name(e2e_client, platform_admin):
    """Codex #15: a v2 app's useTable("name") (no per-install id) must resolve the
    app's OWN install table. The SDK sends X-Bifrost-App; the table router resolves
    the app's solution_id and finds the install's table by name. Without the header
    the name cascade excludes solution-managed tables and row ops 404."""
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


def test_solution_workflow_resolves_its_table_by_name(e2e_client, platform_admin):
    """F2: a solution WORKFLOW's sdk.tables call must resolve its OWN install's
    table by name — the workflow analog of the app path. The SDK appends
    ?solution=<install_id> (from the ExecutionContext); the table router resolves
    own-first off ctx.solution_id. Without it, the name cascade excludes the
    solution table → 404 (insert) / empty (query). The install id IS the solution
    id (a workflow knows its install directly, no app→solution lookup)."""
    headers = platform_admin.headers
    slug = f"wftbl-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug)
    tid = str(uuid.uuid4())
    table_name = f"widgets_{slug}"

    dep = e2e_client.post(f"/api/solutions/{sid}/deploy", headers=headers, json={
        "tables": [{"id": tid, "name": table_name,
                    "schema": {"columns": [{"name": "label"}]}, "policies": None}],
    })
    assert dep.status_code in (200, 201), dep.text

    # WITHOUT the solution scope: name cascade excludes the solution table → 404.
    no_scope = e2e_client.post(
        f"/api/tables/{table_name}/documents", headers=headers,
        json={"id": "r1", "data": {"label": "alpha"}},
    )
    assert no_scope.status_code == 404, f"expected 404 w/o solution scope, got {no_scope.text}"

    # WITH ?solution=<install_id> (what the SDK appends for a solution workflow):
    # resolves the install's own table by name → row op works.
    with_scope = e2e_client.post(
        f"/api/tables/{table_name}/documents?solution={sid}", headers=headers,
        json={"id": "r1", "data": {"label": "alpha"}},
    )
    assert with_scope.status_code in (200, 201), f"workflow-scoped row op failed: {with_scope.text}"
    got = e2e_client.get(
        f"/api/tables/{table_name}/documents/r1?solution={sid}", headers=headers
    )
    assert got.status_code == 200, got.text
    assert got.json()["data"]["label"] == "alpha"
