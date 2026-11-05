"""The single minter: its own read-only snapshot, lifetimes, refusals and run refresh tokens."""

from __future__ import annotations

import logging
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from src.core.security import decode_token
from src.models.enums import ExecutionStatus
from src.models.orm.users import UserRole, UserRoleBoundary
from src.services.authorization import tokens
from src.services.authorization.authz import AuthorityStateMissing, read_epoch
from src.services.authorization.tokens import (
    RuntimeSubject,
    TokenSubjectUnavailable,
    issue_access_token,
    issue_run_refresh_token,
    parse_run_refresh_claims,
    parse_runtime_claims,
)
from tests.unit.authorization.committed_runs import committed_world


async def test_a_token_carries_the_committed_facts_and_epoch(async_engine: AsyncEngine) -> None:
    async with committed_world(async_engine) as world:
        organization = await world.organization()
        user = await world.person(organization)
        role = await world.role("workflows.read")
        await world.assign(user, role.id, ("organization", organization.id))
        workflow = await world.workflow(organization)
        execution = await world.execution(run_user=user, workflow=workflow, organization=organization)
        await world.session.commit()

        issued = await issue_access_token(RuntimeSubject("workflow_execution", execution.id))

        payload = decode_token(issued.token, expected_type="access")
        assert payload is not None
        assert parse_runtime_claims(payload) == issued.claims
        assert issued.claims.sub == execution.id
        assert issued.claims.usr.id == user.id
        assert [(r.id, r.perm, r.bnd) for r in issued.claims.usr.roles] == [
            (role.id, ["workflows.read"], [("organization", organization.id)])
        ]
        async with AsyncSession(async_engine) as session:
            assert int(issued.claims.av) == await read_epoch(session)


async def test_the_minter_never_sees_or_disturbs_the_callers_transaction(
    async_engine: AsyncEngine, db_session: AsyncSession
) -> None:
    async with committed_world(async_engine) as world:
        organization = await world.organization()
        user = await world.person(organization)
        role = await world.role("secrets.read")
        execution = await world.execution(run_user=user, organization=organization)
        await world.session.commit()

        try:
            # The caller holds an uncommitted role assignment.
            db_session.add(UserRole(user_id=user.id, role_id=role.id, assigned_by="caller"))
            await db_session.flush()
            db_session.add(
                UserRoleBoundary(user_id=user.id, role_id=role.id, kind="organization", organization_id=organization.id)
            )
            await db_session.flush()

            issued = await issue_access_token(RuntimeSubject("workflow_execution", execution.id))

            assert issued.claims.usr.roles == []
            assert await db_session.get(UserRole, (user.id, role.id)) is not None
        finally:
            # Release the caller's locks before the world's cleanup deletes.
            await db_session.rollback()


async def test_lifetimes_spread_across_sixty_to_ninety_minutes(
    async_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with committed_world(async_engine) as world:
        organization = await world.organization()
        user = await world.person(organization)
        execution = await world.execution(run_user=user, organization=organization)
        await world.session.commit()
        subject = RuntimeSubject("workflow_execution", execution.id)
        bounds: list[int] = []

        for pick in (lambda n: 0, lambda n: n - 1):
            asked: list[int] = []
            monkeypatch.setattr(tokens.secrets, "randbelow", lambda n, pick=pick: (asked.append(n), pick(n))[1])
            claims = (await issue_access_token(subject)).claims
            assert asked == [1801]
            bounds.append(claims.exp - claims.iat)
        monkeypatch.undo()
        spread = {(c.exp - c.iat) for c in [(await issue_access_token(subject)).claims for _ in range(8)]}

    assert bounds == [3600, 5400]
    assert all(3600 <= lifetime <= 5400 for lifetime in spread)
    assert len(spread) > 1


@pytest.mark.parametrize("status", [ExecutionStatus.SUCCESS, ExecutionStatus.FAILED, ExecutionStatus.CANCELLED])
async def test_a_run_that_is_not_live_gets_no_token(async_engine: AsyncEngine, status: ExecutionStatus) -> None:
    async with committed_world(async_engine) as world:
        organization = await world.organization()
        user = await world.person(organization)
        execution = await world.execution(run_user=user, organization=organization, status=status)
        await world.session.commit()

        with pytest.raises(TokenSubjectUnavailable):
            await issue_access_token(RuntimeSubject("workflow_execution", execution.id))


async def test_a_missing_run_or_mismatched_service_gets_no_token(async_engine: AsyncEngine) -> None:
    async with committed_world(async_engine) as world:
        organization = await world.organization()
        user = await world.person(organization)
        workflow = await world.workflow(organization)
        attempt = await world.service_attempt(run_user=user, workflow=workflow, organization=organization)
        await world.session.commit()

        with pytest.raises(TokenSubjectUnavailable):
            await issue_access_token(RuntimeSubject("workflow_execution", uuid4()))
        with pytest.raises(TokenSubjectUnavailable):
            await issue_access_token(RuntimeSubject("service_attempt", attempt.id, uuid4()))
        issued = await issue_access_token(RuntimeSubject("service_attempt", attempt.id, attempt.service_id))
        assert issued.claims.service_id == attempt.service_id


async def test_a_missing_epoch_row_refuses_to_mint_and_logs(
    async_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def missing(db: AsyncSession) -> int:
        raise AuthorityStateMissing("missing")

    monkeypatch.setattr(tokens, "read_epoch", missing)
    async with committed_world(async_engine) as world:
        organization = await world.organization()
        user = await world.person(organization)
        execution = await world.execution(run_user=user, organization=organization)
        await world.session.commit()

        with caplog.at_level(logging.ERROR), pytest.raises(AuthorityStateMissing):
            await issue_access_token(RuntimeSubject("workflow_execution", execution.id))

    assert "authz.epoch_missing" in caplog.text


def test_a_run_refresh_token_carries_identity_only() -> None:
    attempt_id, service_id = uuid4(), uuid4()

    token = issue_run_refresh_token(RuntimeSubject("service_attempt", attempt_id, service_id))

    payload = decode_token(token, expected_type="run_refresh")
    assert payload is not None
    assert set(payload) == {"iss", "aud", "type", "ver", "iat", "jti", "sub", "credential_kind", "service_id"}
    claims = parse_run_refresh_claims(payload)
    assert claims is not None
    assert (claims.sub, claims.service_id, claims.credential_kind) == (attempt_id, service_id, "service_attempt")
    # It is never an access token.
    assert decode_token(token, expected_type="access") is None


def test_agent_runs_get_no_run_refresh_token() -> None:
    with pytest.raises(ValueError):
        issue_run_refresh_token(RuntimeSubject("agent_run", uuid4()))
