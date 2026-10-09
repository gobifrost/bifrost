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


@pytest.fixture(scope="module")
def deployed_solution(e2e_client, platform_admin) -> dict:
    """One deploy for independent table-namespace and V2 app contracts."""
    headers = platform_admin.headers
    slug = f"solution-assets-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug)
    coexist_table_manifest_id = str(uuid.uuid4())
    scoped_table_manifest_id = str(uuid.uuid4())
    app_manifest_id = str(uuid.uuid4())
    batch_workflow_manifest_id = str(uuid.uuid4())
    coexist_table_name = f"coexist_{slug.replace('-', '_')}"
    scoped_table_name = f"people_{slug}"
    batch_workflow_name = f"batch_writer_{slug.replace('-', '_')}"
    app_id = str(solution_entity_id(UUID(sid), UUID(app_manifest_id)))
    app_slug = f"dash-{slug}"
    index_html = (
        "<!doctype html><html><head>"
        '<meta name="bifrost-app-runtime" content="mount-v1">'
        f'<script type="module" crossorigin src="/api/applications/{app_id}/dist/assets/main-abc.js"></script>'
        f'<link rel="stylesheet" href="/api/applications/{app_id}/dist/assets/main-abc.css">'
        "</head><body><div id=\"root\"></div></body></html>"
    )

    dep = e2e_client.post(f"/api/solutions/{sid}/deploy", headers=headers, json={
        "tables": [
            {
                "id": coexist_table_manifest_id,
                "name": coexist_table_name,
                "schema": {"columns": [{"name": "email"}]},
                "policies": None,
            },
            {
                "id": scoped_table_manifest_id,
                "name": scoped_table_name,
                "schema": {"columns": [{"name": "email"}]},
                "policies": [
                    {
                        "name": "admin_bypass",
                        "actions": ["read", "create", "update", "delete"],
                        "when": {"user": "is_platform_admin"},
                    },
                    {
                        "name": "authenticated_batch_write",
                        "actions": ["create", "update"],
                        "when": None,
                    },
                ],
            },
        ],
        "python_files": {
            "workflows/batch_writer.py": (
                "from bifrost import context, tables, workflow\n\n"
                f'@workflow(name="{batch_workflow_name}")\n'
                "async def batch_writer():\n"
                f"    result = await tables.upsert_batch({scoped_table_name!r}, [{{'id': 'batch-row', 'data': {{'email': 'batch@example.test'}}}}])\n"
                "    return {\n"
                "        'count': result.count,\n"
                "        'org_id': context.org_id,\n"
                "        'solution_id': context.solution_id,\n"
                "    }\n"
            ),
        },
        "workflows": [{
            "id": batch_workflow_manifest_id,
            "name": batch_workflow_name,
            "function_name": "batch_writer",
            "path": "workflows/batch_writer.py",
            "type": "workflow",
            "access_level": "authenticated",
        }],
        "apps": [{
            "id": app_manifest_id,
            "slug": app_slug,
            "name": "Dash",
            "app_model": "standalone_v2",
            "dependencies": {},
            "access_level": "authenticated",
            "dist_files": {
                "index.html": index_html,
                "assets/main-abc.js": "console.log('v2')",
                "assets/main-abc.css": ".x{color:red}",
            },
        }],
    })
    dep = wait_for_deploy(e2e_client, dep, headers)
    assert dep.status_code in (200, 201), dep.text
    return {
        "app_id": app_id,
        "app_slug": app_slug,
        "batch_workflow_id": str(
            solution_entity_id(UUID(sid), UUID(batch_workflow_manifest_id))
        ),
        "batch_workflow_name": batch_workflow_name,
        "coexist_table_id": str(
            solution_entity_id(UUID(sid), UUID(coexist_table_manifest_id))
        ),
        "coexist_table_name": coexist_table_name,
        "deploy": dep.json(),
        "id": sid,
        "scoped_table_name": scoped_table_name,
    }


def test_repo_table_coexists_with_solution_table_same_name(
    e2e_client, platform_admin, deployed_solution
):
    """Bug #5: a _repo table must be creatable even when a solution-managed table
    of the SAME name already exists in the same scope. The schema (migration
    20260606_table_name_solution_scope) uses separate partial unique indexes per
    source so the two coexist; the _repo create-time duplicate check must only see
    the _repo namespace (solution_id IS NULL)."""
    headers = platform_admin.headers
    sid = deployed_solution["id"]
    name = deployed_solution["coexist_table_name"]
    sol_table_id = deployed_solution["coexist_table_id"]

    # Create a normal _repo table of the SAME name in the same (global) scope.
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


def test_solution_app_and_workflow_resolve_their_table_by_name(
    e2e_client, platform_admin, deployed_solution
):
    """An app header and a workflow's solution query each resolve the install's
    table by name; an unscoped request cannot access it."""
    headers = platform_admin.headers
    sid = deployed_solution["id"]
    table_name = deployed_solution["scoped_table_name"]
    app_id = deployed_solution["app_id"]

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



def test_solution_global_table_batch_write_uses_engine_org_without_explicit_scope(
    e2e_client, org1, org1_user, platform_admin, deployed_solution
):
    """The worker keeps the caller organization while its SDK batch request resolves own global data."""
    from tests.e2e.conftest import execute_workflow_sync

    result = execute_workflow_sync(
        e2e_client,
        org1_user.headers,
        deployed_solution["batch_workflow_id"],
        max_wait=120.0,
    )
    assert result["status"] == "Success", result
    assert result["result"] == {
        "count": 1,
        "org_id": org1["id"],
        "solution_id": deployed_solution["id"],
    }

    stored = e2e_client.get(
        f"/api/tables/{deployed_solution['scoped_table_name']}/documents/batch-row?solution={deployed_solution['id']}",
        headers=platform_admin.headers,
    )
    assert stored.status_code == 200, stored.text
    assert stored.json()["data"] == {"email": "batch@example.test"}


def test_solution_global_table_batch_write_rejects_explicit_org_scope(
    e2e_client, org1, platform_admin, deployed_solution
):
    """An explicit organization scope cannot be used to write the global table."""
    response = e2e_client.post(
        f"/api/tables/{deployed_solution['scoped_table_name']}/documents/batch?solution={deployed_solution['id']}&scope={org1['id']}",
        headers=platform_admin.headers,
        json={"upsert": True, "documents": [{"id": "blocked-row", "data": {"email": "blocked@example.test"}}]},
    )
    assert response.status_code == 404, response.text


def test_v2_app_deploys_builds_dist_and_reports_model(
    e2e_client, platform_admin, deployed_solution
):
    headers = platform_admin.headers
    app_id = deployed_solution["app_id"]
    app_slug = deployed_solution["app_slug"]
    assert deployed_solution["deploy"]["apps_upserted"] == 1

    # The Application row is solution-managed and standalone_v2. The metadata
    # GET endpoint resolves by slug (globally unique), not by id.
    got = e2e_client.get(f"/api/applications/{app_slug}", headers=headers)
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["id"] == app_id
    assert body["app_model"] == "standalone_v2"
    assert body["is_solution_managed"] is True

    # The dist/ is served from _apps/{id}/ — index.html (criterion 12).
    idx = e2e_client.get(f"/api/applications/{app_id}/dist/index.html", headers=headers)
    assert idx.status_code == 200, idx.text
    assert 'id="root"' in idx.text
    assert idx.headers["content-type"].startswith("text/html")

    # A hashed asset referenced by index.html is fetchable from the same prefix.
    asset = e2e_client.get(
        f"/api/applications/{app_id}/dist/assets/main-abc.js", headers=headers
    )
    assert asset.status_code == 200, asset.text
    assert "v2" in asset.text

    # The bundle-manifest surfaces app_model AND the hashed entry parsed from the
    # built index.html, so the client mounts the app same-document (P1-b/G7) —
    # it loads `entry` from the dist base, NOT an iframe to index.html.
    man = e2e_client.get(
        f"/api/applications/{app_id}/bundle-manifest?mode=live", headers=headers
    )
    assert man.status_code == 200, man.text
    mbody = man.json()
    assert mbody["app_model"] == "standalone_v2"
    assert mbody["base_url"] == f"/api/applications/{app_id}/dist"
    # index.html referenced the entry + css under /dist/ → manifest reports them
    # relative to the dist base (the shell re-joins base + entry/css).
    assert mbody["entry"] == "assets/main-abc.js"
    assert mbody["css"] == "assets/main-abc.css"
    assert mbody["runtime_contract"] == "mount-v1"
