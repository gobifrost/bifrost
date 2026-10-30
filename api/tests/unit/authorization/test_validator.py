"""The runtime-token validator (plan §3.5, §6, §8.3).

Claims minted by ``issue_access_token`` (``ver: 1``) are authority while the
token is unexpired, the authority epoch still equals its ``av`` and its run
is live; one statement on the request's own session decides. Refusals that a
refresh fixes carry the ``refresh_required`` challenge, raised by the auth
dependency before any handler runs. Legacy runtime tokens (no ``ver``) are
bridged: validated from the database exactly as before.
"""

# No ``from __future__ import annotations``: FastAPI resolves these routes'
# Annotated dependencies at definition time.
import base64
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import jwt
import pytest
from fastapi import FastAPI
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from src.config import get_settings
from src.core.auth import REFRESH_REQUIRED_CHALLENGE, Context, CurrentUser
from src.core.principal import UserPrincipal
from src.models.enums import ExecutionStatus
from src.routers.auth import router as auth_router
from src.services.authorization.tokens import (
    RuntimeSubject,
    authenticate_runtime_token,
    issue_access_token,
    issue_run_refresh_token,
)
from tests.unit.authorization.committed_runs import committed_world

_BUMP = text("SELECT authz_epoch_bump_once()")


def _app(seen: list[UserPrincipal]) -> FastAPI:
    app = FastAPI()
    app.include_router(auth_router)

    @app.post("/probe")
    async def probe(user: CurrentUser) -> dict[str, str]:
        seen.append(user)
        return {"user": str(user.user_id)}

    @app.get("/context")
    async def context(ctx: Context) -> dict[str, Any]:
        return {"solution": ctx.solution_id, "roles": ctx.user.role_names}

    return app


async def _send(app: FastAPI, method: str, path: str, token: str, **kwargs: Any) -> httpx.Response:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.request(method, path, headers={"Authorization": f"Bearer {token}"}, **kwargs)


async def _refresh(app: FastAPI, refresh_token: str) -> httpx.Response:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/auth/refresh", json={"refresh_token": refresh_token})


async def _bump(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.execute(_BUMP)


def _sign(claims: dict[str, Any], *, key: str | None = None) -> str:
    settings = get_settings()
    payload = {
        "type": "access",
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "exp": int((datetime.now(timezone.utc) + timedelta(minutes=10)).timestamp()),
        **claims,
    }
    return jwt.encode(payload, key or settings.secret_key, algorithm=settings.algorithm)


async def test_claims_are_authority_after_one_validator_statement(async_engine: AsyncEngine) -> None:
    async with committed_world(async_engine) as world:
        organization = await world.organization()
        user = await world.person(organization)
        execution = await world.execution(run_user=user, organization=organization)
        await world.session.commit()
        claims = (await issue_access_token(RuntimeSubject("workflow_execution", execution.id))).claims

        statements: list[str] = []

        def record(conn, cursor, statement, parameters, context, executemany) -> None:
            statements.append(statement)

        async with AsyncSession(async_engine) as session:
            engine = session.get_bind()
            event.listen(engine, "before_cursor_execute", record)
            try:
                result = await authenticate_runtime_token(session, claims)
            finally:
                event.remove(engine, "before_cursor_execute", record)

    assert len(statements) == 1
    assert result.refusal is None
    assert result.principal is not None
    assert result.principal.user_id == user.id
    assert result.principal.runtime_authority is not None
    assert result.principal.runtime_authority.credential_id == execution.id


async def test_an_epoch_change_is_challenged_before_the_handler_and_a_refresh_recovers(
    async_engine: AsyncEngine,
) -> None:
    seen: list[UserPrincipal] = []
    app = _app(seen)
    async with committed_world(async_engine) as world:
        organization = await world.organization()
        user = await world.person(organization)
        execution = await world.execution(run_user=user, organization=organization)
        await world.session.commit()
        subject = RuntimeSubject("workflow_execution", execution.id)
        access = (await issue_access_token(subject)).token
        refresh = issue_run_refresh_token(subject)

        accepted = await _send(app, "POST", "/probe", access)
        await _bump(async_engine)
        challenged = await _send(app, "POST", "/probe", access)
        refreshed = await _refresh(app, refresh)
        replayed = await _send(app, "POST", "/probe", refreshed.json()["access_token"])

    assert accepted.status_code == 200
    assert challenged.status_code == 401
    assert challenged.headers["www-authenticate"] == REFRESH_REQUIRED_CHALLENGE
    assert refreshed.status_code == 200
    assert replayed.status_code == 200
    # The challenged attempt never reached the handler.
    assert len(seen) == 2


async def test_an_ended_run_is_refused_without_a_challenge(async_engine: AsyncEngine) -> None:
    app = _app([])
    async with committed_world(async_engine) as world:
        organization = await world.organization()
        user = await world.person(organization)
        execution = await world.execution(run_user=user, organization=organization)
        await world.session.commit()
        subject = RuntimeSubject("workflow_execution", execution.id)
        access = (await issue_access_token(subject)).token
        execution.status = ExecutionStatus.SUCCESS
        await world.session.commit()

        refused = await _send(app, "POST", "/probe", access)
        refreshed = await _refresh(app, issue_run_refresh_token(subject))

    assert refused.status_code == 401
    assert refused.headers["www-authenticate"] == "Bearer"
    assert refreshed.status_code == 401


async def test_an_expired_service_lease_is_refused_without_a_challenge(async_engine: AsyncEngine) -> None:
    app = _app([])
    async with committed_world(async_engine) as world:
        organization = await world.organization()
        user = await world.person(organization)
        workflow = await world.workflow(organization)
        attempt = await world.service_attempt(run_user=user, workflow=workflow, organization=organization)
        await world.session.commit()
        access = (
            await issue_access_token(RuntimeSubject("service_attempt", attempt.id, attempt.service_id))
        ).token
        accepted = await _send(app, "POST", "/probe", access)
        attempt.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await world.session.commit()

        refused = await _send(app, "POST", "/probe", access)

    assert accepted.status_code == 200
    assert refused.status_code == 401
    assert refused.headers["www-authenticate"] == "Bearer"


async def test_a_missing_epoch_row_refuses_and_logs(
    async_engine: AsyncEngine, caplog: pytest.LogCaptureFixture
) -> None:
    async with committed_world(async_engine) as world:
        organization = await world.organization()
        user = await world.person(organization)
        execution = await world.execution(run_user=user, organization=organization)
        await world.session.commit()
        claims = (await issue_access_token(RuntimeSubject("workflow_execution", execution.id))).claims

        async with async_engine.connect() as connection:
            transaction = await connection.begin()
            try:
                await connection.execute(text("ALTER TABLE authz_versions DISABLE TRIGGER authz_versions_no_delete"))
                await connection.execute(text("DELETE FROM authz_versions"))
                with caplog.at_level(logging.ERROR):
                    result = await authenticate_runtime_token(AsyncSession(bind=connection), claims)
            finally:
                await transaction.rollback()

    assert result.principal is None
    assert result.refusal == "authority_state_missing"
    assert not result.refresh_required
    assert any("authz.epoch_missing" in record.getMessage() for record in caplog.records)


def _tamper(token: str, change: dict[str, Any]) -> str:
    """Rewrite the payload segment without re-signing."""
    header, payload, signature = token.split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    claims.update(change)
    rewritten = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"{header}.{rewritten}.{signature}"


async def test_a_tampered_or_foreign_token_is_refused(async_engine: AsyncEngine) -> None:
    app = _app([])
    async with committed_world(async_engine) as world:
        organization = await world.organization()
        user = await world.person(organization)
        execution = await world.execution(run_user=user, organization=organization)
        await world.session.commit()
        issued = await issue_access_token(RuntimeSubject("workflow_execution", execution.id))
        foreign = _sign(issued.claims.to_payload(), key="not-the-secret-key-not-the-secret-key")

        tampered = await _send(app, "POST", "/probe", _tamper(issued.token, {"run": {"org": str(uuid4())}}))
        forged = await _send(app, "POST", "/probe", foreign)

    assert (tampered.status_code, tampered.headers["www-authenticate"]) == (401, "Bearer")
    assert (forged.status_code, forged.headers["www-authenticate"]) == (401, "Bearer")


@pytest.mark.parametrize("version", [1, 2], ids=["schema-invalid ver 1", "unknown ver"])
async def test_a_versioned_payload_never_falls_back_to_the_legacy_bridge(
    async_engine: AsyncEngine, version: int
) -> None:
    # Signed and naming a live run, which the legacy path would accept.
    app = _app([])
    async with committed_world(async_engine) as world:
        organization = await world.organization()
        user = await world.person(organization)
        execution = await world.execution(run_user=user, organization=organization)
        await world.session.commit()
        token = _sign({"sub": str(execution.id), "credential_kind": "workflow_execution", "ver": version})

        response = await _send(app, "POST", "/probe", token)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


async def test_a_legacy_token_is_bridged_only_while_its_run_is_live(
    async_engine: AsyncEngine, caplog: pytest.LogCaptureFixture
) -> None:
    seen: list[UserPrincipal] = []
    app = _app(seen)
    async with committed_world(async_engine) as world:
        organization = await world.organization()
        user = await world.person(organization)
        execution = await world.execution(run_user=user, organization=organization)
        await world.session.commit()
        legacy = _sign({"sub": str(execution.id), "credential_kind": "workflow_execution"})

        with caplog.at_level(logging.INFO, logger="src.services.authorization.runtime_auth"):
            accepted = await _send(app, "POST", "/probe", legacy)
        # An epoch change does not matter to a legacy token: it is read from
        # the database on every request.
        await _bump(async_engine)
        still_accepted = await _send(app, "POST", "/probe", legacy)
        execution.status = ExecutionStatus.SUCCESS
        await world.session.commit()
        refused = await _send(app, "POST", "/probe", legacy)
        # Its refresh slot holds the same token, which /auth/refresh refuses.
        no_refresh = await _refresh(app, legacy)

    assert accepted.status_code == 200
    assert still_accepted.status_code == 200
    assert [principal.runtime_authority.credential_id for principal in seen if principal.runtime_authority] == [
        execution.id,
        execution.id,
    ]
    assert any("authz.legacy_runtime_token_accepted" in record.getMessage() for record in caplog.records)
    assert (refused.status_code, refused.headers["www-authenticate"]) == (401, "Bearer")
    assert no_refresh.status_code == 401


async def test_the_runs_own_solution_gate_follows_the_claims_and_the_role_read_is_skipped(
    async_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app([])
    # A runtime principal already carries its actor's roles.
    monkeypatch.setattr("src.core.auth.get_user_roles", AsyncMock(side_effect=AssertionError("role read")))
    async with committed_world(async_engine) as world:
        organization = await world.organization()
        user = await world.person(organization)
        solution = await world.solution(organization)
        workflow = await world.workflow(organization, solution_id=solution.id)
        execution = await world.execution(run_user=user, workflow=workflow, organization=organization)
        await world.session.commit()
        subject = RuntimeSubject("workflow_execution", execution.id)
        access = (await issue_access_token(subject)).token

        active = await _send(app, "GET", "/context", access, params={"solution": str(solution.id)})
        solution.status = "inactive"
        await world.session.commit()
        challenged = await _send(app, "GET", "/context", access, params={"solution": str(solution.id)})
        refreshed = (await _refresh(app, issue_run_refresh_token(subject))).json()["access_token"]
        inactive = await _send(app, "GET", "/context", refreshed, params={"solution": str(solution.id)})

    assert active.status_code == 200
    assert active.json()["solution"] == str(solution.id)
    assert challenged.status_code == 401
    assert challenged.headers["www-authenticate"] == REFRESH_REQUIRED_CHALLENGE
    assert inactive.status_code == 409


async def test_mcp_tool_filtering_takes_the_run_users_roles_from_the_claims(
    async_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.services.mcp_server.auth import BifrostAuthProvider

    provider = BifrostAuthProvider(base_url="https://test.example.com")
    monkeypatch.setattr(provider, "_check_mcp_access", AsyncMock(return_value=True))
    monkeypatch.setattr(provider, "_get_user_roles", AsyncMock(side_effect=AssertionError("role read")))
    async with committed_world(async_engine) as world:
        organization = await world.organization()
        user = await world.person(organization)
        role = await world.role("workflows.read")
        await world.assign(user, role.id, ("organization", organization.id))
        run = await world.agent_run(run_user=user, actor=user, organization=organization)
        await world.session.commit()
        access = (await issue_access_token(RuntimeSubject("agent_run", run.id))).token

        verified = await provider.verify_token(access)
        await _bump(async_engine)
        stale = await provider.verify_token(access)

    assert verified is not None
    assert verified.claims["roles"] == [role.name]
    assert verified.claims["runtime_credential_id"] == str(run.id)
    assert stale is None
