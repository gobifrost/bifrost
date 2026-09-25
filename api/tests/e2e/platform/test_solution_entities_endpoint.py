"""E2E: GET /api/solutions/{id}/entities aggregate — returns the install plus
everything it owns (workflows/apps/forms/agents/tables) and its config
declarations paired with whether each has a value set (admin only)."""
from __future__ import annotations

import io
import uuid
from uuid import UUID

import pytest
from PIL import Image
from sqlalchemy import select

from shared.logo_processing import process_logo
from src.models.orm.applications import Application
from src.models.orm.solution_config_schema import SolutionConfigSchema
from src.models.orm.solution_file_location import SolutionFileLocation
from src.services.solutions.deploy import solution_entity_id

pytestmark = pytest.mark.e2e


async def _declare_file_location(db_session, solution_id: str, location: str) -> None:
    db_session.add(
        SolutionFileLocation(solution_id=UUID(solution_id), location=location)
    )
    await db_session.commit()

_png_buffer = io.BytesIO()
Image.new("RGBA", (1, 1), (255, 0, 0, 255)).save(_png_buffer, "PNG")
CLEAN_PNG = _png_buffer.getvalue()


def _create_solution(e2e_client, headers, slug: str) -> str:
    r = e2e_client.post("/api/solutions", headers=headers, json={
        "slug": slug, "name": slug.upper(), "organization_id": None,
    })
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def test_get_solution_entities_reports_config_status_and_app_logo(
    e2e_client, platform_admin, db_session,
):
    headers = platform_admin.headers
    slug = f"ent-e2e-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug)
    app_id = str(uuid.uuid4())
    real_app_id = str(solution_entity_id(UUID(sid), UUID(app_id)))
    logo = process_logo(CLEAN_PNG, "image/png")
    db_session.add_all([
        SolutionConfigSchema(
            solution_id=UUID(sid),
            key="API_KEY",
            type="secret",
            required=True,
            description="needed",
            position=0,
        ),
        Application(
            id=UUID(real_app_id),
            solution_id=UUID(sid),
            slug=f"summary-app-{uuid.uuid4().hex[:8]}",
            name="Summary App",
            app_model="standalone_v2",
            dependencies={},
            access_level="authenticated",
            logo_data=logo.original_data,
            logo_content_type=logo.original_content_type,
            logo_thumbnail_data=logo.thumbnail_data,
            logo_thumbnail_content_type=logo.thumbnail_content_type,
            logo_thumbnail_version=logo.thumbnail_version,
        ),
    ])
    await db_session.commit()

    declarations = (await db_session.execute(
        select(SolutionConfigSchema).where(
            SolutionConfigSchema.solution_id == UUID(sid)
        )
    )).scalars().all()
    assert len(declarations) == 1
    assert declarations[0].key == "API_KEY"
    assert declarations[0].type == "secret"
    assert declarations[0].required is True
    assert declarations[0].description == "needed"

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
    solution_app = next(item for item in body["apps"] if item["id"] == real_app_id)
    assert solution_app["logo"] is None
    assert solution_app["logo_url"].startswith(
        f"/api/applications/{real_app_id}/logo?v="
    )

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
