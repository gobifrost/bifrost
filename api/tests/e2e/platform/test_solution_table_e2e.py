"""End-to-end (live REST): deploy a Solution table, seed rows, redeploy with a
changed schema, and confirm rows are preserved (criterion 11)."""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.e2e


def _create_solution(e2e_client, headers, slug: str) -> str:
    r = e2e_client.post("/api/solutions", headers=headers, json={
        "slug": slug, "name": slug.upper(), "scope": "global",
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

    # Seed a runtime row via the documents API (this is NOT part of the bundle).
    doc = e2e_client.post(f"/api/tables/{tid}/documents", headers=headers, json={
        "id": "row-1", "data": {"email": "a@x.com"},
    })
    assert doc.status_code in (200, 201), doc.text

    # Redeploy with a CHANGED schema (added column).
    dep2 = e2e_client.post(f"/api/solutions/{sid}/deploy", headers=headers, json={
        "tables": [{"id": tid, "name": f"people_{slug}", "schema": {"columns": [{"name": "email"}, {"name": "phone"}]}, "policies": None}],
    })
    assert dep2.status_code in (200, 201), dep2.text

    # Row survives the schema migration.
    got = e2e_client.get(f"/api/tables/{tid}/documents/row-1", headers=headers)
    assert got.status_code == 200, got.text
    assert got.json()["data"]["email"] == "a@x.com"
