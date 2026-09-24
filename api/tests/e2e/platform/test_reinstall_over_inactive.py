"""E2E: reinstall-over-inactive — prompt then reactivate (L6 of solution-inactive-lifecycle).

One lifecycle checks that an inactive install is refused until explicitly
reactivated, preserving its identity and retained table data.
"""
from __future__ import annotations

import io
import uuid
import zipfile

import pytest

from tests.e2e.platform.conftest import wait_for_install


pytestmark = pytest.mark.e2e

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _upload_headers(headers: dict) -> dict:
    """Strip Content-Type so httpx sets the multipart boundary itself."""
    return {k: v for k, v in headers.items() if k.lower() != "content-type"}


def _make_zip(slug: str, table_name: str) -> bytes:
    """Minimal Solution workspace zip: descriptor + one workflow + one table."""
    wf_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{slug}/workflows/main"))
    tbl_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{slug}/tables/{table_name}"))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("bifrost.solution.yaml", f"slug: {slug}\nname: {slug.upper()}\nscope: global\n")
        z.writestr(
            ".bifrost/workflows.yaml",
            "workflows:\n"
            f"  {wf_id}:\n"
            f"    id: {wf_id}\n"
            "    name: main\n"
            "    function_name: run\n"
            "    path: workflows/main.py\n",
        )
        z.writestr(
            ".bifrost/tables.yaml",
            "tables:\n"
            f"  {tbl_id}:\n"
            f"    id: {tbl_id}\n"
            f"    name: {table_name}\n"
            "    schema:\n"
            "      columns:\n"
            "        - name: val\n"
            "    policies: null\n",
        )
        z.writestr("workflows/main.py", "def run(sdk):\n    return 'ok'\n")
    return buf.getvalue()


def _install(e2e_client, headers, zip_bytes: bytes, *, query: str = ""):
    # Install is async (202 + poll); wait_for_install returns a terminal shim.
    # Synchronous refusals (the structured inactive-install 409) pass through
    # unchanged, keeping the prompt contract this file asserts.
    r = e2e_client.post(
        f"/api/solutions/install{query}",
        headers=_upload_headers(headers),
        files={"file": ("sol.zip", zip_bytes, "application/zip")},
    )
    return wait_for_install(e2e_client, r, headers)


def _uninstall(e2e_client, headers, solution_id: str) -> None:
    r = e2e_client.post(f"/api/solutions/{solution_id}/uninstall", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "inactive"


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

async def test_reinstall_inactive_then_reactivate(e2e_client, platform_admin):
    """An inactive reinstall requires explicit reactivation and preserves data."""
    headers = platform_admin.headers
    slug = f"reins-lifecycle-{uuid.uuid4().hex[:8]}"
    table_name = f"rt_{uuid.uuid4().hex[:8]}"
    zip_bytes = _make_zip(slug, table_name)

    r = _install(e2e_client, headers, zip_bytes)
    assert r.status_code in (200, 201), r.text
    sid = r.json()["id"]

    # Retained data must exist before uninstall; a failed insert cannot silently
    # weaken this assertion.
    tbl_doc_id = f"row-{uuid.uuid4().hex[:8]}"
    ins = e2e_client.post(
        f"/api/tables/{table_name}/documents?solution={sid}",
        headers=headers,
        json={"id": tbl_doc_id, "data": {"val": "retained"}},
    )
    assert ins.status_code in (200, 201), ins.text

    _uninstall(e2e_client, headers, sid)

    refused = _install(e2e_client, headers, zip_bytes)
    assert refused.status_code == 409, refused.text
    detail = refused.json()["detail"]
    assert detail["reason"] == "inactive_install_exists", detail
    assert detail["solution_id"] == sid, detail
    assert detail["slug"] == slug, detail

    r2 = _install(e2e_client, headers, zip_bytes, query="?reactivate=true")
    assert r2.status_code in (200, 201), f"Expected 200/201 on reactivate, got {r2.status_code}: {r2.text}"
    reactivated = r2.json()

    assert reactivated["id"] == sid, (
        f"Reactivate must return the SAME install id, not a new one. "
        f"Original: {sid}, returned: {reactivated['id']}"
    )
    assert reactivated["status"] == "active", (
        f"Reactivated install must have status='active', got: {reactivated['status']}"
    )

    # Only ONE Solution with this slug in this org (no duplicate row).
    list_r = e2e_client.get("/api/solutions?scope=global", headers=headers)
    assert list_r.status_code == 200, list_r.text
    matching = [s for s in list_r.json()["solutions"] if s["slug"] == slug]
    assert len(matching) == 1, (
        f"Expected exactly one install for slug '{slug}', found {len(matching)}: {matching}"
    )

    got = e2e_client.get(
        f"/api/tables/{table_name}/documents/{tbl_doc_id}?solution={sid}",
        headers=headers,
    )
    assert got.status_code == 200, got.text
    assert got.json().get("data", {}).get("val") == "retained", got.text
