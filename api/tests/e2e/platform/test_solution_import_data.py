"""E2E: full-backup zip import — table data (per-table wholesale, with collision).

Task 17 of the Solutions success-criteria programme.

Contract under test:
- A full-backup zip (with .bifrost/secrets.enc) that carries table rows installs
  and fills the target table silently when the table is empty.
- A collision (target table already has rows AND not replace_data) refuses with 409,
  naming the colliding table.
- replace_data=true performs a wholesale clear+insert and succeeds.

make_solution_with_table_rows builds a real full-backup zip by:
  1. Creating a source solution with a deployed table.
  2. Seeding rows in that table via the documents API.
  3. GETting /export?mode=full&password=...&include_data=true for real zip bytes.

This exercises the full round-trip (export → install) end to end.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.e2e


def _upload_headers(headers: dict[str, str]) -> dict[str, str]:
    """Strip Content-Type so httpx sets it correctly for multipart."""
    return {k: v for k, v in headers.items() if k.lower() != "content-type"}


def _create_org(e2e_client, headers: dict[str, str]) -> str:
    domain = f"import-data-{uuid.uuid4().hex[:8]}.test"
    r = e2e_client.post(
        "/api/organizations",
        headers=headers,
        json={"name": f"ImportData Org {domain}", "domain": domain},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.fixture
def make_org(e2e_client, platform_admin):
    """Factory: create a fresh org, return SimpleNamespace with .id."""
    async def _make() -> SimpleNamespace:
        org_id = _create_org(e2e_client, platform_admin.headers)
        return SimpleNamespace(id=uuid.UUID(org_id))
    return _make


@pytest.fixture
def make_solution_with_table_rows(e2e_client, platform_admin):
    """Factory: create a solution with a deployed table + seeded rows, export zip.

    Steps:
    1. Create a source org + solution with a given slug (random if not given).
    2. Deploy a table named ``table`` into the solution (via /deploy).
    3. Seed ``rows`` into the deployed table via the documents API.
    4. Export with mode=full&password=pw&include_data=true.
    Returns a SimpleNamespace with .id, .org_id, .manifest_tid, .zip_bytes.
    """
    from src.services.solutions.deploy import solution_entity_id

    async def _make(
        table: str,
        rows: list[dict],
        *,
        slug: str | None = None,
    ) -> SimpleNamespace:
        headers = platform_admin.headers

        src_org_id = _create_org(e2e_client, headers)
        actual_slug = slug or f"import-data-{uuid.uuid4().hex[:8]}"
        r = e2e_client.post(
            "/api/solutions",
            headers=headers,
            json={
                "slug": actual_slug,
                "name": actual_slug.upper(),
                "scope": "org",
                "organization_id": src_org_id,
            },
        )
        assert r.status_code in (200, 201), r.text
        sol = r.json()
        sol_id = sol["id"]
        org_id = sol["organization_id"]
        manifest_tid = str(uuid.uuid4())

        # Deploy the table into the solution.
        dep = e2e_client.post(
            f"/api/solutions/{sol_id}/deploy",
            headers=headers,
            json={
                "tables": [
                    {
                        "id": manifest_tid,
                        "name": table,
                        "schema": {"columns": [{"name": k} for k in (rows[0].keys() if rows else [])]},
                        "policies": None,
                    }
                ],
            },
        )
        assert dep.status_code in (200, 201), dep.text

        # Real deployed table id = uuid5(install_id, manifest_id).
        real_tid = str(solution_entity_id(uuid.UUID(sol_id), uuid.UUID(manifest_tid)))

        # Seed the provided rows via the documents API.
        for row in rows:
            doc_id = str(row.get("id", uuid.uuid4()))
            dr = e2e_client.post(
                f"/api/tables/{real_tid}/documents?solution={sol_id}",
                headers=headers,
                json={"id": doc_id, "data": {k: v for k, v in row.items() if k != "id"}},
            )
            assert dr.status_code in (200, 201), f"seed doc failed: {dr.text}"

        # Export with include_data=true to get the full-backup zip.
        exp = e2e_client.get(
            f"/api/solutions/{sol_id}/export?mode=full&password=pw&include_data=true",
            headers=headers,
        )
        assert exp.status_code == 200, exp.text

        return SimpleNamespace(
            id=uuid.UUID(sol_id),
            org_id=uuid.UUID(org_id),
            manifest_tid=uuid.UUID(manifest_tid),
            zip_bytes=exp.content,
        )

    return _make


# ---------------------------------------------------------------------------
# Test 1: empty table fills silently
# ---------------------------------------------------------------------------


async def test_full_export_with_data_restores_rows_in_fresh_org(
    e2e_client, platform_admin, make_solution_with_table_rows, make_org
):
    """Installing a full-backup zip with table data into a fresh org must succeed
    and the installed solution's table must contain the exported rows."""
    headers = platform_admin.headers
    upload_headers = _upload_headers(headers)

    src = await make_solution_with_table_rows(
        table="widgets",
        rows=[{"id": "1", "name": "a"}],
    )
    org = await make_org()

    files = {"file": ("s.zip", src.zip_bytes, "application/zip")}
    r = e2e_client.post(
        "/api/solutions/install",
        headers=upload_headers,
        files=files,
        data={"organization_id": str(org.id), "password": "pw"},
    )
    assert r.status_code == 200, r.text
    sol_id = r.json()["id"]

    # Find the installed table's UUID via /entities, then query its documents.
    ent_r = e2e_client.get(f"/api/solutions/{sol_id}/entities", headers=headers)
    assert ent_r.status_code == 200, ent_r.text
    tables = ent_r.json()["tables"]
    widgets_tbl = next((t for t in tables if t["name"] == "widgets"), None)
    assert widgets_tbl is not None, f"widgets table not found in installed solution: {tables}"
    tbl_id = widgets_tbl["id"]

    table_r = e2e_client.post(
        f"/api/tables/{tbl_id}/documents/query",
        headers=headers,
        json={},
    )
    assert table_r.status_code == 200, table_r.text
    docs = table_r.json()
    doc_items = docs.get("documents", [])
    assert len(doc_items) == 1, f"expected 1 row, got {len(doc_items)}: {docs}"
    assert doc_items[0]["data"].get("name") == "a", f"wrong row data: {doc_items[0]}"


# ---------------------------------------------------------------------------
# Test 2: collision refuses without replace_data; Test 3: replace_data succeeds
# ---------------------------------------------------------------------------


async def test_data_collision_refuses_without_replace_data(
    e2e_client, platform_admin, make_solution_with_table_rows, make_org
):
    """Re-installing the SAME zip into an org whose table already has rows must
    refuse with 409 (naming the table) unless replace_data=true.  With
    replace_data=true the install must succeed and the table must contain only
    the bundle's rows (wholesale replace)."""
    headers = platform_admin.headers
    upload_headers = _upload_headers(headers)

    # One zip: widgets with row {"name": "bundled"}.
    src = await make_solution_with_table_rows(
        table="widgets",
        rows=[{"id": "row1", "name": "bundled"}],
        slug=f"data-collide-{uuid.uuid4().hex[:8]}",
    )
    org = await make_org()

    # First install: fills the empty table silently.
    files1 = {"file": ("s.zip", src.zip_bytes, "application/zip")}
    r0 = e2e_client.post(
        "/api/solutions/install",
        headers=upload_headers,
        files=files1,
        data={"organization_id": str(org.id), "password": "pw"},
    )
    assert r0.status_code == 200, r0.text
    sol_id = r0.json()["id"]

    # Second install of the SAME zip into the SAME org → collision (table has rows).
    files2 = {"file": ("s.zip", src.zip_bytes, "application/zip")}
    r = e2e_client.post(
        "/api/solutions/install",
        headers=upload_headers,
        files=files2,
        data={"organization_id": str(org.id), "password": "pw"},
    )
    assert r.status_code == 409, r.text
    assert "widgets" in r.text, f"expected 'widgets' in collision error, got: {r.text}"

    # Third install of the SAME zip with replace_data=true → wholesale replace, succeeds.
    files3 = {"file": ("s.zip", src.zip_bytes, "application/zip")}
    r2 = e2e_client.post(
        "/api/solutions/install",
        headers=upload_headers,
        files=files3,
        data={"organization_id": str(org.id), "password": "pw", "replace_data": "true"},
    )
    assert r2.status_code == 200, r2.text

    # After wholesale replace, table has exactly the bundle's rows (1 row).
    ent_r2 = e2e_client.get(f"/api/solutions/{sol_id}/entities", headers=headers)
    assert ent_r2.status_code == 200, ent_r2.text
    tables2 = ent_r2.json()["tables"]
    widgets_tbl2 = next((t for t in tables2 if t["name"] == "widgets"), None)
    assert widgets_tbl2 is not None
    tbl_id2 = widgets_tbl2["id"]

    table_r = e2e_client.post(
        f"/api/tables/{tbl_id2}/documents/query",
        headers=headers,
        json={},
    )
    assert table_r.status_code == 200, table_r.text
    docs = table_r.json()
    doc_items = docs.get("documents", [])
    assert len(doc_items) == 1, f"expected 1 row after replace, got {len(doc_items)}: {docs}"
    assert doc_items[0]["data"].get("name") == "bundled", f"wrong data after replace: {doc_items[0]}"
