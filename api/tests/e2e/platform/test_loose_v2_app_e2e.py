"""Loose standalone_v2 staging for v1-to-Solution migrations."""

from __future__ import annotations

import json
import re
import uuid

import pytest

pytestmark = pytest.mark.e2e


def _write(e2e_client, headers, path: str, content: str) -> None:
    response = e2e_client.put(
        "/api/files/editor/content",
        headers=headers,
        json={"path": path, "content": content, "encoding": "utf-8"},
    )
    assert response.status_code in (200, 201), response.text


def test_loose_v2_app_builds_serves_and_remains_capture_eligible(
    e2e_client, platform_admin
):
    headers = platform_admin.headers
    slug = f"loose-v2-{uuid.uuid4().hex[:8]}"
    prefix = f"apps/{slug}"
    package = {
        "name": slug,
        "private": True,
        "type": "module",
        "scripts": {"build": "vite build"},
        "devDependencies": {"vite": "^8.1.4"},
    }
    paths = [
        f"{prefix}/package.json",
        f"{prefix}/index.html",
        f"{prefix}/src/main.js",
    ]
    _write(e2e_client, headers, paths[0], json.dumps(package))
    _write(
        e2e_client,
        headers,
        paths[1],
        '<!doctype html><div id="root"></div><script type="module" src="/src/main.js"></script>',
    )
    _write(
        e2e_client,
        headers,
        paths[2],
        'document.querySelector("#root").textContent = "loose v2 staged";',
    )

    created = e2e_client.post(
        "/api/applications",
        headers=headers,
        json={
            "name": "Loose V2 Staging",
            "slug": slug,
            "app_model": "standalone_v2",
            "organization_id": None,
        },
    )
    assert created.status_code == 201, created.text
    app = created.json()
    app_id = app["id"]
    assert app["app_model"] == "standalone_v2"
    assert app["organization_id"] is None
    assert app["is_solution_managed"] is False

    index = e2e_client.get(
        f"/api/applications/{app_id}/dist/index.html", headers=headers
    )
    assert index.status_code == 200, index.text
    match = re.search(r'<script[^>]+src="([^"]+)"', index.text)
    assert match is not None
    asset_path = match.group(1).split(f"/api/applications/{app_id}/dist/")[-1]
    asset = e2e_client.get(
        f"/api/applications/{app_id}/dist/{asset_path}", headers=headers
    )
    assert asset.status_code == 200, asset.text
    assert "loose v2 staged" in asset.text

    solution = e2e_client.post(
        "/api/solutions",
        headers=headers,
        json={
            "slug": f"capture-{slug}",
            "name": "Loose V2 Capture Target",
            "organization_id": None,
        },
    )
    assert solution.status_code in (200, 201), solution.text
    candidates = e2e_client.get(
        f"/api/solutions/{solution.json()['id']}/capture/candidates",
        headers=headers,
    )
    assert candidates.status_code == 200, candidates.text
    assert app_id in {candidate["id"] for candidate in candidates.json()["apps"]}
