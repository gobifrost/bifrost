"""The MCP bridge's runtime branch: claims tokens re-minted on the challenge (plan §5.3, D6).

A runtime MCP context mints a claims access token in-process. When the API
answers a bridge request with the ``refresh_required`` challenge, the
bridge's transport re-mints and replays it, at most three times per request;
the challenged attempt never reaches a handler.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import Depends, FastAPI, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from src.core.auth import REFRESH_REQUIRED_CHALLENGE, get_current_user
from src.core.principal import UserPrincipal
from src.services.authorization import tokens
from src.services.mcp_server.server import MCPContext
from src.services.mcp_server.tools import _http_bridge
from src.services.mcp_server.tools._http_bridge import BRIDGE_REFRESH_BUDGET, rest_client
from tests.unit.authorization.committed_runs import committed_world

_BUMP = text("SELECT authz_epoch_bump_once()")


async def test_live_external_mcp_credential_is_forwarded_without_a_new_session(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["Authorization"])
        return httpx.Response(200, json={})

    issue = AsyncMock()
    monkeypatch.setattr(tokens, "issue_access_token", issue)
    monkeypatch.setattr(_http_bridge, "_live_access_token", lambda: "original.jwt")
    monkeypatch.setattr(_http_bridge, "_build_in_process_transport", lambda: httpx.MockTransport(respond))
    async with rest_client(MCPContext(user_id=uuid4(), org_id=uuid4())) as client:
        assert (await client.post("/mutation")).status_code == 200
    assert seen == ["Bearer original.jwt"]
    issue.assert_not_awaited()


async def test_internal_human_bridge_uses_database_facts_and_shared_refresh(
    async_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _Api(async_engine)
    monkeypatch.setattr(_http_bridge, "_live_access_token", lambda: None)
    monkeypatch.setattr(_http_bridge, "_build_in_process_transport", lambda: httpx.ASGITransport(app=api.app))
    async with committed_world(async_engine) as world:
        org = await world.organization()
        human = await world.person(org)
        await world.session.commit()
        # Copied context privilege flags must not enter the access token.
        context = MCPContext(user_id=human.id, org_id=uuid4(), is_platform_admin=True)
        async with rest_client(context) as client:
            await client.post("/mutate")
            response = await client.get("/probe")
    assert response.status_code == 200 and response.json() == {"user": str(human.id)}
    assert api.handled == ["mutate", "probe"]


async def _bump(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.execute(_BUMP)


class _Api:
    """A stand-in API: ``/probe`` reads, ``/mutate`` changes authority, and
    ``bump_before_auth`` changes it before every authentication."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.handled: list[str] = []
        self.bump_before_auth = False
        app = FastAPI()

        @app.middleware("http")
        async def bump_first(request: Request, call_next: Callable[[Request], Awaitable[Any]]) -> Any:
            if self.bump_before_auth:
                await _bump(engine)
            return await call_next(request)

        async def probe(user: UserPrincipal = Depends(get_current_user)) -> dict[str, str]:
            self.handled.append("probe")
            return {"user": str(user.user_id)}

        async def mutate(user: UserPrincipal = Depends(get_current_user)) -> dict[str, str]:
            self.handled.append("mutate")
            await _bump(engine)
            return {}

        app.add_api_route("/probe", probe, methods=["GET"])
        app.add_api_route("/mutate", mutate, methods=["POST"])
        self.app = app


@pytest.fixture
def mints(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    minted: list[str] = []
    real = tokens.issue_access_token

    async def counting(subject: tokens.RuntimeSubject) -> tokens.IssuedAccessToken:
        issued = await real(subject)
        minted.append(issued.token)
        return issued

    monkeypatch.setattr(tokens, "issue_access_token", counting)
    return minted


async def _agent_context(world: Any) -> MCPContext:
    organization = await world.organization()
    user = await world.person(organization)
    run = await world.agent_run(run_user=user, actor=user, organization=organization)
    await world.session.commit()
    return MCPContext(
        user_id=user.id, org_id=organization.id, runtime_credential_kind="agent_run", runtime_credential_id=run.id
    )


async def test_an_authority_change_after_the_mint_is_challenged_and_re_minted(
    async_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch, mints: list[str]
) -> None:
    api = _Api(async_engine)
    monkeypatch.setattr(_http_bridge, "_build_in_process_transport", lambda: httpx.ASGITransport(app=api.app))
    async with committed_world(async_engine) as world:
        context = await _agent_context(world)
        async with rest_client(context) as client:
            await _bump(async_engine)  # a concurrent authority commit
            response = await client.get("/probe")

    assert response.status_code == 200
    assert len(mints) == 2
    assert api.handled == ["probe"]


async def test_a_bridge_context_whose_request_changes_authority_keeps_working(
    async_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch, mints: list[str]
) -> None:
    api = _Api(async_engine)
    monkeypatch.setattr(_http_bridge, "_build_in_process_transport", lambda: httpx.ASGITransport(app=api.app))
    async with committed_world(async_engine) as world:
        context = await _agent_context(world)
        async with rest_client(context) as client:
            changed = await client.post("/mutate")
            after = await client.get("/probe")
            again = await client.get("/probe")

    assert (changed.status_code, after.status_code, again.status_code) == (200, 200, 200)
    # One re-mint after the change; the next request reuses the new token.
    assert len(mints) == 2
    assert api.handled == ["mutate", "probe", "probe"]


async def test_an_exhausted_budget_surfaces_the_challenge(
    async_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch, mints: list[str]
) -> None:
    api = _Api(async_engine)
    api.bump_before_auth = True
    monkeypatch.setattr(_http_bridge, "_build_in_process_transport", lambda: httpx.ASGITransport(app=api.app))
    async with committed_world(async_engine) as world:
        context = await _agent_context(world)
        async with rest_client(context) as client:
            response = await client.get("/probe")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == REFRESH_REQUIRED_CHALLENGE
    assert len(mints) == 1 + BRIDGE_REFRESH_BUDGET
    assert api.handled == []
