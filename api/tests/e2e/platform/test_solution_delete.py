"""E2E (live REST + DB read): DELETE /api/solutions/{id} is NON-DESTRUCTIVE for
customer data. Pure-code entities (workflows/apps/forms/agents) and the install's
config DECLARATIONS cascade away, but data-bearing entities are ORPHANED:

- owned tables (and their documents) are DETACHED and survive as ordinary org
  tables, stamped with orphan provenance;
- the install's config VALUES are stamped with orphan provenance and survive.

The S3 artifacts are swept and the git repo is NEVER touched (git-connected
installs are deletable too)."""
from __future__ import annotations

import uuid
from uuid import UUID

import pytest
from sqlalchemy import select

from src.models.orm.config import Config
from src.models.orm.solutions import Solution as SolutionORM
from src.models.orm.tables import Document, Table
from src.models.orm.workflows import Workflow
from src.services.solutions.deploy import solution_entity_id

pytestmark = pytest.mark.e2e


def _create_solution(e2e_client, headers, slug: str) -> str:
    r = e2e_client.post("/api/solutions", headers=headers, json={
        "slug": slug, "name": slug.upper(), "scope": "global",
    })
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def test_delete_cascades_code_entities(e2e_client, platform_admin, db_session):
    """Pure-code entities (workflows) and config DECLARATIONS still cascade."""
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
    assert dep.status_code == 200, dep.text

    r = e2e_client.delete(f"/api/solutions/{sid}", headers=headers)
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


async def test_delete_orphans_tables_with_data(e2e_client, platform_admin, db_session):
    """A table owned by the install — WITH document data — survives the delete as
    an ordinary org table, stamped with orphan provenance. Its documents are
    untouched (data is never lost on uninstall)."""
    headers = platform_admin.headers
    slug = f"del-tbl-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug)

    # Find the install's org scope (global install → organization_id IS NULL).
    install = (
        await db_session.execute(
            select(SolutionORM).where(SolutionORM.id == UUID(sid))
        )
    ).scalar_one()
    install_org = install.organization_id

    # Bundle id is remapped at deploy to a deterministic per-install real id.
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
    assert dep.status_code == 200, dep.text
    real_tid = solution_entity_id(UUID(sid), UUID(bundle_tid))

    # Seed customer data via the documents API (same connection that committed
    # the table — avoids cross-session FK visibility issues).
    doc = e2e_client.post(f"/api/tables/{real_tid}/documents", headers=headers, json={
        "id": "row-1", "data": {"email": "a@b.com"},
    })
    assert doc.status_code in (200, 201), doc.text

    r = e2e_client.delete(f"/api/solutions/{sid}", headers=headers)
    assert r.status_code in (200, 204), r.text
    body = r.json()
    assert body["tables_orphaned"] >= 1, body

    # The install is gone.
    g = e2e_client.get(f"/api/solutions/{sid}", headers=headers)
    assert g.status_code == 404, g.text

    # The Table row STILL EXISTS, detached + stamped with provenance.
    db_session.expire_all()
    tbl = (
        await db_session.execute(select(Table).where(Table.id == real_tid))
    ).scalar_one_or_none()
    assert tbl is not None, "table was deleted — data loss on uninstall!"
    assert tbl.solution_id is None
    assert tbl.orphaned_at is not None
    assert tbl.origin_solution_slug == slug
    assert tbl.origin_solution_id == UUID(sid)
    assert tbl.organization_id == install_org

    # The document (customer data) survives — it hangs off the surviving table.
    docs = (
        await db_session.execute(
            select(Document).where(Document.table_id == real_tid)
        )
    ).scalars().all()
    assert len(docs) == 1, f"document data lost on uninstall, got {len(docs)}"
    assert docs[0].data == {"email": "a@b.com"}


async def test_uninstall_with_same_name_repo_table_succeeds(
    e2e_client, platform_admin, db_session
):
    """Coexistence of a _repo/ table and a same-name solution table is legal
    (table names are solution-scoped); uninstall must detach the solution table
    without violating the _repo/ live-name unique index. Orphaned rows leave the
    live name namespace, so the detach can never collide."""
    headers = platform_admin.headers
    slug = f"del-coex-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug)
    name = f"users_{slug}"

    # 1. An ordinary live _repo/ table in the install's scope (global install →
    #    organization_id IS NULL) with the SAME name the solution will ship.
    ct = e2e_client.post("/api/tables", headers=headers, json={
        "name": name, "organization_id": None, "description": "live repo table",
    })
    assert ct.status_code in (200, 201), ct.text

    # 2. The solution ships a same-name table — legal coexistence (deploy
    #    blesses it: uniqueness is solution-scoped).
    bundle_tid = str(uuid.uuid4())
    dep = e2e_client.post(f"/api/solutions/{sid}/deploy", headers=headers, json={
        "tables": [{
            "id": bundle_tid,
            "name": name,
            "description": "solution-owned table",
            "schema": {"columns": [{"name": "email"}]},
            "policies": None,
        }],
    })
    assert dep.status_code == 200, dep.text

    # 3. Uninstall must succeed — the detach stamps orphaned_at, moving the row
    #    OUT of the live name namespace instead of colliding with the live table.
    r = e2e_client.delete(f"/api/solutions/{sid}", headers=headers)
    assert r.status_code in (200, 204), r.text
    assert r.json()["tables_orphaned"] == 1, r.text

    # 4. Exactly two rows of that name survive: the live one and the orphan
    #    (with provenance).
    lst = e2e_client.get("/api/tables?include_orphaned=true", headers=headers)
    assert lst.status_code == 200, lst.text
    rows = [t for t in lst.json()["tables"] if t["name"] == name]
    assert len(rows) == 2, rows
    live = [t for t in rows if t["orphaned_at"] is None]
    orphaned = [t for t in rows if t["orphaned_at"] is not None]
    assert len(live) == 1 and len(orphaned) == 1, rows
    assert orphaned[0]["origin_solution_slug"] == slug


async def test_delete_orphans_config_values(e2e_client, platform_admin, db_session):
    """A config VALUE matching one of the install's declarations is stamped with
    orphan provenance and survives the delete."""
    headers = platform_admin.headers
    slug = f"del-cfg-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug)

    key = f"API_KEY_{uuid.uuid4().hex[:6]}"
    dep = e2e_client.post(f"/api/solutions/{sid}/deploy", headers=headers, json={
        "config_schemas": [{
            "id": str(uuid.uuid4()), "key": key, "type": "string",
            "required": True, "description": "needed", "position": 0,
        }],
    })
    assert dep.status_code == 200, dep.text

    # Set a value in the install's (global) scope.
    sc = e2e_client.post("/api/config", headers=headers, json={
        "key": key, "value": "sekret", "type": "string", "organization_id": None,
    })
    assert sc.status_code in (200, 201), sc.text

    r = e2e_client.delete(f"/api/solutions/{sid}", headers=headers)
    assert r.status_code in (200, 204), r.text
    body = r.json()
    assert body["config_values_orphaned"] >= 1, body

    # The install is gone.
    g = e2e_client.get(f"/api/solutions/{sid}", headers=headers)
    assert g.status_code == 404, g.text

    # The Config VALUE survives, stamped with provenance.
    db_session.expire_all()
    cfg = (
        await db_session.execute(
            select(Config).where(Config.key == key, Config.organization_id.is_(None))
        )
    ).scalar_one_or_none()
    assert cfg is not None, "config value deleted — customer data loss!"
    assert cfg.orphaned_at is not None
    assert cfg.origin_solution_slug == slug
    assert cfg.origin_solution_id == UUID(sid)


def test_delete_missing_is_404(e2e_client, platform_admin):
    headers = platform_admin.headers
    r = e2e_client.delete(f"/api/solutions/{uuid.uuid4()}", headers=headers)
    assert r.status_code == 404, r.text


async def test_delete_git_connected_allowed(e2e_client, platform_admin, db_session):
    """git-connected installs ARE deletable from the API (unlike deploy/zip-install
    which refuse them). The upstream repo is external — nothing is asserted about it
    because the endpoint never touches a git repo."""
    headers = platform_admin.headers
    sid = uuid.uuid4()
    db_session.add(SolutionORM(
        id=sid,
        slug=f"del-git-{uuid.uuid4().hex[:8]}",
        name="GIT",
        organization_id=None,
        git_connected=True,
        git_repo_url="https://example.com/repo.git",
    ))
    await db_session.commit()

    r = e2e_client.delete(f"/api/solutions/{sid}", headers=headers)
    assert r.status_code in (200, 204), r.text

    g = e2e_client.get(f"/api/solutions/{sid}", headers=headers)
    assert g.status_code == 404, g.text
