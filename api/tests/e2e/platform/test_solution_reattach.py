"""E2E (live REST + DB read): reinstalling a Solution at the same (slug, scope)
REATTACHES the orphaned data left behind by a prior uninstall, instead of
starting empty (Task 14c).

- A table orphaned on uninstall (origin_solution_slug == slug, name match, org
  match) is ADOPTED IN PLACE by the redeploy: it keeps its id + documents, gets
  re-stamped to the new install, and its orphan provenance is cleared. The
  customer's data flows back in.
- A config VALUE orphaned on uninstall is un-orphaned when the reinstall
  re-declares its key, so the operator doesn't re-enter the secret.
- A fresh install with no prior orphan creates an ordinary empty table (the
  reattach logic must not misfire).
"""
from __future__ import annotations

import uuid
from uuid import UUID

import pytest
from sqlalchemy import select

from src.models.orm.config import Config
from src.models.orm.tables import Document, Table
from src.services.solutions.deploy import solution_entity_id

pytestmark = pytest.mark.e2e


def _create_solution(e2e_client, headers, slug: str) -> str:
    r = e2e_client.post("/api/solutions", headers=headers, json={
        "slug": slug, "name": slug.upper(), "scope": "global",
    })
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def test_reinstall_reattaches_orphaned_table_with_data(
    e2e_client, platform_admin, db_session
):
    """Install → deploy table + seed a doc → DELETE (orphans table+doc) →
    REINSTALL same slug+scope → redeploy same table → the orphan is adopted: the
    SAME table id survives, the document is intact, ownership is the new install,
    orphan stamp cleared. (And it isn't reconcile-deleted within the same deploy.)
    """
    headers = platform_admin.headers
    slug = f"reatt-tbl-{uuid.uuid4().hex[:8]}"
    table_name = f"customers_{slug}"

    # ── Install #1 ────────────────────────────────────────────────────────────
    sid1 = _create_solution(e2e_client, headers, slug)
    bundle_tid = str(uuid.uuid4())
    dep = e2e_client.post(f"/api/solutions/{sid1}/deploy", headers=headers, json={
        "tables": [{
            "id": bundle_tid, "name": table_name,
            "description": "customer records",
            "schema": {"columns": [{"name": "email"}]}, "policies": None,
        }],
    })
    assert dep.status_code == 200, dep.text
    real_tid_1 = solution_entity_id(UUID(sid1), UUID(bundle_tid))

    doc = e2e_client.post(f"/api/tables/{real_tid_1}/documents", headers=headers, json={
        "id": "row-1", "data": {"email": "a@b.com"},
    })
    assert doc.status_code in (200, 201), doc.text

    # ── Uninstall (orphans the table + its document) ──────────────────────────
    r = e2e_client.delete(f"/api/solutions/{sid1}", headers=headers)
    assert r.status_code in (200, 204), r.text
    assert r.json()["tables_orphaned"] >= 1

    db_session.expire_all()
    orphan = (
        await db_session.execute(select(Table).where(Table.id == real_tid_1))
    ).scalar_one_or_none()
    assert orphan is not None and orphan.orphaned_at is not None
    assert orphan.origin_solution_slug == slug

    # ── Reinstall at the SAME slug+scope (new install id) ─────────────────────
    sid2 = _create_solution(e2e_client, headers, slug)
    assert sid2 != sid1
    # Fresh bundle id → remaps to a DIFFERENT uuid5; reattach matches by NAME, so
    # the orphan is adopted regardless of the id mismatch.
    bundle_tid_2 = str(uuid.uuid4())
    remapped_2 = solution_entity_id(UUID(sid2), UUID(bundle_tid_2))
    assert remapped_2 != real_tid_1, "precondition: new bundle id remaps differently"

    dep2 = e2e_client.post(f"/api/solutions/{sid2}/deploy", headers=headers, json={
        "tables": [{
            "id": bundle_tid_2, "name": table_name,
            "description": "customer records",
            "schema": {"columns": [{"name": "email"}]}, "policies": None,
        }],
    })
    assert dep2.status_code == 200, dep2.text

    # ── Assert: the SAME table id was adopted (not recreated) ─────────────────
    db_session.expire_all()
    # The remapped-fresh id must NOT exist as a new row (proves no recreation).
    fresh = (
        await db_session.execute(select(Table).where(Table.id == remapped_2))
    ).scalar_one_or_none()
    assert fresh is None, "reattach created a NEW table instead of adopting the orphan"

    adopted = (
        await db_session.execute(select(Table).where(Table.id == real_tid_1))
    ).scalar_one_or_none()
    assert adopted is not None, "orphan table vanished — reattach + reconcile lost it"
    assert adopted.solution_id == UUID(sid2), "table not re-owned by the new install"
    assert adopted.orphaned_at is None, "orphan stamp not cleared on reattach"
    assert adopted.origin_solution_slug is None
    assert adopted.origin_solution_id is None

    # ── The customer's document flowed back in (data reattached) ──────────────
    docs = (
        await db_session.execute(
            select(Document).where(Document.table_id == real_tid_1)
        )
    ).scalars().all()
    assert len(docs) == 1, f"document data lost across reinstall, got {len(docs)}"
    assert docs[0].data == {"email": "a@b.com"}


async def test_reinstall_reattaches_orphaned_config_value(
    e2e_client, platform_admin, db_session
):
    """A config value orphaned on uninstall is un-orphaned when the reinstall
    re-declares its key; the value itself is intact."""
    headers = platform_admin.headers
    slug = f"reatt-cfg-{uuid.uuid4().hex[:8]}"
    key = f"API_KEY_{uuid.uuid4().hex[:6]}"

    # Install #1 + declaration + value.
    sid1 = _create_solution(e2e_client, headers, slug)
    dep = e2e_client.post(f"/api/solutions/{sid1}/deploy", headers=headers, json={
        "config_schemas": [{
            "id": str(uuid.uuid4()), "key": key, "type": "string",
            "required": True, "description": "needed", "position": 0,
        }],
    })
    assert dep.status_code == 200, dep.text
    sc = e2e_client.post("/api/config", headers=headers, json={
        "key": key, "value": "sekret", "type": "string", "organization_id": None,
    })
    assert sc.status_code in (200, 201), sc.text

    # Uninstall orphans the value.
    r = e2e_client.delete(f"/api/solutions/{sid1}", headers=headers)
    assert r.status_code in (200, 204), r.text
    assert r.json()["config_values_orphaned"] >= 1

    db_session.expire_all()
    cfg = (
        await db_session.execute(
            select(Config).where(Config.key == key, Config.organization_id.is_(None))
        )
    ).scalar_one()
    assert cfg.orphaned_at is not None, "precondition: value should be orphaned"

    # Reinstall + re-declare the SAME key.
    sid2 = _create_solution(e2e_client, headers, slug)
    dep2 = e2e_client.post(f"/api/solutions/{sid2}/deploy", headers=headers, json={
        "config_schemas": [{
            "id": str(uuid.uuid4()), "key": key, "type": "string",
            "required": True, "description": "needed", "position": 0,
        }],
    })
    assert dep2.status_code == 200, dep2.text

    # The value is un-orphaned and intact.
    db_session.expire_all()
    cfg2 = (
        await db_session.execute(
            select(Config).where(Config.key == key, Config.organization_id.is_(None))
        )
    ).scalar_one()
    assert cfg2.orphaned_at is None, "config value still orphaned after reinstall"
    assert cfg2.origin_solution_slug is None
    assert cfg2.origin_solution_id is None


async def test_fresh_install_no_orphan_creates_empty(
    e2e_client, platform_admin, db_session
):
    """A normal deploy with no prior orphan creates a fresh table at the remapped
    id (the reattach logic must not misfire and must not adopt unrelated rows)."""
    headers = platform_admin.headers
    slug = f"fresh-tbl-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug)
    bundle_tid = str(uuid.uuid4())
    real_tid = solution_entity_id(UUID(sid), UUID(bundle_tid))

    dep = e2e_client.post(f"/api/solutions/{sid}/deploy", headers=headers, json={
        "tables": [{
            "id": bundle_tid, "name": f"things_{slug}",
            "schema": {"columns": [{"name": "x"}]}, "policies": None,
        }],
    })
    assert dep.status_code == 200, dep.text

    db_session.expire_all()
    tbl = (
        await db_session.execute(select(Table).where(Table.id == real_tid))
    ).scalar_one_or_none()
    assert tbl is not None, "fresh deploy didn't create the table at its remapped id"
    assert tbl.solution_id == UUID(sid)
    assert tbl.orphaned_at is None
    # No documents (empty fresh table).
    docs = (
        await db_session.execute(
            select(Document).where(Document.table_id == real_tid)
        )
    ).scalars().all()
    assert docs == []
