"""Claims parity: the principal from a minted token equals today's database resolution.

``load_runtime_principal`` is the parity oracle (D5). Each scenario mirrors a
case in ``test_runtime.py``, committed so the minter can read it. The role
cache is cleared first, because the oracle reads the actor's roles through
it; a poisoned cache must never reach the claims.

The same oracle holds on the validator path: a signed token sent through the
real authentication dependency yields the principal the database resolves.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi import Depends, FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from shared.builtin_roles import PLATFORM_ADMIN_ROLE_ID
from shared.role_cache import _CACHE_SCHEMA_VERSION, _key, invalidate_user
from src.core.auth import get_current_user
from src.core.cache import get_shared_redis
from src.core.principal import UserPrincipal
from src.services.authorization.runtime_auth import load_runtime_principal
from src.services.authorization.tokens import RuntimeSubject, issue_access_token, principal_from_claims
from tests.unit.authorization.committed_runs import World, committed_world


def _normalized(principal: UserPrincipal) -> dict[str, Any]:
    """Every fact the principal carries, with database row order removed."""
    authority = principal.runtime_authority
    assert authority is not None
    ctx = authority.run_user.ctx
    powers = authority.powers
    fields = {
        name: value
        for name, value in vars(principal).items()
        if name not in {"role_ids", "role_names", "roles", "runtime_authority"}
    }
    authority_fields = {
        name: value for name, value in vars(authority).items() if name not in {"run_user", "powers"}
    }
    return {
        "principal": fields,
        "roles": sorted(zip(map(str, principal.role_ids), principal.role_names)),
        "authority": authority_fields,
        "run_user": (authority.run_user.user_id, authority.run_user.identity_kind, authority.run_user.is_active),
        "ctx": (ctx.user_id, ctx.home_organization_id, ctx.base_role_id, ctx.is_external, ctx.base_permissions),
        "grants": sorted(
            (str(grant.role_id), sorted(grant.permissions), sorted((b.kind, str(b.organization_id)) for b in grant.boundaries))
            for grant in ctx.role_grants
        ),
        "is_platform_admin": authority.run_user.is_platform_admin,
        "powers": None if powers is None else (powers.mode, sorted(g.permission for g in powers.grants)),
    }


def _payload(subject: RuntimeSubject) -> dict[str, Any]:
    payload: dict[str, Any] = {"credential_kind": subject.kind, "sub": str(subject.credential_id)}
    if subject.service_id is not None:
        payload["service_id"] = str(subject.service_id)
    return payload


async def _oracle(engine: AsyncEngine, subject: RuntimeSubject) -> UserPrincipal:
    async with AsyncSession(engine) as session:
        principal = await load_runtime_principal(session, _payload(subject))
    assert principal is not None
    return principal


async def _separate_actor_restricted_powers(world: World) -> RuntimeSubject:
    contoso = await world.organization()
    fabrikam = await world.organization(provider=True)
    run_user = await world.person(contoso)
    actor = await world.person(fabrikam)
    role = await world.role("tables.read", "workflows.read")
    await world.assign(run_user, role.id, ("organization", contoso.id), ("managed_organizations", None))
    await world.assign(actor, role.id, ("organization", fabrikam.id))
    workflow = await world.workflow(contoso, permission_mode="restricted", grants=("secrets.read",))
    execution = await world.execution(run_user=run_user, actor=actor, workflow=workflow, organization=contoso)
    return RuntimeSubject("workflow_execution", execution.id)


async def _impersonated_inline(world: World) -> RuntimeSubject:
    organization = await world.organization()
    user = await world.person(organization, external=True)
    execution = await world.execution(
        run_user=user, organization=organization, impersonated_by_user_id=uuid4(), impersonation_session_id=uuid4()
    )
    return RuntimeSubject("workflow_execution", execution.id)


async def _platform_admin_full_powers(world: World) -> RuntimeSubject:
    organization = await world.organization()
    user = await world.person(organization)
    await world.platform_admin(user)
    unbounded = await world.role("secrets.read")
    await world.assign(user, unbounded.id)
    workflow = await world.workflow(organization, permission_mode="full")
    execution = await world.execution(run_user=user, workflow=workflow, organization=organization)
    return RuntimeSubject("workflow_execution", execution.id)


async def _solution_workflow(world: World) -> RuntimeSubject:
    organization = await world.organization()
    user = await world.person(organization)
    solution = await world.solution(organization, status="inactive")
    workflow = await world.workflow(organization, solution_id=solution.id)
    execution = await world.execution(run_user=user, workflow=workflow, organization=organization)
    return RuntimeSubject("workflow_execution", execution.id)


async def _service_attempt(world: World) -> RuntimeSubject:
    organization = await world.organization()
    user = await world.person(organization)
    role = await world.role("workflows.read")
    await world.assign(user, role.id, ("platform", None))
    workflow = await world.workflow(organization, permission_mode="restricted", grants=("tables.read",))
    attempt = await world.service_attempt(run_user=user, workflow=workflow, organization=organization)
    return RuntimeSubject("service_attempt", attempt.id, attempt.service_id)


async def _agent_run_as_other_user(world: World) -> RuntimeSubject:
    organization = await world.organization()
    run_user = await world.person(organization)
    actor = await world.person(organization)
    await world.platform_admin(actor)
    solution = await world.solution(organization)
    agent = await world.agent(organization, solution=solution)
    run = await world.agent_run(
        run_user=run_user, actor=actor, organization=organization, agent=agent,
        impersonated_by_user_id=uuid4(), impersonation_session_id=uuid4(),
    )
    return RuntimeSubject("agent_run", run.id)


async def _unattended_agent_run(world: World) -> RuntimeSubject:
    organization = await world.organization()
    user = await world.person(organization)
    run = await world.agent_run(run_user=user, actor=user, organization=organization)
    return RuntimeSubject("agent_run", run.id)


_SCENARIOS: dict[str, Callable[[World], Awaitable[RuntimeSubject]]] = {
    "workflow, separate actor, restricted powers": _separate_actor_restricted_powers,
    "inline execution, external user, impersonation": _impersonated_inline,
    "platform admin, unbounded role, full powers": _platform_admin_full_powers,
    "inactive Solution workflow": _solution_workflow,
    "service attempt": _service_attempt,
    "agent run as another user": _agent_run_as_other_user,
    "unattended agent run": _unattended_agent_run,
}


@pytest.mark.parametrize("build", list(_SCENARIOS.values()), ids=list(_SCENARIOS))
async def test_the_principal_from_claims_equals_the_database_resolution(
    async_engine: AsyncEngine, build: Callable[[World], Awaitable[RuntimeSubject]]
) -> None:
    async with committed_world(async_engine) as world:
        subject = await build(world)
        await world.session.commit()
        claims = (await issue_access_token(subject)).claims
        for user_id in {claims.usr.id, *(() if claims.act is None else (claims.act.id,))}:
            await invalidate_user(user_id)

        assert _normalized(principal_from_claims(claims)) == _normalized(await _oracle(async_engine, subject))


@pytest.mark.parametrize("build", list(_SCENARIOS.values()), ids=list(_SCENARIOS))
async def test_the_validated_principal_equals_the_database_resolution(
    async_engine: AsyncEngine, build: Callable[[World], Awaitable[RuntimeSubject]]
) -> None:
    seen: list[UserPrincipal] = []
    app = FastAPI()

    async def probe(user: UserPrincipal = Depends(get_current_user)) -> dict[str, str]:
        seen.append(user)
        return {}

    app.add_api_route("/probe", probe, methods=["POST"])
    async with committed_world(async_engine) as world:
        subject = await build(world)
        await world.session.commit()
        issued = await issue_access_token(subject)
        for user_id in {issued.claims.usr.id, *(() if issued.claims.act is None else (issued.claims.act.id,))}:
            await invalidate_user(user_id)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/probe", headers={"Authorization": f"Bearer {issued.token}"})

        assert response.status_code == 200
        assert _normalized(seen[0]) == _normalized(await _oracle(async_engine, subject))


async def test_a_poisoned_role_cache_never_reaches_the_claims(async_engine: AsyncEngine) -> None:
    async with committed_world(async_engine) as world:
        organization = await world.organization()
        user = await world.person(organization)
        execution = await world.execution(run_user=user, organization=organization)
        await world.session.commit()
        redis = await get_shared_redis()
        await redis.set(
            _key(user.id),
            json.dumps(
                {"role_ids": [str(PLATFORM_ADMIN_ROLE_ID)], "role_names": ["Platform Admin"], "v": _CACHE_SCHEMA_VERSION}
            ),
        )
        try:
            subject = RuntimeSubject("workflow_execution", execution.id)
            claims = (await issue_access_token(subject)).claims
            # The cache-backed oracle is fooled; the claims are not.
            assert (await _oracle(async_engine, subject)).is_superuser is True
            principal = principal_from_claims(claims)
        finally:
            await invalidate_user(user.id)

        assert principal.is_superuser is False
        assert principal.role_ids == []
        assert claims.usr.roles == []
