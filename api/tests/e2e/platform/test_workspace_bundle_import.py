"""Workspace bundle preview/enqueue contracts against the live API boundary."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

pytestmark = pytest.mark.e2e


def _upload_headers(headers: dict[str, str]) -> dict[str, str]:
    return {key: value for key, value in headers.items() if key.lower() != "content-type"}


def _bundle_bytes() -> bytes:
    from src.models.orm.solutions import Solution
    from src.services.solutions.deploy import SolutionBundle
    from src.services.solutions.export import build_workspace_zip

    workflow_id = str(uuid4())
    return build_workspace_zip(SolutionBundle(
        solution=Solution(id=uuid4(), slug=f"workspace-{uuid4().hex[:8]}", name="Workspace test"),
        python_files={"workflows/imported.py": "def imported():\n    return 1\n"},
        workflows=[{
            "id": workflow_id, "name": "imported", "path": "workflows/imported.py",
            "function_name": "imported", "organization_id": None,
        }],
    ))


def _preview(e2e_client, headers: dict[str, str], archive: bytes) -> dict:
    response = e2e_client.post(
        "/api/solutions/import-workspace/preview", headers=_upload_headers(headers),
        files={"file": ("workspace.zip", archive, "application/zip")},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_workspace_bundle_preview_enforces_auth_requester_expiry_and_active_dedupe(
    e2e_client, platform_admin, org1_user,
):
    """A staged preview belongs to its reviewer and one active decision job."""
    archive = _bundle_bytes()
    unauthenticated = e2e_client.post(
        "/api/solutions/import-workspace/preview",
        files={"file": ("workspace.zip", archive, "application/zip")},
    )
    assert unauthenticated.status_code in {401, 403}

    preview = _preview(e2e_client, platform_admin.headers, archive)
    assert {item["classification"] for item in preview["items"]} <= {"create", "unchanged", "conflict"}

    # A second valid platform-admin principal reaches the requester binding,
    # rather than stopping at the route's superuser dependency.
    from tests.fixtures.auth import auth_headers, create_test_jwt
    other_headers = auth_headers(create_test_jwt(
        user_id=str(org1_user.user_id), email=org1_user.email, name=org1_user.name,
        is_superuser=True,
    ))
    other_request = e2e_client.post(
        "/api/solutions/import-workspace", headers=other_headers,
        json={"preview_token": preview["preview_token"], "decisions": []},
    )
    assert other_request.status_code == 404

    from src.services.solutions.workspace_bundle_storage import WorkspaceBundleStorage
    storage = WorkspaceBundleStorage(preview["preview_token"])
    metadata = await storage.load_metadata()
    metadata["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    await storage.stage_metadata(metadata)
    expired = e2e_client.post(
        "/api/solutions/import-workspace", headers=platform_admin.headers,
        json={"preview_token": preview["preview_token"], "decisions": []},
    )
    assert expired.status_code == 410
    await storage.delete()

    active_preview = _preview(e2e_client, platform_admin.headers, archive)
    body = {"preview_token": active_preview["preview_token"], "decisions": []}
    accepted = e2e_client.post("/api/solutions/import-workspace", headers=platform_admin.headers, json=body)
    assert accepted.status_code == 202, accepted.text
    reused = e2e_client.post("/api/solutions/import-workspace", headers=platform_admin.headers, json=body)
    assert reused.status_code == 202, reused.text
    assert reused.json()["job_id"] == accepted.json()["job_id"]
    assert reused.json()["reused"] is True
