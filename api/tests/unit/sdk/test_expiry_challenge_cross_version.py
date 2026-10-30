"""The PR1a server's expiry contract, which a PR1b client relies on after a rollback.

A PR1b child holds a ``ver: 1`` access token and a run refresh token. If the
API is rolled back to PR1a, the child must still recover when its access
token expires: PR1a answers the expired token with the refresh challenge and
redeems the run refresh token (plan §5.3, Astra review 4 #1).
``PR1A_EXPIRED_TOKEN_RESPONSE`` is that recorded response; the PR1b client
test replays it against the SDK's refreshing transport.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from src.routers.auth import router as auth_router
from src.services.authorization import tokens
from src.services.authorization.tokens import RuntimeSubject, issue_access_token, issue_run_refresh_token
from tests.unit.authorization.committed_runs import committed_world

PR1A_EXPIRED_TOKEN_RESPONSE = {
    "status_code": 401,
    "www-authenticate": 'Bearer error="invalid_token", error_description="refresh_required"',
    "json": {"detail": "Not authenticated"},
}


async def test_an_expired_run_token_is_challenged_and_its_run_refresh_token_redeemed(
    async_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = FastAPI()
    app.include_router(auth_router)
    async with committed_world(async_engine) as world:
        organization = await world.organization()
        user = await world.person(organization)
        execution = await world.execution(run_user=user, organization=organization)
        await world.session.commit()
        subject = RuntimeSubject("workflow_execution", execution.id)
        real_now = tokens._now
        # Minted three hours ago: expired whatever its 60-90 minute lifetime.
        monkeypatch.setattr(tokens, "_now", lambda: real_now() - 3 * 3600)
        expired = (await issue_access_token(subject)).token
        monkeypatch.undo()
        refresh_token = issue_run_refresh_token(subject)

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            challenged = await client.get("/auth/me", headers={"Authorization": f"Bearer {expired}"})
            refreshed = await client.post("/auth/refresh", json={"refresh_token": refresh_token})
            replayed = await client.get(
                "/auth/me", headers={"Authorization": f"Bearer {refreshed.json()['access_token']}"}
            )

    assert {
        "status_code": challenged.status_code,
        "www-authenticate": challenged.headers["www-authenticate"],
        "json": challenged.json(),
    } == PR1A_EXPIRED_TOKEN_RESPONSE
    assert refreshed.status_code == 200
    assert refreshed.json()["refresh_token"] == refresh_token
    assert replayed.status_code == 200
    assert replayed.json()["id"] == str(user.id)


async def test_a_run_refresh_token_is_never_an_access_token(async_engine: AsyncEngine) -> None:
    app = FastAPI()
    app.include_router(auth_router)
    async with committed_world(async_engine) as world:
        organization = await world.organization()
        user = await world.person(organization)
        execution = await world.execution(run_user=user, organization=organization)
        await world.session.commit()
        refresh_token = issue_run_refresh_token(RuntimeSubject("workflow_execution", execution.id))

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/auth/me", headers={"Authorization": f"Bearer {refresh_token}"})

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
