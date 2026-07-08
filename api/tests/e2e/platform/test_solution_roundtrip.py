"""Round-trip E2E: export a deployed solution (app + workflow) then install the
zip into a DIFFERENT org and confirm both entities land in the target org.

Task 4 of the Solutions success-criteria programme.

Design notes:
- Uses the same sync ``e2e_client`` + ``platform_admin`` pattern as sibling
  tests (no async httpx needed — the stack exposes blocking httpx at
  ``tests/e2e/conftest.py:e2e_client``).
- The source solution is deployed with a ``standalone_v2`` app carrying a
  pre-built ``dist_files`` payload (bypasses the Vite build so the test stack
  doesn't need npm) and a Python workflow file.
- Export produces a live-rebuilt zip (Task 1/2 rebuild path — no stale cache).
- Install targets a fresh org created inline; the slug is unique per run so a
  fresh install is always created for the target org.
- Assertions check GET /api/solutions/{id}/entities for at least one app and
  one workflow — not just that install returned 200.
"""

from __future__ import annotations

import asyncio
import base64
import uuid

import pytest
from tests.e2e.platform.conftest import wait_for_deploy, wait_for_install

pytestmark = pytest.mark.e2e


# A tiny but real 1x1 PNG (8-byte signature + IHDR + IDAT + IEND). Its bytes are
# deliberately NOT valid UTF-8 (0x89, 0xC4, etc.) so it exercises the binary
# (bin_dist_files) round-trip path, not the UTF-8 text path.
TINY_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xfa\xcf\x00\x00\x00\x02\x00\x01\xe5'\xde\xfc"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)
ASSET_REL = "assets/logo.png"


def _upload_headers(headers: dict[str, str]) -> dict[str, str]:
    """Strip Content-Type so httpx sets it correctly for multipart requests."""
    return {k: v for k, v in headers.items() if k.lower() != "content-type"}


def _create_solution_with_app_and_workflow(
    e2e_client, headers: dict[str, str], *, org_id: str
) -> str:
    """Deploy an org-scoped solution that owns one standalone_v2 app + one workflow.

    Returns the solution id.  Uses ``dist_files`` (pre-built HTML) + a binary
    ``dist_files`` asset (a PNG, base64-encoded) to avoid a Vite build in the
    test stack while still exercising the non-UTF-8 dist asset path.  The
    solution is org-scoped so that a second install of the SAME zip into a
    DIFFERENT org does not collide (two purely cross-org apps for different orgs
    are not visible to each other — deploy collision only fires when one of them
    is global/NULL-scoped).
    """
    slug = f"rt-src-{uuid.uuid4().hex[:8]}"
    app_slug = f"rt-app-{uuid.uuid4().hex[:8]}"
    wf_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{slug}/wf/main"))

    sol_r = e2e_client.post(
        "/api/solutions",
        headers=headers,
        json={"slug": slug, "name": slug.upper(), "scope": "org", "organization_id": org_id},
    )
    assert sol_r.status_code in (200, 201), sol_r.text
    sol_id = sol_r.json()["id"]

    dep = e2e_client.post(
        f"/api/solutions/{sol_id}/deploy",
        headers=headers,
        json={
            "apps": [
                {
                    "id": str(uuid.uuid4()),
                    "slug": app_slug,
                    "name": "Round-Trip App",
                    "app_model": "standalone_v2",
                    "dependencies": {},
                    # Text dist entry (HTML) + a binary dist asset. The deploy
                    # request accepts both under dist_files; the deployer encodes
                    # str values to UTF-8. A binary PNG cannot ride dist_files as
                    # raw text, so it is carried base64 under bin_dist_files —
                    # the path this test is proving round-trips correctly.
                    "dist_files": {"index.html": "<html><body>roundtrip</body></html>"},
                    "bin_dist_files": {
                        ASSET_REL: base64.b64encode(TINY_PNG).decode("ascii")
                    },
                }
            ],
            "workflows": [
                {
                    "id": wf_id,
                    "name": "main",
                    "function_name": "run",
                    "path": f"workflows/{slug}_main.py",
                    "content": "def run(sdk):\n    return 'rt'\n",
                }
            ],
        },
    )
    dep = wait_for_deploy(e2e_client, dep, headers)
    assert dep.status_code in (200, 201), dep.text
    body = dep.json()
    assert body["apps_upserted"] == 1, f"expected 1 app, got: {body}"
    assert body["workflows_upserted"] == 1, f"expected 1 workflow, got: {body}"

    return sol_id


def test_shareable_export_installs_into_fresh_org(e2e_client, platform_admin):
    """Export a deployed solution (app + workflow) and install into a fresh org.

    Proves:
    1. Export produces a re-installable zip from live entity state.
    2. Install into a different (non-global) org scope succeeds.
    3. The installed solution is owned by the target org.
    4. Both the app and the workflow land in the installed solution.

    Both source and target are org-scoped (not global) to avoid the cross-org
    app-slug collision that the global scope check enforces: a purely cross-org
    pair (two different non-global orgs) is allowed because neither org sees the
    other's apps.
    """
    headers = platform_admin.headers
    upload_headers = _upload_headers(headers)

    # --- Create two orgs: source and target ---
    def _create_org(suffix: str) -> str:
        domain = f"rt-{uuid.uuid4().hex[:8]}-{suffix}.test"
        r = e2e_client.post(
            "/api/organizations",
            headers=headers,
            json={"name": f"RT {suffix.upper()} Org {domain}", "domain": domain},
        )
        assert r.status_code == 201, r.text
        return r.json()["id"]

    src_org_id = _create_org("src")
    target_org_id = _create_org("target")

    # --- Source solution: org-scoped deploy with an app + workflow ---
    src_id = _create_solution_with_app_and_workflow(
        e2e_client, headers, org_id=src_org_id
    )

    # --- Export the source solution ---
    export_r = e2e_client.post(f"/api/solutions/{src_id}/export", json={}, headers=headers)
    assert export_r.status_code == 200, export_r.text
    assert export_r.headers.get("content-type") == "application/zip"
    zip_bytes = export_r.content
    assert len(zip_bytes) > 0, "export returned empty body"

    # --- Install the exported zip into the target org ---
    inst_r = wait_for_install(
        e2e_client,
        e2e_client.post(
            "/api/solutions/install",
            headers=upload_headers,
            files={"file": ("solution.zip", zip_bytes, "application/zip")},
            data={"organization_id": target_org_id},
        ),
        headers,
    )
    assert inst_r.status_code in (200, 201), inst_r.text
    installed_id = inst_r.json()["id"]

    # The source and target are different installs (different orgs, different rows).
    assert installed_id != src_id, (
        "install into a different org must produce a new install row"
    )

    # --- Installed solution must be scoped to the target org ---
    detail_r = e2e_client.get(f"/api/solutions/{installed_id}", headers=headers)
    assert detail_r.status_code == 200, detail_r.text
    assert detail_r.json()["organization_id"] == target_org_id, (
        f"installed solution org {detail_r.json()['organization_id']!r} "
        f"!= target org {target_org_id!r}"
    )

    # --- Entities must include at least one app AND one workflow ---
    ent_r = e2e_client.get(f"/api/solutions/{installed_id}/entities", headers=headers)
    assert ent_r.status_code == 200, ent_r.text
    entities = ent_r.json()

    assert len(entities["apps"]) >= 1, (
        f"installed solution has no apps; entities: {entities}"
    )
    assert len(entities["workflows"]) >= 1, (
        f"installed solution has no workflows; entities: {entities}"
    )

    # --- The binary dist asset must round-trip BYTE-FOR-BYTE ---
    # Read the installed app's dist asset straight from S3 (the same store the
    # platform serves the standalone app from) and compare to the original PNG.
    # If bin_dist_files were folded into dist_files, the deployer would have
    # UTF-8-encoded the base64 TEXT and written that to S3, so these bytes would
    # NOT equal TINY_PNG — this assertion fails before the bin_dist_files fix.
    installed_app_id = entities["apps"][0]["id"]

    async def _read_installed_asset() -> bytes:
        from src.services.solutions.app_build import SolutionAppBuilder

        return await SolutionAppBuilder().read_dist(installed_app_id, ASSET_REL)

    stored = asyncio.run(_read_installed_asset())
    assert stored == TINY_PNG, (
        "binary dist asset was corrupted on round-trip: "
        f"stored {len(stored)} bytes, expected {len(TINY_PNG)}"
    )
