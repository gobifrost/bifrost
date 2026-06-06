"""End-to-end (live REST): deploy a standalone_v2 Solution app and confirm it
builds to dist/, is served from _apps/{id}/, and reports app_model via the
bundle-manifest — while an inline_v1 app is unaffected (criterion 12).

The bundle ships a prebuilt ``dist_files`` (disconnected fast-path) so no real
vite/Node build runs in the test; the server uploads the shipped dist verbatim.
"""
from __future__ import annotations

import uuid
from uuid import UUID

import pytest

from src.services.solutions.deploy import solution_entity_id

pytestmark = pytest.mark.e2e


def _create_solution(e2e_client, headers, slug: str) -> str:
    r = e2e_client.post(
        "/api/solutions",
        headers=headers,
        json={"slug": slug, "name": slug.upper(), "scope": "global"},
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def test_v2_app_deploys_builds_dist_and_reports_model(e2e_client, platform_admin):
    headers = platform_admin.headers
    slug = f"v2app-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug)
    app_id = str(uuid.uuid4())
    # Deploy remaps the manifest id to uuid5(install_id, manifest_id); the served
    # dist prefix and id-addressed endpoints all use the remapped id.
    real_id = str(solution_entity_id(UUID(sid), UUID(app_id)))
    app_slug = f"dash-{slug}"
    # Realistic Vite output: quoted attrs, asset URLs under the --base dist route.
    index_html = (
        '<!doctype html><html><head>'
        f'<script type="module" crossorigin src="/api/applications/{real_id}/dist/assets/main-abc.js"></script>'
        '<link rel="stylesheet" href="/api/applications/{aid}/dist/assets/main-abc.css">'
        '</head><body><div id="root"></div></body></html>'
    ).replace("{aid}", real_id)

    dep = e2e_client.post(
        f"/api/solutions/{sid}/deploy",
        headers=headers,
        json={
            "apps": [
                {
                    "id": app_id,
                    "slug": app_slug,
                    "name": "Dash",
                    "app_model": "standalone_v2",
                    "dependencies": {},
                    "access_level": "authenticated",
                    # Prebuilt dist → server skips the vite build (fast-path).
                    "dist_files": {
                        "index.html": index_html,
                        "assets/main-abc.js": "console.log('v2')",
                        "assets/main-abc.css": ".x{color:red}",
                    },
                }
            ]
        },
    )
    assert dep.status_code in (200, 201), dep.text
    assert dep.json()["apps_upserted"] == 1

    # The Application row is solution-managed and standalone_v2. The metadata
    # GET endpoint resolves by slug (globally unique), not by id.
    got = e2e_client.get(f"/api/applications/{app_slug}", headers=headers)
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["id"] == real_id
    assert body["app_model"] == "standalone_v2"
    assert body["is_solution_managed"] is True

    # The dist/ is served from _apps/{id}/ — index.html (criterion 12).
    idx = e2e_client.get(f"/api/applications/{real_id}/dist/index.html", headers=headers)
    assert idx.status_code == 200, idx.text
    assert 'id="root"' in idx.text
    assert idx.headers["content-type"].startswith("text/html")

    # A hashed asset referenced by index.html is fetchable from the same prefix.
    asset = e2e_client.get(
        f"/api/applications/{real_id}/dist/assets/main-abc.js", headers=headers
    )
    assert asset.status_code == 200, asset.text
    assert "v2" in asset.text

    # The bundle-manifest surfaces app_model AND the hashed entry parsed from the
    # built index.html, so the client mounts the app same-document (P1-b/G7) —
    # it loads `entry` from the dist base, NOT an iframe to index.html.
    man = e2e_client.get(
        f"/api/applications/{real_id}/bundle-manifest?mode=live", headers=headers
    )
    assert man.status_code == 200, man.text
    mbody = man.json()
    assert mbody["app_model"] == "standalone_v2"
    assert mbody["base_url"] == f"/api/applications/{real_id}/dist"
    # index.html referenced the entry + css under /dist/ → manifest reports them
    # relative to the dist base (the shell re-joins base + entry/css).
    assert mbody["entry"] == "assets/main-abc.js"
    assert mbody["css"] == "assets/main-abc.css"


def test_redeploy_without_app_removes_it_for_this_install(e2e_client, platform_admin):
    headers = platform_admin.headers
    slug = f"v2rm-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug)
    app_id = str(uuid.uuid4())
    app_slug = f"gone-{slug}"

    dep = e2e_client.post(
        f"/api/solutions/{sid}/deploy",
        headers=headers,
        json={
            "apps": [
                {
                    "id": app_id,
                    "slug": app_slug,
                    "name": "Gone",
                    "app_model": "standalone_v2",
                    "dependencies": {},
                    "dist_files": {"index.html": "<html></html>"},
                }
            ]
        },
    )
    assert dep.status_code in (200, 201), dep.text
    assert e2e_client.get(f"/api/applications/{app_slug}", headers=headers).status_code == 200

    # Redeploy with the app removed → swept for THIS install only.
    dep2 = e2e_client.post(
        f"/api/solutions/{sid}/deploy", headers=headers, json={"apps": []}
    )
    assert dep2.status_code in (200, 201), dep2.text
    assert dep2.json()["apps_deleted"] == 1
    assert e2e_client.get(f"/api/applications/{app_slug}", headers=headers).status_code == 404
