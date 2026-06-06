"""E2E: GET /api/solutions/{id}/entities aggregate — returns the install plus
everything it owns (workflows/apps/forms/agents/tables) and its config
declarations paired with whether each has a value set (admin only)."""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.e2e


def _create_solution(e2e_client, headers, slug: str) -> str:
    r = e2e_client.post("/api/solutions", headers=headers, json={
        "slug": slug, "name": slug.upper(), "scope": "global",
    })
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


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
