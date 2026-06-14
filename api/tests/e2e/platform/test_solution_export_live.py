"""Regression: GET /api/solutions/{id}/export must rebuild the workspace zip
LIVE from the entities the solution currently owns — not serve a stale stored
bundle.

Bug: the old endpoint served ``SolutionExportStore().read()``, which is only
written at deploy/capture time. An app captured AFTER the last deploy (or a
bundle otherwise written before the current DB state) would be silently missing.

Fix: the endpoint calls ``SolutionCaptureService.bundle_for()`` +
``build_workspace_zip()`` on every request, so the zip always reflects live
ownership.
"""
from __future__ import annotations

import io
import uuid
import zipfile

import pytest

from src.services.solutions.export import SolutionExportStore

pytestmark = pytest.mark.e2e


async def test_export_reflects_currently_owned_app_not_stale_cache(
    e2e_client, platform_admin
):
    """Regression: export used to serve a stale stored zip, so an app that
    exists in the DB but whose zip was deleted/stale would be missing. Export
    must rebuild live from owned entities.

    Setup: deploy a solution with a standalone_v2 app (creates the DB row +
    writes the export store zip), then delete the export store zip to simulate
    a stale/missing cache. The endpoint must still return the app in the zip.
    """
    headers = platform_admin.headers
    slug = f"export-live-{uuid.uuid4().hex[:8]}"
    app_slug = f"dash-{slug}"

    # 1. Create the solution.
    sol_r = e2e_client.post(
        "/api/solutions",
        headers=headers,
        json={"slug": slug, "name": slug.upper(), "scope": "global"},
    )
    assert sol_r.status_code in (200, 201), sol_r.text
    sol_id = sol_r.json()["id"]

    # 2. Deploy with a standalone_v2 app. This writes the app to the DB (owned
    #    by the solution) AND writes an export zip to SolutionExportStore.
    dep = e2e_client.post(
        f"/api/solutions/{sol_id}/deploy",
        headers=headers,
        json={
            "apps": [
                {
                    "id": str(uuid.uuid4()),
                    "slug": app_slug,
                    "name": "Dashboard",
                    "app_model": "standalone_v2",
                    "dependencies": {},
                    "dist_files": {"index.html": "<html><body>hello</body></html>"},
                }
            ]
        },
    )
    assert dep.status_code in (200, 201), dep.text
    assert dep.json()["apps_upserted"] == 1

    # 3. Delete the export store zip — now there is NO stale bundle, but the DB
    #    still has the app owned by this solution.
    await SolutionExportStore().delete(sol_id)

    # 4. Export must rebuild live and include the app.
    resp = e2e_client.get(f"/api/solutions/{sol_id}/export", headers=headers)
    assert resp.status_code == 200, resp.text
    names = zipfile.ZipFile(io.BytesIO(resp.content)).namelist()
    # The app is serialized into .bifrost/apps.yaml (the manifest); source files
    # appear under apps/ only when the app has repo source. Either presence
    # proves the live rebuild captured the app.
    assert ".bifrost/apps.yaml" in names or any(n.startswith("apps/") for n in names), (
        f"Expected app serialized in zip but got: {names}"
    )
