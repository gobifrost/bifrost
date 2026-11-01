"""Live E2E: runtime tokens carry claims, are refused when stale, and refresh (plan §9.4).

Each test builds a live run's durable rows, obtains its claims access token
from the running API (``/auth/refresh`` with the run's refresh token, as a
worker child does) and drives the real API with it, with raw HTTP to observe
the refusals and with the SDK's ``BifrostClient`` to prove the
challenge-driven refresh. One test runs a real workflow so the worker
child's own refresh path is covered too.

Unit coverage of the same contracts (no live stack) lives in
``tests/unit/authorization/test_validator.py`` and
``tests/unit/sdk/test_refreshing_transport.py``.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
import jwt
import pytest
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncEngine

from bifrost import client as client_mod
from shared.builtin_roles import PLATFORM_ADMIN_ROLE_ID
from src.config import get_settings
from src.models.enums import ExecutionStatus
from src.models.orm.users import UserRole, UserRoleBoundary
from src.services.authorization.tokens import RuntimeSubject, issue_run_refresh_token
from tests.e2e.conftest import execute_workflow_sync, write_and_register
from tests.helpers.runtime_tokens import legacy_engine_token
from tests.unit.authorization.committed_runs import World, committed_world

pytestmark = pytest.mark.e2e

CHALLENGE = 'Bearer error="invalid_token", error_description="refresh_required"'


@dataclass
class _Run:
    world: World
    execution: Any
    user: Any
    refresh_token: str


async def _live_run(world: World, *, platform_admin: bool, solution: Any = None, organization: Any = None) -> _Run:
    organization = organization or await world.organization()
    user = await world.person(organization)
    if platform_admin:
        await world.platform_admin(user)
    workflow = await world.workflow(organization, solution_id=None if solution is None else solution.id)
    execution = await world.execution(run_user=user, workflow=workflow, organization=organization)
    await world.session.commit()
    refresh = issue_run_refresh_token(RuntimeSubject("workflow_execution", execution.id))
    return _Run(world, execution, user, refresh)


async def _redeem(api_url: str, refresh_token: str) -> httpx.Response:
    async with httpx.AsyncClient(base_url=api_url, timeout=30.0) as http:
        return await http.post("/auth/refresh", json={"refresh_token": refresh_token})


async def _get(api_url: str, token: str, path: str, **params: str) -> httpx.Response:
    async with httpx.AsyncClient(base_url=api_url, timeout=30.0) as http:
        return await http.get(path, headers={"Authorization": f"Bearer {token}"}, params=params)


async def _bump(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.execute(text("SELECT authz_epoch_bump_once()"))


@pytest.fixture
async def sdk(e2e_api_url: str, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Any]:
    """Build an SDK client that holds a run's credentials the way a worker
    child does: process-scoped access and run refresh tokens."""
    clients: list[client_mod.BifrostClient] = []
    client_mod._reset_refresh_coordinators_for_tests()

    def build(access_token: str, refresh_token: str) -> client_mod.BifrostClient:
        monkeypatch.setenv("BIFROST_API_URL", e2e_api_url)
        monkeypatch.setenv("BIFROST_ACCESS_TOKEN", access_token)
        monkeypatch.setenv("BIFROST_REFRESH_TOKEN", refresh_token)
        client = client_mod.BifrostClient(e2e_api_url, access_token)
        clients.append(client)
        return client

    yield build
    for client in clients:
        await client.close()
    client_mod._reset_refresh_coordinators_for_tests()


async def test_a_role_revoked_mid_run_is_challenged_and_the_refresh_carries_the_change(
    async_engine: AsyncEngine, e2e_api_url: str, sdk: Any
) -> None:
    async with committed_world(async_engine) as world:
        run = await _live_run(world, platform_admin=True)
        access = (await _redeem(e2e_api_url, run.refresh_token)).json()["access_token"]
        client = sdk(access, run.refresh_token)
        before = await client.get("/api/sdk/context")

        # Revoke the run user's Platform Admin role: the epoch changes.
        for model in (UserRoleBoundary, UserRole):
            await world.session.execute(
                delete(model).where(model.user_id == run.user.id, model.role_id == PLATFORM_ADMIN_ROLE_ID)
            )
        await world.session.commit()

        stale = await _get(e2e_api_url, access, "/api/sdk/context")
        after = await client.get("/api/sdk/context")

    assert before.status_code == 200 and before.json()["user"]["is_superuser"] is True
    assert (stale.status_code, stale.headers["www-authenticate"]) == (401, CHALLENGE)
    assert after.status_code == 200, after.text
    assert after.json()["user"]["is_superuser"] is False
    assert client._access_token != access


async def test_ai_stream_after_an_epoch_change_refreshes_before_any_bytes(
    async_engine: AsyncEngine, e2e_api_url: str, sdk: Any
) -> None:
    async with committed_world(async_engine) as world:
        run = await _live_run(world, platform_admin=True)
        access = (await _redeem(e2e_api_url, run.refresh_token)).json()["access_token"]
        client = sdk(access, run.refresh_token)
        await _bump(async_engine)

        async with client.stream(
            "POST", "/api/sdk/ai/stream", json={"messages": [{"role": "user", "content": "Say hi."}]}
        ) as response:
            body = (await response.aread()).decode()

    assert response.status_code == 200, body
    assert body.startswith("data: ")
    assert client._access_token != access


async def test_an_ended_run_refuses_its_token_and_its_refresh_token(
    async_engine: AsyncEngine, e2e_api_url: str
) -> None:
    async with committed_world(async_engine) as world:
        run = await _live_run(world, platform_admin=False)
        access = (await _redeem(e2e_api_url, run.refresh_token)).json()["access_token"]
        live = await _get(e2e_api_url, access, "/api/sdk/context")
        run.execution.status = ExecutionStatus.SUCCESS
        await world.session.commit()

        ended = await _get(e2e_api_url, access, "/api/sdk/context")
        refreshed = await _redeem(e2e_api_url, run.refresh_token)

    assert live.status_code == 200, live.text
    # Not live: refused without the challenge, since a refresh would fail too.
    assert (ended.status_code, ended.headers["www-authenticate"]) == (401, "Bearer")
    assert refreshed.status_code == 401


def _expired_copy(token: str) -> str:
    """The same claims, signed with an ``exp`` in the past: what a run holds
    after a wait longer than its token's 60-90 minute lifetime."""
    settings = get_settings()
    claims = jwt.decode(token, options={"verify_signature": False})
    claims["exp"] = int(time.time()) - 30
    return jwt.encode(claims, settings.secret_key, algorithm=settings.algorithm)


async def test_after_a_long_wait_the_expired_token_is_challenged_and_the_sdk_continues(
    async_engine: AsyncEngine, e2e_api_url: str, sdk: Any
) -> None:
    async with committed_world(async_engine) as world:
        run = await _live_run(world, platform_admin=False)
        access = (await _redeem(e2e_api_url, run.refresh_token)).json()["access_token"]
        expired = _expired_copy(access)

        challenged = await _get(e2e_api_url, expired, "/api/sdk/context")
        client = sdk(expired, run.refresh_token)
        continued = await client.get("/api/sdk/context")

    assert (challenged.status_code, challenged.headers["www-authenticate"]) == (401, CHALLENGE)
    assert continued.status_code == 200, continued.text
    assert continued.json()["user"]["id"] == str(run.user.id)


async def test_a_deactivated_solution_is_refused_after_the_refresh(
    async_engine: AsyncEngine, e2e_api_url: str, sdk: Any
) -> None:
    async with committed_world(async_engine) as world:
        organization = await world.organization()
        solution = await world.solution(organization)
        run = await _live_run(world, platform_admin=True, solution=solution, organization=organization)
        access = (await _redeem(e2e_api_url, run.refresh_token)).json()["access_token"]
        client = sdk(access, run.refresh_token)
        active = await client.get("/api/tables", params={"solution": str(solution.id)})

        solution.status = "inactive"
        await world.session.commit()
        inactive = await client.get("/api/tables", params={"solution": str(solution.id)})

    assert active.status_code == 200, active.text
    assert inactive.status_code == 409, inactive.text
    assert client._access_token != access


async def test_a_legacy_token_is_bridged_only_while_its_run_is_live(
    async_engine: AsyncEngine, e2e_api_url: str
) -> None:
    async with committed_world(async_engine) as world:
        run = await _live_run(world, platform_admin=False)
        legacy, _ = legacy_engine_token(execution_id=str(run.execution.id), timeout_seconds=300)

        accepted = await _get(e2e_api_url, legacy, "/api/sdk/context")
        # The authority epoch does not apply: it is read from the database.
        await _bump(async_engine)
        still_accepted = await _get(e2e_api_url, legacy, "/api/sdk/context")
        run.execution.status = ExecutionStatus.SUCCESS
        await world.session.commit()
        refused = await _get(e2e_api_url, legacy, "/api/sdk/context")

    assert accepted.status_code == 200, accepted.text
    assert still_accepted.status_code == 200, still_accepted.text
    assert (refused.status_code, refused.headers["www-authenticate"]) == (401, "Bearer")


# --- A real worker child ------------------------------------------------------------


@pytest.fixture(scope="module")
def refreshing_workflow(e2e_client: Any, platform_admin: Any, org1: dict) -> Any:
    name = f"e2e_runtime_token_refresh_{uuid.uuid4().hex[:8]}"
    path = f"{name}.py"
    content = f'''"""E2E workflow: a run whose own call changes authority keeps working."""
import os

from bifrost import config, roles, workflow


@workflow(name="{name}", description="Runtime token refresh E2E")
async def {name}(target_user_id: str, role_name: str, key: str):
    before = os.environ["BIFROST_ACCESS_TOKEN"]
    role = await roles.create(name=role_name)
    # Assigning a role commits an authority change: the epoch moves on.
    await roles.assign_users(role.id, [target_user_id])
    # This call is challenged once, refreshed with the run refresh token and
    # replayed.
    value = await config.get(key)
    after = os.environ["BIFROST_ACCESS_TOKEN"]
    return {{"refreshed": before != after, "value": value, "role_id": role.id}}
'''
    registered = write_and_register(
        e2e_client, platform_admin.headers, path, content, name, organization_id=org1["id"]
    )
    yield registered
    e2e_client.delete(f"/api/files/editor?path={path}", headers=platform_admin.headers)


def test_a_worker_child_refreshes_after_its_own_authority_change(
    e2e_client: Any, platform_admin: Any, org1: dict, org1_user: Any, refreshing_workflow: Any
) -> None:
    key = f"e2e_runtime_token_{uuid.uuid4().hex[:8]}"
    seeded = e2e_client.post(
        "/api/sdk/config/set",
        headers=platform_admin.headers,
        json={"key": key, "value": "still-works", "is_secret": False, "scope": org1["id"]},
    )
    assert seeded.status_code == 204, seeded.text
    role_id = None
    try:
        result = execute_workflow_sync(
            e2e_client,
            platform_admin.headers,
            refreshing_workflow["id"],
            input_data={
                "target_user_id": str(org1_user.user_id),
                "role_name": f"E2E Token Refresh {uuid.uuid4().hex[:8]}",
                "key": key,
            },
            max_wait=120.0,
            org_id=org1["id"],
        )
        assert result["status"] == "Success", result
        out = result["result"]
        role_id = out["role_id"]
        assert out == {"refreshed": True, "value": "still-works", "role_id": role_id}
    finally:
        if role_id is not None:
            e2e_client.delete(f"/api/roles/{role_id}", headers=platform_admin.headers)
        e2e_client.post(
            "/api/sdk/config/delete", headers=platform_admin.headers, json={"key": key, "scope": org1["id"]}
        )
