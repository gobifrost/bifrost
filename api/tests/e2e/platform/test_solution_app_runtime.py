"""End-to-end security contract for the same-origin isolated app runtime.

Builder-authored applications are served by the API-mounted
``/api/builder-runtime`` sub-application.  The browser document is isolated by
an opaque CSP sandbox while a one-time launch code becomes a path-scoped,
HttpOnly session cookie.  The SDK then receives a short-lived ``solution_app``
actor token with access only to resources owned by that Solution.
"""

from __future__ import annotations

import base64
import json
import uuid
from dataclasses import dataclass
from uuid import UUID

import pytest

from src.models.orm.applications import Application
from src.models.orm.tables import Table
from src.services.builder.private_solutions import create_private_solution
from src.services.solutions.app_build import SolutionAppBuilder

pytestmark = pytest.mark.e2e

BUILDER_URL = "/api/builder/solutions"
RUNTIME_URL = "/api/builder-runtime"


@dataclass(frozen=True)
class RuntimeFixture:
    solution_id: UUID
    app_id: UUID
    table_name: str
    sibling_solution_id: UUID
    sibling_table_name: str


def _claims(token: str) -> dict[str, object]:
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


@pytest.fixture
async def isolated_runtime_app(
    db_session,
    platform_admin,
    org1,
):
    owner_id = platform_admin.user_id
    org_id = UUID(org1["id"])
    suffix = uuid.uuid4().hex[:8]
    solution, _ = await create_private_solution(
        db_session,
        slug=f"runtime-{suffix}",
        name="Runtime security E2E",
        owner_user_id=owner_id,
        organization_id=org_id,
    )
    sibling, _ = await create_private_solution(
        db_session,
        slug=f"runtime-sibling-{suffix}",
        name="Runtime sibling E2E",
        owner_user_id=owner_id,
        organization_id=org_id,
    )
    app = Application(
        name=f"Runtime {suffix}",
        slug=f"runtime-{suffix}",
        description="Same-origin isolated runtime fixture",
        repo_path=f"apps/runtime-{suffix}",
        app_model="standalone_v2",
        runtime_mode="isolated",
        organization_id=org_id,
        solution_id=solution.id,
    )
    table = Table(
        name=f"runtime_notes_{suffix}",
        organization_id=org_id,
        solution_id=solution.id,
        schema={"columns": [{"name": "title", "type": "string"}]},
        access={
            "policies": [
                {
                    "name": "runtime_access",
                    "actions": ["read", "create", "update", "delete"],
                    "when": None,
                }
            ]
        },
    )
    sibling_table = Table(
        name=f"runtime_sibling_{suffix}",
        organization_id=org_id,
        solution_id=sibling.id,
        schema={"columns": [{"name": "title", "type": "string"}]},
        access={
            "policies": [
                {
                    "name": "runtime_access",
                    "actions": ["read", "create", "update", "delete"],
                    "when": None,
                }
            ]
        },
    )
    db_session.add_all([app, table, sibling_table])
    await db_session.commit()

    app_builder = SolutionAppBuilder()
    await app_builder.upload_dist(
        app.id,
        {
            "index.html": (
                b'<!doctype html><html><body><div id="root"></div>'
                b'<script type="module" src="./assets/main-abc.js"></script>'
                b"</body></html>"
            ),
            "assets/main-abc.js": b"export function mount() {}",
        },
    )
    fixture = RuntimeFixture(
        solution_id=solution.id,
        app_id=app.id,
        table_name=table.name,
        sibling_solution_id=sibling.id,
        sibling_table_name=sibling_table.name,
    )
    try:
        yield fixture
    finally:
        await app_builder.delete_dist(app.id)
        await db_session.delete(solution)
        await db_session.delete(sibling)
        await db_session.commit()


def _launch(e2e_client, platform_admin, fixture: RuntimeFixture, path: str = "/"):
    response = e2e_client.post(
        f"{BUILDER_URL}/{fixture.solution_id}/apps/{fixture.app_id}/launch",
        headers=platform_admin.headers,
        params={"path": path},
    )
    assert response.status_code == 200, response.text
    launch_url = response.json()["launch_url"]
    assert launch_url.startswith(f"{RUNTIME_URL}/launch/")
    assert "eyJ" not in launch_url
    return launch_url


def _redeem(e2e_client, launch_url: str):
    response = e2e_client.get(launch_url, follow_redirects=False)
    assert response.status_code == 302, response.text
    return response


async def test_launch_is_single_use_and_serves_sandboxed_entry(
    e2e_client,
    platform_admin,
    isolated_runtime_app: RuntimeFixture,
):
    fixture = isolated_runtime_app
    e2e_client.cookies.clear()
    launch_url = _launch(e2e_client, platform_admin, fixture, "/reports")
    redeemed = _redeem(e2e_client, launch_url)
    app_base = f"{RUNTIME_URL}/{fixture.solution_id}/apps/{fixture.app_id}"
    assert redeemed.headers["location"] == f"{app_base}/reports"
    session_cookie = e2e_client.cookies.get("bifrost_app_session")
    assert session_cookie

    replay = e2e_client.get(launch_url, follow_redirects=False)
    assert replay.status_code == 400

    entry = e2e_client.get(f"{app_base}/reports")
    assert entry.status_code == 200, entry.text
    assert entry.headers["cache-control"] == "no-store"
    csp = entry.headers["content-security-policy"]
    assert "sandbox allow-forms allow-scripts" in csp
    assert "object-src 'none'" in csp
    assert f'{app_base}/assets/main-abc.js' in entry.text
    assert "data-bifrost-session-token" in entry.text

    asset = e2e_client.get(f"{app_base}/assets/main-abc.js")
    assert asset.status_code == 200
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"


async def test_runtime_token_is_attenuated_and_sealed_from_normal_api(
    e2e_client,
    platform_admin,
    isolated_runtime_app: RuntimeFixture,
):
    fixture = isolated_runtime_app
    e2e_client.cookies.clear()
    _redeem(e2e_client, _launch(e2e_client, platform_admin, fixture))
    app_base = f"{RUNTIME_URL}/{fixture.solution_id}/apps/{fixture.app_id}"
    minted = e2e_client.post(f"{app_base}/_bifrost/session-token")
    assert minted.status_code == 200, minted.text
    token = minted.json()["access_token"]
    claims = _claims(token)
    assert claims["actor_type"] == "solution_app"
    assert claims["solution_id"] == str(fixture.solution_id)
    assert claims["app_id"] == str(fixture.app_id)
    assert "tables.documents.read" in claims["scopes"]
    assert "files.content.write" in claims["scopes"]
    assert claims.get("is_superuser") is None

    rejected = e2e_client.get(
        "/api/tables",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert rejected.status_code == 401, rejected.text

    normal_user = e2e_client.get(
        f"{RUNTIME_URL}/_bifrost/api/auth/me",
        headers=platform_admin.headers,
    )
    assert normal_user.status_code == 401, normal_user.text


async def test_runtime_actor_can_use_only_its_solution_resources(
    e2e_client,
    platform_admin,
    isolated_runtime_app: RuntimeFixture,
):
    fixture = isolated_runtime_app
    e2e_client.cookies.clear()
    _redeem(e2e_client, _launch(e2e_client, platform_admin, fixture))
    app_base = f"{RUNTIME_URL}/{fixture.solution_id}/apps/{fixture.app_id}"
    token = e2e_client.post(f"{app_base}/_bifrost/session-token").json()[
        "access_token"
    ]
    actor_headers = {
        "Authorization": f"Bearer {token}",
        "Origin": "null",
    }

    created = e2e_client.post(
        f"{RUNTIME_URL}/_bifrost/api/tables/{fixture.table_name}/documents",
        headers=actor_headers,
        json={"id": "one", "data": {"title": "Private note"}},
    )
    assert created.status_code == 201, created.text
    fetched = e2e_client.get(
        f"{RUNTIME_URL}/_bifrost/api/tables/{fixture.table_name}/documents/one",
        headers=actor_headers,
    )
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["data"] == {"title": "Private note"}
    assert fetched.headers["access-control-allow-origin"] == "null"

    sibling = e2e_client.get(
        (
            f"{RUNTIME_URL}/_bifrost/api/tables/"
            f"{fixture.sibling_table_name}/documents/missing"
        ),
        headers=actor_headers,
    )
    assert sibling.status_code == 404, sibling.text


async def test_app_session_is_bound_to_exact_solution_and_app(
    e2e_client,
    platform_admin,
    isolated_runtime_app: RuntimeFixture,
):
    fixture = isolated_runtime_app
    e2e_client.cookies.clear()
    _redeem(e2e_client, _launch(e2e_client, platform_admin, fixture))
    session_cookie = e2e_client.cookies.get("bifrost_app_session")
    response = e2e_client.get(
        (
            f"{RUNTIME_URL}/{fixture.sibling_solution_id}/apps/"
            f"{fixture.app_id}/index.html"
        ),
        headers={"Cookie": f"bifrost_app_session={session_cookie}"},
    )
    assert response.status_code == 404, response.text
