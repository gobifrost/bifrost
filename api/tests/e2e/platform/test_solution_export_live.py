"""Regression: GET /api/solutions/{id}/export must rebuild the workspace zip
LIVE from the entities the solution currently owns.

The old endpoint served a stale S3-cached zip written at deploy/capture time.
The fix (Task 1) rebuilt the endpoint to call
``SolutionCaptureService.bundle_for()`` + ``build_workspace_zip()`` on every
request. Task 2 deleted ``SolutionExportStore`` entirely — no cache exists to
go stale.

This test verifies the live-rebuild path: deploy a solution with an app, then
immediately export and confirm the app appears in the zip (no warm-up needed,
no cache to bust).
"""
from __future__ import annotations

import io
import uuid
import zipfile

import pytest
from tests.e2e.platform.conftest import wait_for_deploy

pytestmark = pytest.mark.e2e


async def test_export_reflects_currently_owned_app(
    e2e_client, platform_admin
):
    """Export must rebuild live from owned entities and include the deployed app."""
    headers = platform_admin.headers
    slug = f"export-live-{uuid.uuid4().hex[:8]}"
    app_slug = f"dash-{slug}"

    # 1. Create the solution.
    sol_r = e2e_client.post(
        "/api/solutions",
        headers=headers,
        json={"slug": slug, "name": slug.upper(), "organization_id": None},
    )
    assert sol_r.status_code in (200, 201), sol_r.text
    sol_id = sol_r.json()["id"]

    # 2. Deploy with a standalone_v2 app — writes the app to the DB.
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
    dep = wait_for_deploy(e2e_client, dep, headers)
    assert dep.status_code in (200, 201), dep.text
    assert dep.json()["apps_upserted"] == 1

    # 3. Export must rebuild live and include the app.
    resp = e2e_client.post(f"/api/solutions/{sol_id}/export", json={}, headers=headers)
    assert resp.status_code == 200, resp.text
    names = zipfile.ZipFile(io.BytesIO(resp.content)).namelist()
    # The app is serialized into .bifrost/apps.yaml (the manifest); source files
    # appear under apps/ only when the app has repo source. Either presence
    # proves the live rebuild captured the app.
    assert ".bifrost/apps.yaml" in names or any(n.startswith("apps/") for n in names), (
        f"Expected app serialized in zip but got: {names}"
    )
