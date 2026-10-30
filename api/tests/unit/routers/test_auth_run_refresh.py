"""POST /auth/refresh redeems run refresh tokens (plan §5.1, D7, D8)."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from src.core.security import decode_token
from src.models.enums import ExecutionStatus
from src.routers.auth import router as auth_router
from src.services.authorization.tokens import RuntimeSubject, issue_run_refresh_token, parse_runtime_claims
from tests.unit.authorization.committed_runs import committed_world


@pytest.fixture
def app() -> FastAPI:
    application = FastAPI()
    application.include_router(auth_router)
    return application


async def _refresh(app: FastAPI, refresh_token: str) -> httpx.Response:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/auth/refresh", json={"refresh_token": refresh_token})


async def test_a_live_run_gets_a_new_access_token_and_keeps_its_refresh_token(
    app: FastAPI, async_engine: AsyncEngine
) -> None:
    async with committed_world(async_engine) as world:
        organization = await world.organization()
        user = await world.person(organization)
        execution = await world.execution(run_user=user, organization=organization)
        await world.session.commit()
        refresh_token = issue_run_refresh_token(RuntimeSubject("workflow_execution", execution.id))

        response = await _refresh(app, refresh_token)

    assert response.status_code == 200
    body = response.json()
    assert body["refresh_token"] == refresh_token
    assert "set-cookie" not in response.headers
    payload = decode_token(body["access_token"], expected_type="access")
    assert payload is not None
    claims = parse_runtime_claims(payload)
    assert claims is not None
    assert (claims.sub, claims.usr.id) == (execution.id, user.id)


async def test_an_ended_run_is_refused(app: FastAPI, async_engine: AsyncEngine) -> None:
    async with committed_world(async_engine) as world:
        organization = await world.organization()
        user = await world.person(organization)
        execution = await world.execution(run_user=user, organization=organization, status=ExecutionStatus.SUCCESS)
        await world.session.commit()

        response = await _refresh(app, issue_run_refresh_token(RuntimeSubject("workflow_execution", execution.id)))

    assert response.status_code == 401
    assert response.json()["detail"] == "Run is no longer live"


async def test_a_service_attempt_with_an_expired_lease_is_refused(app: FastAPI, async_engine: AsyncEngine) -> None:
    async with committed_world(async_engine) as world:
        organization = await world.organization()
        user = await world.person(organization)
        # One service definition per workflow.
        live = await world.service_attempt(
            run_user=user, workflow=await world.workflow(organization), organization=organization
        )
        lapsed = await world.service_attempt(
            run_user=user, workflow=await world.workflow(organization), organization=organization,
            lease=timedelta(seconds=-1),
        )
        await world.session.commit()

        live_response = await _refresh(
            app, issue_run_refresh_token(RuntimeSubject("service_attempt", live.id, live.service_id))
        )
        lapsed_response = await _refresh(
            app, issue_run_refresh_token(RuntimeSubject("service_attempt", lapsed.id, lapsed.service_id))
        )

    assert live_response.status_code == 200
    assert lapsed_response.status_code == 401


async def test_fifty_concurrent_run_refreshes_from_one_address_all_succeed(
    app: FastAPI, async_engine: AsyncEngine
) -> None:
    async with committed_world(async_engine) as world:
        organization = await world.organization()
        user = await world.person(organization)
        execution = await world.execution(run_user=user, organization=organization)
        await world.session.commit()
        refresh_token = issue_run_refresh_token(RuntimeSubject("workflow_execution", execution.id))

        responses = await asyncio.gather(*(_refresh(app, refresh_token) for _ in range(50)))

    assert [response.status_code for response in responses] == [200] * 50
