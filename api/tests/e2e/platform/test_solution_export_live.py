"""Regression: GET /api/solutions/{id}/export must rebuild the workspace zip
LIVE from the entities the solution currently owns.

The old endpoint served a stale S3-cached zip written at deploy/capture time.
The fix (Task 1) rebuilt the endpoint to call
``SolutionCaptureService.bundle_for()`` + ``build_workspace_zip()`` on every
request. Task 2 deleted ``SolutionExportStore`` entirely — no cache exists to
go stale.

This test verifies the live-rebuild path: add an owned app after creating the
install, then export and confirm the current DB state appears in the zip.
"""
from __future__ import annotations

import io
import uuid
import zipfile

import pytest
import yaml

from src.models.orm.applications import Application

pytestmark = pytest.mark.e2e


async def test_export_reflects_currently_owned_app(
    e2e_client, platform_admin, db_session,
):
    """Export must rebuild live from owned entities, including a newly added app."""
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

    # 2. Add the owned app after install creation. Deployment itself is covered
    # by Solution deploy tests; this checks export's live ownership query.
    db_session.add(
        Application(
            name="Dashboard",
            slug=app_slug,
            app_model="standalone_v2",
            repo_path=f"apps/{app_slug}",
            dependencies={},
            solution_id=uuid.UUID(sol_id),
        )
    )
    await db_session.commit()

    # 3. Export must rebuild live and include the app.
    resp = e2e_client.post(f"/api/solutions/{sol_id}/export", json={}, headers=headers)
    assert resp.status_code == 200, resp.text
    with zipfile.ZipFile(io.BytesIO(resp.content)) as archive:
        apps = yaml.safe_load(archive.read(".bifrost/apps.yaml"))["apps"]
    assert any(item["slug"] == app_slug for item in apps.values()), apps
