"""E2E: solution-aware file scope resolver + presign O2 hardening.

Task 2 of the solution-scoped-files plan:
- H6: ctx.solution_id wins over request.scope (even for superusers).
- C2: solution_id is stored in FileMetadata.solution_id, NOT in
  organization_id (install UUID never lands in organization_id).
- O2 failure modes: presign rejects foreign scope, path traversal, PUT into
  foreign scope.
- Isolation: solution A's file is not visible to solution B at the same path.
"""

from __future__ import annotations

import uuid
from uuid import UUID

import pytest

from src.models.orm.applications import Application
from src.models.orm.solution_file_location import SolutionFileLocation
from tests.e2e.file_policy_helpers import grant_file_policy

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_solution(e2e_client, headers, slug: str, org_id: str | None = None) -> dict:
    """Create a solution and return the full response dict (id + organization_id).

    Pass ``org_id`` to create an org-scoped solution, or None for global.
    """
    r = e2e_client.post("/api/solutions", headers=headers, json={
        "slug": slug, "name": slug.upper(), "organization_id": org_id,
    })
    assert r.status_code in (200, 201), f"create solution failed: {r.text}"
    return r.json()


def _seed_solutions_policy(e2e_client, headers, *, org_id: str | None) -> None:
    """Seed an allow-all policy for location=solutions under the given org.

    Platform-admin superusers are allowed by the admin_bypass policy that is
    auto-seeded on first upsert — this call ensures that row exists so that
    is_allowed() finds a policy and returns True for the admin-token tests.

    PUT /api/files/policies/{path}?location=<loc>&scope=<scope>
    """
    params: dict = {"location": "solutions"}
    if org_id is not None:
        params["scope"] = org_id
    r = e2e_client.put(
        "/api/files/policies/",
        headers=headers,
        params=params,
        json={
            "policies": {
                "policies": [
                    {
                        "name": "allow_all",
                        "actions": ["read", "write", "delete", "list"],
                    }
                ]
            }
        },
    )
    assert r.status_code in (200, 201, 204), f"seed policy failed: {r.status_code} {r.text}"


async def _declare_file_location(db_session, solution_id: str, location: str) -> None:
    db_session.add(
        SolutionFileLocation(solution_id=UUID(solution_id), location=location)
    )
    await db_session.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.e2e
@pytest.mark.asyncio
async def test_solution_write_then_read_isolated(e2e_client, platform_admin, db_session):
    """A solution write lands under solutions/{install_id}/; readable by that
    solution; solution B CANNOT read solution A's file at the same logical path.
    """
    headers = platform_admin.headers
    slug_a = f"file-scope-a-{uuid.uuid4().hex[:8]}"
    slug_b = f"file-scope-b-{uuid.uuid4().hex[:8]}"

    sol_a = _create_solution(e2e_client, headers, slug_a)
    sol_b = _create_solution(e2e_client, headers, slug_b)
    sid_a = sol_a["id"]
    sid_b = sol_b["id"]
    org_id_a = sol_a.get("organization_id")
    org_id_b = sol_b.get("organization_id")

    _seed_solutions_policy(e2e_client, headers, org_id=org_id_a)
    _seed_solutions_policy(e2e_client, headers, org_id=org_id_b)
    await _declare_file_location(db_session, sid_a, "solutions")
    await _declare_file_location(db_session, sid_b, "solutions")

    test_path = f"r/{uuid.uuid4().hex}/x.txt"

    # Write via solution A's context.
    write_r = e2e_client.post(
        f"/api/files/write?solution={sid_a}",
        headers=headers,
        json={
            "location": "solutions",
            "path": test_path,
            "content": "hello from A",
            "mode": "cloud",
        },
    )
    assert write_r.status_code == 204, f"solution A write failed: {write_r.status_code} {write_r.text}"

    # Readable by solution A.
    read_a = e2e_client.post(
        f"/api/files/read?solution={sid_a}",
        headers=headers,
        json={"location": "solutions", "path": test_path, "mode": "cloud"},
    )
    assert read_a.status_code == 200, f"solution A read failed: {read_a.text}"
    assert read_a.json()["content"] == "hello from A"

    # Solution B CANNOT read solution A's file at the same logical path —
    # their scopes are different install UUIDs, so they target different S3 keys.
    # Expect 403 (policy denied) or 404 (different scope → different S3 key).
    read_b = e2e_client.post(
        f"/api/files/read?solution={sid_b}",
        headers=headers,
        json={"location": "solutions", "path": test_path, "mode": "cloud"},
    )
    assert read_b.status_code in (403, 404), (
        f"solution B should not read solution A's file: {read_b.status_code} {read_b.text}"
    )


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_presign_rejects_client_supplied_foreign_scope(
    e2e_client, platform_admin, db_session
):
    """O2 #1: a client cannot presign into a foreign scope by passing scope=<other org>.

    The server ignores / overrides the supplied scope — the signed URL
    targets the caller's own solution scope, NOT the foreign org.
    """
    headers = platform_admin.headers
    slug = f"file-presign-o2-1-{uuid.uuid4().hex[:8]}"
    sol = _create_solution(e2e_client, headers, slug)
    sid = sol["id"]
    org_id = sol.get("organization_id")
    _seed_solutions_policy(e2e_client, headers, org_id=org_id)
    await _declare_file_location(db_session, sid, "solutions")

    # Use a different org UUID as the "foreign" scope.
    foreign_org = str(uuid.uuid4())

    r = e2e_client.post(
        f"/api/files/signed-url?solution={sid}",
        headers=headers,
        json={
            "location": "solutions",
            "path": "secret.txt",
            "method": "GET",
            "scope": foreign_org,
        },
    )
    # Server ignores/overrides the supplied scope → URL targets the caller's
    # own solution scope (200), or policy denied (403). Must NOT be 200 with
    # the foreign_org in the resolved path.
    assert r.status_code in (200, 403), f"unexpected status: {r.status_code} {r.text}"
    if r.status_code == 200:
        resolved_path = r.json().get("path", "")
        assert foreign_org not in resolved_path, (
            f"signed URL landed in foreign scope: path={resolved_path}"
        )
        # The URL must target the caller's own install, not the foreign org.
        assert sid in resolved_path, (
            f"signed URL did not target caller's install: path={resolved_path}"
        )


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_presign_rejects_path_traversal(e2e_client, platform_admin, db_session):
    """O2 #2: path traversal in the presign path is rejected with 400."""
    headers = platform_admin.headers
    slug = f"file-presign-o2-2-{uuid.uuid4().hex[:8]}"
    sol = _create_solution(e2e_client, headers, slug)
    sid = sol["id"]
    org_id = sol.get("organization_id")
    _seed_solutions_policy(e2e_client, headers, org_id=org_id)
    await _declare_file_location(db_session, sid, "solutions")

    r = e2e_client.post(
        f"/api/files/signed-url?solution={sid}",
        headers=headers,
        json={
            "location": "solutions",
            "path": "../../other/x",
            "method": "GET",
        },
    )
    assert r.status_code == 400, f"expected 400 for path traversal, got {r.status_code}: {r.text}"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_presign_put_cannot_plant_in_foreign_scope(
    e2e_client, platform_admin, db_session
):
    """O2 #3: presign PUT with a foreign scope cannot plant in the foreign scope."""
    headers = platform_admin.headers
    slug = f"file-presign-o2-3-{uuid.uuid4().hex[:8]}"
    sol = _create_solution(e2e_client, headers, slug)
    sid = sol["id"]
    org_id = sol.get("organization_id")
    _seed_solutions_policy(e2e_client, headers, org_id=org_id)
    await _declare_file_location(db_session, sid, "solutions")

    foreign_org = str(uuid.uuid4())

    r = e2e_client.post(
        f"/api/files/signed-url?solution={sid}",
        headers=headers,
        json={
            "location": "solutions",
            "path": "plant.txt",
            "method": "PUT",
            "content_type": "text/plain",
            "scope": foreign_org,
        },
    )
    assert r.status_code in (200, 403), f"unexpected status: {r.status_code} {r.text}"
    if r.status_code == 200:
        resolved_path = r.json().get("path", "")
        assert foreign_org not in resolved_path, (
            f"signed PUT landed in foreign scope: path={resolved_path}"
        )
        assert sid in resolved_path, (
            f"signed PUT did not target caller's install: path={resolved_path}"
        )


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_solution_context_rejects_workspace_location_without_metadata(
    e2e_client,
    platform_admin,
    db_session,
):
    """Workspace is physically unscoped, so solution-context file APIs must
    reject it instead of stamping solution metadata over shared _repo bytes.
    """
    from sqlalchemy import select

    from src.models.orm.file_metadata import FileMetadata

    headers = platform_admin.headers
    sol = _create_solution(
        e2e_client,
        headers,
        f"file-scope-workspace-{uuid.uuid4().hex[:8]}",
    )
    sid = sol["id"]
    test_path = f"solution-workspace/{uuid.uuid4().hex}.txt"

    write_r = e2e_client.post(
        f"/api/files/write?solution={sid}",
        headers=headers,
        json={
            "location": "workspace",
            "path": test_path,
            "content": "must not land in _repo",
            "mode": "cloud",
        },
    )
    assert write_r.status_code == 400, f"expected 400, got {write_r.status_code}: {write_r.text}"
    assert "workspace is not available in solution file context" in write_r.text

    result = await db_session.execute(
        select(FileMetadata).where(
            FileMetadata.solution_id == UUID(sid),
            FileMetadata.location == "workspace",
            FileMetadata.path == test_path,
        )
    )
    assert result.scalar_one_or_none() is None


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_solution_context_scopes_freeform_location_metadata_and_s3_key(
    e2e_client,
    platform_admin,
    org1,
    db_session,
):
    """A solution context scopes any file location by install id, not only
    location=solutions.
    """
    from sqlalchemy import select

    from src.models.orm.file_metadata import FileMetadata

    headers = platform_admin.headers
    org_id_str = org1["id"]
    sol = _create_solution(
        e2e_client,
        headers,
        f"file-scope-reports-{uuid.uuid4().hex[:8]}",
        org_id=org_id_str,
    )
    sid = sol["id"]
    actual_org_id_str = sol.get("organization_id")
    assert actual_org_id_str is not None, "solution must have an organization_id for this test"
    await _declare_file_location(db_session, sid, "reports")
    grant_file_policy(
        e2e_client,
        headers,
        location="reports",
        scope=actual_org_id_str,
        prefix="",
        allow_all=True,
    )

    test_path = f"solution-reports/{uuid.uuid4().hex}.txt"
    write_r = e2e_client.post(
        f"/api/files/write?solution={sid}",
        headers=headers,
        json={
            "location": "reports",
            "path": test_path,
            "content": "quarterly close",
            "mode": "cloud",
        },
    )
    assert write_r.status_code == 204, f"write failed: {write_r.status_code} {write_r.text}"

    read_r = e2e_client.post(
        f"/api/files/read?solution={sid}",
        headers=headers,
        json={"location": "reports", "path": test_path, "mode": "cloud"},
    )
    assert read_r.status_code == 200, f"read failed: {read_r.status_code} {read_r.text}"
    assert read_r.json()["content"] == "quarterly close"

    result = await db_session.execute(
        select(FileMetadata).where(
            FileMetadata.solution_id == UUID(sid),
            FileMetadata.location == "reports",
            FileMetadata.path == test_path,
        )
    )
    row = result.scalar_one_or_none()
    assert row is not None, "FileMetadata row not found for solution-scoped reports write"
    assert row.solution_id == UUID(sid)
    assert row.organization_id == UUID(actual_org_id_str)
    assert row.organization_id != UUID(sid)
    assert row.s3_key == f"reports/{sid}/{test_path}"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_solution_app_files_resolve_to_install_scope(
    e2e_client,
    platform_admin,
    org1,
    org1_user,
    org2_user,
    db_session,
):
    """An app-scoped file request resolves the app's solution install without
    an explicit ?solution= query parameter.
    """
    from sqlalchemy import select

    from src.models.orm.file_metadata import FileMetadata

    headers = platform_admin.headers
    org_id_str = org1["id"]
    sol = _create_solution(
        e2e_client,
        headers,
        f"file-scope-app-{uuid.uuid4().hex[:8]}",
        org_id=org_id_str,
    )
    sid = sol["id"]
    actual_org_id_str = sol.get("organization_id")
    assert actual_org_id_str is not None, "solution must have an organization_id for this test"

    solution_id = UUID(sid)
    app_id = uuid.uuid4()
    app_slug = f"file-scope-app-{app_id.hex[:8]}"
    db_session.add(SolutionFileLocation(solution_id=solution_id, location="reports"))
    db_session.add(
        Application(
            id=app_id,
            name="Files Scope App",
            slug=app_slug,
            repo_path=f"apps/{app_slug}",
            organization_id=UUID(actual_org_id_str),
            solution_id=solution_id,
            app_model="standalone_v2",
            created_by="e2e@test.local",
        )
    )
    await db_session.commit()

    grant_file_policy(
        e2e_client,
        headers,
        location="reports",
        scope=actual_org_id_str,
        prefix="",
        allow_all=True,
    )

    forged_scope = str(uuid.uuid4())
    app_headers = {**org1_user.headers, "X-Bifrost-App": str(app_id)}
    test_path = f"solution-app-reports/{uuid.uuid4().hex}.txt"
    write_r = e2e_client.post(
        "/api/files/write",
        headers=app_headers,
        json={
            "location": "reports",
            "scope": forged_scope,
            "path": test_path,
            "content": "app install scope",
            "mode": "cloud",
        },
    )
    assert write_r.status_code == 204, f"write failed: {write_r.status_code} {write_r.text}"

    read_r = e2e_client.post(
        "/api/files/read",
        headers=app_headers,
        json={"location": "reports", "path": test_path, "mode": "cloud"},
    )
    assert read_r.status_code == 200, f"read failed: {read_r.status_code} {read_r.text}"
    assert read_r.json()["content"] == "app install scope"

    result = await db_session.execute(
        select(FileMetadata).where(
            FileMetadata.solution_id == solution_id,
            FileMetadata.location == "reports",
            FileMetadata.path == test_path,
        )
    )
    row = result.scalar_one_or_none()
    assert row is not None, "FileMetadata row not found for app-scoped reports write"
    assert row.solution_id == solution_id
    assert row.organization_id == UUID(actual_org_id_str)
    assert row.s3_key == f"reports/{sid}/{test_path}"
    assert forged_scope not in row.s3_key

    forged_headers = {**org2_user.headers, "X-Bifrost-App": str(app_id)}
    forged_r = e2e_client.post(
        "/api/files/write",
        headers=forged_headers,
        json={
            "location": "reports",
            "path": f"solution-app-reports/{uuid.uuid4().hex}.txt",
            "content": "wrong org",
            "mode": "cloud",
        },
    )
    assert forged_r.status_code == 403, (
        f"foreign org user should not use another org's app scope: "
        f"{forged_r.status_code} {forged_r.text}"
    )


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_solution_app_read_requires_declared_file_location(
    e2e_client,
    platform_admin,
    org1,
    org1_user,
    db_session,
):
    """App-scoped read/list/exists paths cannot reach undeclared locations."""
    from sqlalchemy import select

    from src.models.orm.file_metadata import FileMetadata

    headers = platform_admin.headers
    org_id_str = org1["id"]
    sol = _create_solution(
        e2e_client,
        headers,
        f"file-scope-app-undeclared-{uuid.uuid4().hex[:8]}",
        org_id=org_id_str,
    )
    sid = sol["id"]
    actual_org_id_str = sol.get("organization_id")
    assert actual_org_id_str is not None, "solution must have an organization_id for this test"

    solution_id = UUID(sid)
    app_id = uuid.uuid4()
    app_slug = f"file-scope-app-undec-{app_id.hex[:8]}"
    db_session.add(
        Application(
            id=app_id,
            name="Files Undeclared App",
            slug=app_slug,
            repo_path=f"apps/{app_slug}",
            organization_id=UUID(actual_org_id_str),
            solution_id=solution_id,
            app_model="standalone_v2",
            created_by="e2e@test.local",
        )
    )
    await db_session.commit()

    location = "undeclared"
    grant_file_policy(
        e2e_client,
        headers,
        location=location,
        scope=actual_org_id_str,
        prefix="",
        allow_all=True,
    )
    path = f"solution-app-undeclared/{uuid.uuid4().hex}.txt"
    org_write = e2e_client.post(
        "/api/files/write",
        headers=headers,
        json={
            "location": location,
            "scope": actual_org_id_str,
            "path": path,
            "content": "org fallback should stay hidden",
            "mode": "cloud",
        },
    )
    assert org_write.status_code == 204, org_write.text

    app_headers = {**org1_user.headers, "X-Bifrost-App": str(app_id)}
    read_r = e2e_client.post(
        "/api/files/read",
        headers=app_headers,
        json={"location": location, "path": path, "mode": "cloud"},
    )
    assert read_r.status_code == 404, read_r.text

    exists_r = e2e_client.post(
        "/api/files/exists",
        headers=app_headers,
        json={"location": location, "path": path, "mode": "cloud"},
    )
    assert exists_r.status_code == 404, exists_r.text

    list_r = e2e_client.post(
        "/api/files/list",
        headers=app_headers,
        json={"location": location, "directory": "solution-app-undeclared", "mode": "cloud"},
    )
    assert list_r.status_code == 404, list_r.text

    signed_r = e2e_client.post(
        "/api/files/signed-url",
        headers=app_headers,
        json={"location": location, "path": path, "method": "GET"},
    )
    assert signed_r.status_code == 404, signed_r.text

    result = await db_session.execute(
        select(FileMetadata).where(
            FileMetadata.solution_id == solution_id,
            FileMetadata.location == location,
            FileMetadata.path == path,
        )
    )
    assert result.scalar_one_or_none() is None


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_solution_context_overrides_body_scope_for_non_solutions_location(
    e2e_client,
    platform_admin,
    org1,
    db_session,
):
    """A conflicting request-body scope cannot move a solution-context write
    out of the install's storage scope.
    """
    from sqlalchemy import select

    from src.models.orm.file_metadata import FileMetadata

    headers = platform_admin.headers
    org_id_str = org1["id"]
    sol = _create_solution(
        e2e_client,
        headers,
        f"file-scope-conflict-{uuid.uuid4().hex[:8]}",
        org_id=org_id_str,
    )
    sid = sol["id"]
    actual_org_id_str = sol.get("organization_id")
    assert actual_org_id_str is not None, "solution must have an organization_id for this test"
    await _declare_file_location(db_session, sid, "reports")
    grant_file_policy(
        e2e_client,
        headers,
        location="reports",
        scope=actual_org_id_str,
        prefix="",
        allow_all=True,
    )

    conflicting_scope = str(uuid.uuid4())
    test_path = f"solution-scope-override/{uuid.uuid4().hex}.txt"
    write_r = e2e_client.post(
        f"/api/files/write?solution={sid}",
        headers=headers,
        json={
            "location": "reports",
            "scope": conflicting_scope,
            "path": test_path,
            "content": "body scope ignored",
            "mode": "cloud",
        },
    )
    assert write_r.status_code == 204, f"write failed: {write_r.status_code} {write_r.text}"

    result = await db_session.execute(
        select(FileMetadata).where(
            FileMetadata.solution_id == UUID(sid),
            FileMetadata.location == "reports",
            FileMetadata.path == test_path,
        )
    )
    row = result.scalar_one_or_none()
    assert row is not None, "FileMetadata row not found for body-scope override write"
    assert row.s3_key == f"reports/{sid}/{test_path}"
    assert conflicting_scope not in row.s3_key
    assert row.organization_id == UUID(actual_org_id_str)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_solution_write_metadata_c2_correct_columns(e2e_client, platform_admin, org1, db_session):
    """C2: a solution write stores solution_id in FileMetadata.solution_id,
    and organization_id = the install's org — NOT the install UUID in organization_id.

    Uses an org-scoped solution so organization_id is a real org UUID (not None),
    making the "install UUID != organization_id" assertion non-trivially provable.
    """
    from sqlalchemy import select

    from src.models.orm.file_metadata import FileMetadata

    headers = platform_admin.headers
    slug = f"file-meta-c2-{uuid.uuid4().hex[:8]}"
    org_id_str = org1["id"]
    sol = _create_solution(e2e_client, headers, slug, org_id=org_id_str)
    sid = sol["id"]
    actual_org_id_str = sol.get("organization_id")
    assert actual_org_id_str is not None, "solution must have an organization_id for this test"
    org_id = UUID(actual_org_id_str)
    _seed_solutions_policy(e2e_client, headers, org_id=actual_org_id_str)
    await _declare_file_location(db_session, sid, "solutions")

    test_path = f"meta-c2/{uuid.uuid4().hex}.txt"

    # Write a file through the solutions location with this install's context.
    write_r = e2e_client.post(
        f"/api/files/write?solution={sid}",
        headers=headers,
        json={
            "location": "solutions",
            "path": test_path,
            "content": "c2 test",
            "mode": "cloud",
        },
    )
    assert write_r.status_code == 204, f"write failed: {write_r.status_code} {write_r.text}"

    # Inspect the DB row directly.
    result = await db_session.execute(
        select(FileMetadata).where(
            FileMetadata.solution_id == UUID(sid),
            FileMetadata.location == "solutions",
            FileMetadata.path == test_path,
        )
    )
    row = result.scalar_one_or_none()
    assert row is not None, "FileMetadata row not found for solution write"
    # C2 assertion: install UUID must be in solution_id, NOT in organization_id.
    assert row.solution_id == UUID(sid), (
        f"solution_id not set: {row.solution_id}"
    )
    assert row.organization_id == org_id, (
        f"organization_id should be install's org {org_id}, got {row.organization_id}"
    )
    # Ensure the install UUID did NOT land in organization_id (the C2 bug).
    assert row.organization_id != UUID(sid), (
        f"install UUID {sid} mistakenly written to organization_id (C2 bug)"
    )


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_solution_delete_removes_metadata_row(e2e_client, platform_admin, org1, db_session):
    """C2 delete path: deleting a solution file removes its FileMetadata row.

    Before the fix, delete_metadata matched by organization_id=<install_uuid>
    but the row was stored with organization_id=<install_org>, so the DB row
    survived the delete (orphaned metadata).  After the fix it matches by
    solution_id and the row is gone.
    """
    from sqlalchemy import select

    from src.models.orm.file_metadata import FileMetadata

    headers = platform_admin.headers
    slug = f"file-del-c2-{uuid.uuid4().hex[:8]}"
    org_id_str = org1["id"]
    sol = _create_solution(e2e_client, headers, slug, org_id=org_id_str)
    sid = sol["id"]
    actual_org_id_str = sol.get("organization_id")
    assert actual_org_id_str is not None, "solution must have an organization_id for this test"
    _seed_solutions_policy(e2e_client, headers, org_id=actual_org_id_str)
    await _declare_file_location(db_session, sid, "solutions")

    test_path = f"del-c2/{uuid.uuid4().hex}.txt"

    # Write a file to create the FileMetadata row.
    write_r = e2e_client.post(
        f"/api/files/write?solution={sid}",
        headers=headers,
        json={
            "location": "solutions",
            "path": test_path,
            "content": "delete-me",
            "mode": "cloud",
        },
    )
    assert write_r.status_code == 204, f"write failed: {write_r.status_code} {write_r.text}"

    # Confirm the row exists.
    result = await db_session.execute(
        select(FileMetadata).where(
            FileMetadata.solution_id == UUID(sid),
            FileMetadata.location == "solutions",
            FileMetadata.path == test_path,
        )
    )
    assert result.scalar_one_or_none() is not None, "FileMetadata row missing after write"

    # Delete the file.
    del_r = e2e_client.post(
        f"/api/files/delete?solution={sid}",
        headers=headers,
        json={
            "location": "solutions",
            "path": test_path,
            "mode": "cloud",
        },
    )
    assert del_r.status_code == 204, f"delete failed: {del_r.status_code} {del_r.text}"

    # Row must be gone — prove the C2 delete-path fix works.
    db_session.expire_all()
    result2 = await db_session.execute(
        select(FileMetadata).where(
            FileMetadata.solution_id == UUID(sid),
            FileMetadata.location == "solutions",
            FileMetadata.path == test_path,
        )
    )
    assert result2.scalar_one_or_none() is None, (
        "FileMetadata row survived delete — C2 delete-path bug not fixed"
    )
