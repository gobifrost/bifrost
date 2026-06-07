"""E2E: PATCH /api/solutions/{id} edits an install's INSTALL-LOCAL fields only
(name/scope/global_repo_access/git fields). A scope change (organization_id) must
re-stamp every owned entity's organization_id to match the install — owned
entities inherit the install's org from the deployer.
"""
from __future__ import annotations

import uuid
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from src.models.orm.workflows import Workflow

pytestmark = pytest.mark.e2e


def _create_global_solution(e2e_client, headers, slug: str) -> str:
    r = e2e_client.post(
        "/api/solutions",
        headers=headers,
        json={"slug": slug, "name": slug.upper(), "scope": "global"},
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def test_patch_updates_install_local_fields(e2e_client, platform_admin):
    headers = platform_admin.headers
    slug = f"patch-local-{uuid.uuid4().hex[:8]}"
    sid = _create_global_solution(e2e_client, headers, slug)

    r = e2e_client.patch(
        f"/api/solutions/{sid}",
        headers=headers,
        json={"name": "Renamed", "global_repo_access": True},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "Renamed"
    assert body["global_repo_access"] is True
    # Scope unchanged (still global → org NULL).
    assert body["organization_id"] is None
    assert body["scope"] == "global"


async def test_patch_scope_restamps_owned_entities(e2e_client, platform_admin, db_session):
    from src.models.orm.organizations import Organization

    headers = platform_admin.headers
    slug = f"patch-scope-{uuid.uuid4().hex[:8]}"
    sid = _create_global_solution(e2e_client, headers, slug)

    # Deploy one workflow into the (global) install. The deployer stamps the
    # owned row with organization_id = install org (NULL here).
    wf_manifest_id = str(uuid.uuid4())
    dep = e2e_client.post(
        f"/api/solutions/{sid}/deploy",
        headers=headers,
        json={
            "python_files": {
                "workflows/w.py": (
                    "from bifrost import workflow\n\n"
                    "@workflow\n"
                    "async def go():\n"
                    "    return 1\n"
                ),
            },
            "workflows": [{
                "id": wf_manifest_id,
                "name": f"go_{slug}",
                "function_name": "go",
                "path": "workflows/w.py",
                "type": "workflow",
            }],
        },
    )
    assert dep.status_code == 200, dep.text

    # The owned workflow currently sits on the global scope (org NULL).
    wf_before = (
        await db_session.execute(
            select(Workflow).where(Workflow.solution_id == UUID(sid))
        )
    ).scalars().all()
    assert len(wf_before) == 1, f"expected one owned workflow, got {len(wf_before)}"
    assert wf_before[0].organization_id is None

    # A real org to move the install to (FK target).
    new_org_id = uuid4()
    db_session.add(Organization(id=new_org_id, name="PatchScopeOrg", created_by="test"))
    await db_session.commit()

    r = e2e_client.patch(
        f"/api/solutions/{sid}",
        headers=headers,
        json={"organization_id": str(new_org_id)},
    )
    assert r.status_code == 200, r.text
    assert r.json()["organization_id"] == str(new_org_id)
    assert r.json()["scope"] == "org"

    # Load-bearing assertion: the OWNED ENTITY's org was re-stamped, not just the
    # install's. Expire to force a fresh read of the row the endpoint mutated.
    db_session.expire_all()
    wf_after = (
        await db_session.execute(
            select(Workflow).where(Workflow.solution_id == UUID(sid))
        )
    ).scalars().all()
    assert len(wf_after) == 1
    assert wf_after[0].organization_id == new_org_id, (
        "owned workflow org should follow the install's scope change"
    )


def test_patch_nonexistent_returns_404(e2e_client, platform_admin):
    headers = platform_admin.headers
    r = e2e_client.patch(
        f"/api/solutions/{uuid.uuid4()}",
        headers=headers,
        json={"name": "x"},
    )
    assert r.status_code == 404, r.text
