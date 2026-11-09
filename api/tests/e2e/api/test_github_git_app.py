"""Real settings endpoints and storage with the test-stack GitHub provider.

No worker jobs here: the single browser journey covers first-connect dispatch.
These cases own the callback, secret-storage and credential-migration contracts.
"""

import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from src.models.orm.config import SystemConfig
from src.models.orm.users import User
from src.services.github_config import get_github_config, save_github_config


@pytest_asyncio.fixture(autouse=True)
async def preserve_git_configuration(db_session, platform_admin):
    user = await db_session.get(User, platform_admin.user_id)
    scope = user.organization_id
    query = select(SystemConfig).where(SystemConfig.category == "github", SystemConfig.key == "integration", SystemConfig.organization_id == scope)
    original = (await db_session.execute(query)).scalar_one_or_none()
    saved = copy.deepcopy(original.value_json) if original else None
    started_at = datetime.now(timezone.utc)
    yield scope
    await db_session.execute(delete(SystemConfig).where(SystemConfig.category == "github_setup", SystemConfig.created_by == str(platform_admin.user_id), SystemConfig.created_at >= started_at))
    if original:
        original.value_json = saved
    else:
        await db_session.execute(delete(SystemConfig).where(SystemConfig.category == "github", SystemConfig.key == "integration", SystemConfig.organization_id == scope))
    await db_session.commit()


def start(client, admin):
    response = client.post("/api/github/app/manifest", headers=admin.headers, json={"owner": "thin-git-app", "name": "Bifrost Fixture App"})
    assert response.status_code == 200, response.text
    result = response.json()
    return result, parse_qs(urlsplit(result["registration_url"]).query)["state"][0]


def register(client, admin):
    result, state = start(client, admin)
    response = client.post("/api/github/app/manifest/complete", headers=admin.headers, json={"code": "fixture-manifest-" + result["setup_id"], "state": state})
    assert response.status_code == 200, response.text
    return response.json(), state


def authorize(client, admin, setup_id):
    response = client.post("/api/github/app/authorize", headers=admin.headers, json={"setup_id": setup_id})
    assert response.status_code == 200, response.text
    state = parse_qs(urlsplit(response.json()["authorization_url"]).query)["state"][0]
    response = client.post("/api/github/app/authorize/complete", headers=admin.headers, json={"code": "fixture-oauth-code", "state": state})
    assert response.status_code == 200, response.text
    return response.json(), state


@pytest.mark.asyncio
async def test_manifest_callback_encrypts_credentials_and_replays_without_registration(e2e_client, platform_admin, db_session, preserve_git_configuration):
    started, state = start(e2e_client, platform_admin)
    body = {"code": "fixture-manifest-" + started["setup_id"], "state": state}
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: e2e_client.post("/api/github/app/manifest/complete", headers=platform_admin.headers, json=body), range(2)))
    assert [response.status_code for response in responses] == [200, 200]
    setup = responses[0].json()
    assert responses[1].json() == setup
    assert setup["stage"] == "authorization"
    row = (await db_session.execute(select(SystemConfig).where(SystemConfig.key == setup["setup_id"], SystemConfig.category == "github_setup"))).scalar_one()
    assert b"PRIVATE KEY" not in row.value_bytes
    assert b"fixture-secret" not in row.value_bytes
    assert "private_key" not in str(row.value_json)
    repeated = e2e_client.post("/api/github/app/manifest/complete", headers=platform_admin.headers, json=body)
    assert repeated.status_code == 200
    assert repeated.json() == setup
    assert "fixture-secret" not in repeated.text
    from fastapi import HTTPException
    from shared.github_git_app import _row
    with pytest.raises(HTTPException) as error:
        await _row(db_session, UUID(setup["setup_id"]), str(uuid4()), preserve_git_configuration)
    assert error.value.status_code == 404


def test_authorization_callback_is_single_use_and_repository_binding_is_verified(e2e_client, platform_admin):
    setup, _ = register(e2e_client, platform_admin)
    authorized, state = authorize(e2e_client, platform_admin, setup["setup_id"])
    assert authorized["installations"] == [{"id": 2718, "account": "thin-git-app"}]
    repeated = e2e_client.post("/api/github/app/authorize/complete", headers=platform_admin.headers, json={"code": "fixture-oauth-code", "state": state})
    assert repeated.status_code == 409
    for installation_id, repository_id in [(999, 1618), (2718, 999)]:
        response = e2e_client.post("/api/github/app/select", headers=platform_admin.headers, json={"setup_id": setup["setup_id"], "installation_id": installation_id, "repository_id": repository_id})
        assert response.status_code == 403, response.text
    forged = e2e_client.post("/api/github/app/manifest/complete", headers=platform_admin.headers, json={"code": "fixture-manifest-code", "state": "manifest.forged"})
    assert forged.status_code == 403


def test_uncertain_registration_does_not_repeat_conversion(e2e_client, platform_admin):
    setup, state = start(e2e_client, platform_admin)
    response = e2e_client.post("/api/github/app/manifest/complete", headers=platform_admin.headers, json={"code": "unconfirmed-code", "state": state})
    assert response.status_code == 409
    response = e2e_client.post("/api/github/app/manifest/complete", headers=platform_admin.headers, json={"code": "fixture-manifest-code", "state": state})
    assert response.status_code == 409
    latest = e2e_client.get("/api/github/app/setup", headers=platform_admin.headers)
    assert latest.json()["stage"] == "registration_needs_review"


@pytest.mark.asyncio
async def test_verified_pat_migration_replaces_secret_and_keeps_repository(e2e_client, platform_admin, db_session, preserve_git_configuration):
    scope = preserve_git_configuration
    await save_github_config(db_session, scope, "old-fixture-pat", "https://github.com/thin-git-app/workspace", "main", platform_admin.email)
    setup, _ = register(e2e_client, platform_admin)
    before = await get_github_config(db_session, scope)
    assert before.token == "old-fixture-pat"
    await db_session.commit()
    authorize(e2e_client, platform_admin, setup["setup_id"])
    selected = e2e_client.post("/api/github/app/select", headers=platform_admin.headers, json={"setup_id": setup["setup_id"], "installation_id": 2718, "repository_id": 1618})
    assert selected.status_code == 200, selected.text
    assert selected.json()["stage"] == "connected"
    config = await get_github_config(db_session, scope)
    assert config.auth_kind == "app" and config.token is None
    assert config.repo_url == "https://github.com/thin-git-app/workspace"
    assert config.branch == "main"
    response = e2e_client.get("/api/github/config", headers=platform_admin.headers)
    assert response.json()["auth_kind"] == "app"
    assert "fixture-git-app-installation" not in response.text
    assert "PRIVATE KEY" not in response.text
