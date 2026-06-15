"""E2E: capture → deploy-block → pull → deploy → genuine-delete round-trip.

Proves the pending_captures queue closes the capture→deploy round-trip:

1. Capture a loose table + form into an org-scoped install (enqueues pending rows).
2. Deploy a manifest that OMITS the captured entities → 409 naming them + "pull",
   and the entities still EXIST (the block protected them from the reconcile sweep).
3. Simulate `bifrost solution pull`: POST /export?mode=shareable → unzip .bifrost/
   → the manifest now contains the captured entities → POST /pull/ack clears their
   pending rows.
4. Deploy the manifest now INCLUDING the captured entities → 200, entities survive.
5. Genuine delete: deploy OMITTING one entity (no pending row remains) → it IS
   deleted (source has demonstrably seen it; this is a deliberate removal).
"""

from __future__ import annotations

import io
import uuid
import zipfile

import pytest
import yaml

pytestmark = pytest.mark.e2e


def _make_org(e2e_client, headers) -> str:
    domain = f"cap-{uuid.uuid4().hex[:8]}.test"
    r = e2e_client.post(
        "/api/organizations",
        headers=headers,
        json={"name": f"Cap Org {domain}", "domain": domain},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _make_solution(e2e_client, headers, org_id: str) -> str:
    slug = f"cap-src-{uuid.uuid4().hex[:8]}"
    r = e2e_client.post(
        "/api/solutions",
        headers=headers,
        json={"slug": slug, "name": slug.upper(), "scope": "org", "organization_id": org_id},
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _make_loose_table(e2e_client, headers, org_id: str) -> str:
    name = f"docs_{uuid.uuid4().hex[:8]}"
    r = e2e_client.post(
        "/api/tables",
        headers=headers,
        json={
            "name": name,
            "organization_id": org_id,
            "schema": {"columns": [{"name": "title", "type": "string"}]},
        },
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _make_loose_form(e2e_client, headers, org_id: str) -> str:
    r = e2e_client.post(
        "/api/forms",
        headers=headers,
        json={
            "name": f"intake_{uuid.uuid4().hex[:8]}",
            "organization_id": org_id,
            "form_schema": {"fields": []},
            "access_level": "role_based",
        },
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _entities_from_zip(zip_bytes: bytes) -> dict[str, dict]:
    """Return the .bifrost manifest dicts present in the export zip, keyed by the
    manifest top-level key (tables/forms/...)."""
    out: dict[str, dict] = {}
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for name in zf.namelist():
            if name.startswith(".bifrost/") and name.endswith((".yaml", ".yml")):
                loaded = yaml.safe_load(zf.read(name)) or {}
                out.update(loaded)
    return out


def test_capture_roundtrip(e2e_client, platform_admin):
    headers = platform_admin.headers
    org_id = _make_org(e2e_client, headers)
    sol_id = _make_solution(e2e_client, headers, org_id)

    table_id = _make_loose_table(e2e_client, headers, org_id)
    form_id = _make_loose_form(e2e_client, headers, org_id)

    # 1. Capture both loose entities into the install (enqueues pending rows).
    cap = e2e_client.post(
        f"/api/solutions/{sol_id}/capture",
        headers=headers,
        json={"tables": [table_id], "forms": [form_id]},
    )
    assert cap.status_code in (200, 201), cap.text
    assert cap.json()["tables_captured"] == 1
    assert cap.json()["forms_captured"] == 1

    # 2. Deploy a manifest that OMITS the captured entities → 409 (pull first).
    blocked = e2e_client.post(
        f"/api/solutions/{sol_id}/deploy",
        headers=headers,
        json={"tables": [], "forms": []},
    )
    assert blocked.status_code == 409, blocked.text
    detail = blocked.json()["detail"]
    assert "bifrost solution pull" in detail
    assert table_id in detail or form_id in detail
    # The block protected the entities — they still exist.
    assert e2e_client.get(f"/api/tables/{table_id}", headers=headers).status_code == 200
    assert e2e_client.get(f"/api/forms/{form_id}", headers=headers).status_code == 200

    # 3. Simulate `bifrost solution pull`: export, read the .bifrost/ manifest,
    #    then ack the materialized entities to clear their pending rows.
    export = e2e_client.post(
        f"/api/solutions/{sol_id}/export?mode=shareable", json={}, headers=headers
    )
    assert export.status_code == 200, export.text
    manifest = _entities_from_zip(export.content)
    assert str(table_id) in manifest.get("tables", {}), manifest.keys()
    assert str(form_id) in manifest.get("forms", {}), manifest.keys()

    ack = e2e_client.post(
        f"/api/solutions/{sol_id}/pull/ack",
        headers=headers,
        json={
            "entities": [
                {"entity_type": "table", "entity_id": str(table_id)},
                {"entity_type": "form", "entity_id": str(form_id)},
            ]
        },
    )
    assert ack.status_code == 200, ack.text
    assert ack.json()["cleared"] == 2

    # Build deploy manifest entries from the exported (pulled) manifest.
    table_entry = manifest["tables"][str(table_id)]
    form_entry = manifest["forms"][str(form_id)]

    # 4. Deploy WITH the captured entities now in the manifest → succeeds, survives.
    ok = e2e_client.post(
        f"/api/solutions/{sol_id}/deploy",
        headers=headers,
        json={"tables": [table_entry], "forms": [form_entry]},
    )
    assert ok.status_code in (200, 201), ok.text
    assert e2e_client.get(f"/api/tables/{table_id}", headers=headers).status_code == 200
    assert e2e_client.get(f"/api/forms/{form_id}", headers=headers).status_code == 200

    # 5. Genuine delete: omit the form (no pending row now) → it IS deleted, and
    #    the table (still in the manifest) survives. No 409 — source has seen it.
    deleted = e2e_client.post(
        f"/api/solutions/{sol_id}/deploy",
        headers=headers,
        json={"tables": [table_entry], "forms": []},
    )
    assert deleted.status_code in (200, 201), deleted.text
    assert deleted.json()["forms_deleted"] >= 1
    assert e2e_client.get(f"/api/forms/{form_id}", headers=headers).status_code == 404
    assert e2e_client.get(f"/api/tables/{table_id}", headers=headers).status_code == 200
