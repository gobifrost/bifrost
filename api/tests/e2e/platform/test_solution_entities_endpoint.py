"""E2E: GET /api/solutions/{id}/entities aggregate — returns the install plus
everything it owns (workflows/apps/forms/agents/tables) and its config
declarations paired with whether each has a value set (admin only)."""
from __future__ import annotations

import base64
import uuid

import pytest

pytestmark = pytest.mark.e2e

CLEAN_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xfa\xcf\x00\x00\x00\x02\x00\x01\xe5'\xde\xfc"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _create_solution(e2e_client, headers, slug: str) -> str:
    r = e2e_client.post("/api/solutions", headers=headers, json={
        "slug": slug, "name": slug.upper(), "scope": "global",
    })
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _create_org_solution(e2e_client, headers, slug: str) -> str:
    r = e2e_client.post("/api/solutions", headers=headers, json={
        "slug": slug, "name": slug.upper(), "scope": "org",
    })
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _upload_headers(headers):
    """Strip Content-Type so httpx sets it for multipart."""
    return {k: v for k, v in headers.items() if k.lower() != "content-type"}


async def test_get_solution_entities_reports_config_status(e2e_client, platform_admin):
    headers = platform_admin.headers
    slug = f"ent-e2e-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug)

    dep = e2e_client.post(f"/api/solutions/{sid}/deploy", headers=headers, json={
        "config_schemas": [{
            "id": str(uuid.uuid4()), "key": "API_KEY", "type": "secret",
            "required": True, "description": "needed", "position": 0,
        }],
    })
    assert dep.status_code == 200, dep.text

    r = e2e_client.get(f"/api/solutions/{sid}/entities", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()

    for key in ("workflows", "apps", "forms", "agents", "tables", "configs", "required_configs_unset"):
        assert key in body, f"missing key {key}: {body}"

    assert body["solution"]["id"] == sid
    assert "API_KEY" in body["required_configs_unset"]

    api_key = next((c for c in body["configs"] if c["key"] == "API_KEY"), None)
    assert api_key is not None, body["configs"]
    assert api_key["required"] is True
    assert api_key["value_set"] is False

    # Set a value for this global install's scope → API_KEY becomes satisfied.
    sc = e2e_client.post("/api/config", headers=headers, json={
        "key": "API_KEY", "value": "shhh", "type": "secret",
        "organization_id": None,
    })
    assert sc.status_code in (200, 201), sc.text

    r2 = e2e_client.get(f"/api/solutions/{sid}/entities", headers=headers)
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    assert "API_KEY" not in body2["required_configs_unset"]
    api_key2 = next((c for c in body2["configs"] if c["key"] == "API_KEY"), None)
    assert api_key2 is not None
    assert api_key2["value_set"] is True


async def test_get_solution_entities_404(e2e_client, platform_admin):
    r = e2e_client.get(f"/api/solutions/{uuid.uuid4()}/entities", headers=platform_admin.headers)
    assert r.status_code == 404, r.text


async def test_get_solution_entities_includes_app_logo(e2e_client, platform_admin):
    headers = platform_admin.headers
    slug = f"app-logo-summary-{uuid.uuid4().hex[:8]}"
    sid = _create_org_solution(e2e_client, headers, slug)
    app_slug = f"summary-app-{uuid.uuid4().hex[:8]}"

    created = e2e_client.post(
        "/api/applications",
        headers=headers,
        json={"name": "Summary App", "slug": app_slug, "app_model": "inline_v1"},
    )
    assert created.status_code == 201, created.text
    app = created.json()

    uploaded = e2e_client.post(
        f"/api/applications/{app['id']}/logo",
        headers=_upload_headers(headers),
        files={"file": ("logo.png", CLEAN_PNG, "image/png")},
    )
    assert uploaded.status_code == 200, uploaded.text

    captured = e2e_client.post(
        f"/api/solutions/{sid}/capture",
        headers=headers,
        json={
            "workflows": [],
            "tables": [],
            "apps": [app["id"]],
            "forms": [],
            "agents": [],
            "claims": [],
            "configs": [],
        },
    )
    assert captured.status_code == 200, captured.text

    entities = e2e_client.get(f"/api/solutions/{sid}/entities", headers=headers)
    assert entities.status_code == 200, entities.text
    solution_app = next(
        item for item in entities.json()["apps"] if item["id"] == app["id"]
    )
    assert solution_app["logo"] == (
        "data:image/png;base64," + base64.b64encode(CLEAN_PNG).decode("ascii")
    )


async def test_capture_candidates_list_and_capture_loose_config(e2e_client, platform_admin):
    headers = platform_admin.headers
    slug = f"capture-candidates-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug)
    key = f"CAPTURE_{uuid.uuid4().hex[:8].upper()}"

    sc = e2e_client.post("/api/config", headers=headers, json={
        "key": key,
        "value": "present",
        "type": "string",
        "organization_id": None,
    })
    assert sc.status_code in (200, 201), sc.text

    candidates = e2e_client.get(f"/api/solutions/{sid}/capture/candidates", headers=headers)
    assert candidates.status_code == 200, candidates.text
    config_keys = {item["key"] for item in candidates.json()["configs"]}
    assert key in config_keys

    captured = e2e_client.post(
        f"/api/solutions/{sid}/capture",
        headers=headers,
        json={
            "workflows": [],
            "tables": [],
            "apps": [],
            "forms": [],
            "agents": [],
            "claims": [],
            "configs": [key],
        },
    )
    assert captured.status_code == 200, captured.text
    assert captured.json()["config_declarations_captured"] == 1

    candidates_after = e2e_client.get(f"/api/solutions/{sid}/capture/candidates", headers=headers)
    assert candidates_after.status_code == 200, candidates_after.text
    config_keys_after = {item["key"] for item in candidates_after.json()["configs"]}
    assert key not in config_keys_after

    entities = e2e_client.get(f"/api/solutions/{sid}/entities", headers=headers)
    assert entities.status_code == 200, entities.text
    entity_config_keys = {item["key"] for item in entities.json()["configs"]}
    assert key in entity_config_keys
