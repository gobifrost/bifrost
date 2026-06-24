"""E2E: GET /api/solutions/{id}/entities aggregate — returns the install plus
everything it owns (workflows/apps/forms/agents/tables) and its config
declarations paired with whether each has a value set (admin only)."""
from __future__ import annotations

import base64
import uuid
from uuid import UUID

import pytest

from src.models.orm.solution_file_location import SolutionFileLocation
from src.services.solutions.deploy import solution_entity_id
from tests.e2e.platform.conftest import wait_for_deploy

pytestmark = pytest.mark.e2e


async def _declare_file_location(db_session, solution_id: str, location: str) -> None:
    db_session.add(
        SolutionFileLocation(solution_id=UUID(solution_id), location=location)
    )
    await db_session.commit()

CLEAN_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xfa\xcf\x00\x00\x00\x02\x00\x01\xe5'\xde\xfc"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _create_solution(e2e_client, headers, slug: str) -> str:
    r = e2e_client.post("/api/solutions", headers=headers, json={
        "slug": slug, "name": slug.upper(), "organization_id": None,
    })
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _create_org_solution(e2e_client, headers, slug: str) -> str:
    r = e2e_client.post("/api/solutions", headers=headers, json={
        "slug": slug, "name": slug.upper(), "scope": "org",
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
    dep = wait_for_deploy(e2e_client, dep, headers)
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
    app_id = str(uuid.uuid4())
    real_id = str(solution_entity_id(UUID(sid), UUID(app_id)))

    # Solution-managed apps are standalone_v2 and arrive via deploy (loose-app
    # capture rejects v1, and bare standalone_v2 creation is blocked). A managed
    # app is read-only, so its logo travels IN the deploy payload (a post-deploy
    # logo upload is correctly rejected by the read-only guard). The CLI resolves
    # the manifest's logo path to bytes and sends logo_b64 + logo_content_type;
    # deploy decodes those (deploy.py:_decode_logo). Confirm it round-trips in
    # entities. Deploy remaps the supplied manifest id to solution_entity_id.
    logo_b64 = base64.b64encode(CLEAN_PNG).decode("ascii")
    dep = e2e_client.post(
        f"/api/solutions/{sid}/deploy",
        headers=headers,
        json={
            "apps": [
                {
                    "id": app_id,
                    "slug": app_slug,
                    "name": "Summary App",
                    "app_model": "standalone_v2",
                    "dependencies": {},
                    "access_level": "authenticated",
                    "logo_b64": logo_b64,
                    "logo_content_type": "image/png",
                    "dist_files": {
                        "index.html": '<!doctype html><html><body><div id="root"></div></body></html>',
                    },
                }
            ]
        },
    )
    dep = wait_for_deploy(e2e_client, dep, headers)
    assert dep.status_code in (200, 201), dep.text

    app = {"id": real_id}

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


async def test_get_solution_entities_includes_files(e2e_client, platform_admin, db_session):
    """SolutionEntities.files is populated with files written to the install scope."""
    headers = platform_admin.headers
    slug = f"ent-files-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug)

    # The install must declare the file location before it can write there.
    await _declare_file_location(db_session, sid, "solutions")

    # Seed an allow-all policy on the 'solutions' location (global scope).
    policy_r = e2e_client.put(
        "/api/files/policies/",
        headers=headers,
        params={"location": "solutions"},
        json={"policies": {"policies": [{"name": "allow_all", "actions": ["read", "write", "delete", "list"]}]}},
    )
    assert policy_r.status_code in (200, 201, 204), policy_r.text

    # Initially the files list is empty.
    r0 = e2e_client.get(f"/api/solutions/{sid}/entities", headers=headers)
    assert r0.status_code == 200, r0.text
    assert "files" in r0.json(), f"missing 'files' key: {list(r0.json().keys())}"
    assert r0.json()["files"] == []

    # Write a file into the solution scope.
    write_r = e2e_client.post(
        f"/api/files/write?solution={sid}",
        headers=headers,
        json={"location": "solutions", "path": "data/hello.txt", "content": "hi", "mode": "cloud"},
    )
    assert write_r.status_code == 204, write_r.text

    # Now the entities response should include the file.
    r1 = e2e_client.get(f"/api/solutions/{sid}/entities", headers=headers)
    assert r1.status_code == 200, r1.text
    body = r1.json()
    assert "files" in body, f"missing 'files' key: {list(body.keys())}"
    files = body["files"]
    assert len(files) == 1, f"expected 1 file, got {files}"
    assert files[0]["location"] == "solutions"
    assert files[0]["path"] == "data/hello.txt"
