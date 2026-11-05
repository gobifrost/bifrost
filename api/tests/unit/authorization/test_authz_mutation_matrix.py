"""Mutation matrix: every carried fact, changed the way the product changes it.

For each fact in plan §3.2: mint, change the fact through its service
function (or the ORM unit of work the routes use where no service function
exists), confirm the old token is now refused, refresh, and confirm the new
claims carry the change. Run, attempt and agent-run ends are refused by
liveness instead, and cannot refresh.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from src.models.contracts.workflow_permissions import (
    RequestedWorkflowPermissions,
    WorkflowPermissionGrantSpec,
    WorkflowPermissionMode,
)
from src.models.enums import ExecutionStatus
from src.models.orm.agent_runs import AgentRun
from src.models.orm.agents import Agent
from src.models.orm.config import SystemConfig
from src.models.orm.executions import Execution
from src.models.orm.organizations import Organization
from src.models.orm.services import ServiceAttempt, ServiceDefinition
from src.models.orm.solution_workflow_permission_requests import SolutionWorkflowPermissionRequest
from src.models.orm.solutions import Solution
from src.models.orm.users import Role, User, UserRoleBoundary
from src.models.orm.workflow_permissions import WorkflowPermissionGrant
from src.models.orm.workflows import Workflow
from src.services.authorization.authz import read_epoch
from src.services.authorization.context import Boundary, BoundaryKind
from src.services.authorization.tokens import (
    AccessTokenClaims,
    RuntimeSubject,
    TokenSubjectUnavailable,
    issue_access_token,
)
from src.services.role_permissions import set_role_permissions
from src.services.user_role_assignments import insert_assignment
from src.services.workflow_permissions import request_digest
from shared.sdk_users import set_user_base_role
from tests.unit.authorization.committed_runs import World, committed_world, token_accepted

Mutation = Callable[[AsyncSession], Awaitable[None]]


async def _change(
    engine: AsyncEngine, subject: RuntimeSubject, mutate: Mutation
) -> tuple[AccessTokenClaims, AccessTokenClaims]:
    """Mint, mutate, show the old token refused, and refresh."""
    before = (await issue_access_token(subject)).claims
    assert await token_accepted(engine, before)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        await mutate(session)
        await session.commit()
    assert not await token_accepted(engine, before)
    after = (await issue_access_token(subject)).claims
    assert await token_accepted(engine, after)
    return before, after


async def _workflow_run(world: World, **workflow_fields: object) -> tuple[RuntimeSubject, Organization, User, Workflow]:
    organization = await world.organization()
    user = await world.person(organization)
    workflow = await world.workflow(organization, **workflow_fields)
    execution = await world.execution(run_user=user, workflow=workflow, organization=organization)
    return RuntimeSubject("workflow_execution", execution.id), organization, user, workflow


# --- People and organizations ------------------------------------------------


async def test_email_and_name(async_engine: AsyncEngine) -> None:
    async with committed_world(async_engine) as world:
        subject, _, user, _ = await _workflow_run(world)
        await world.session.commit()

        async def rename(session: AsyncSession) -> None:
            person = await session.get(User, user.id)
            assert person is not None
            person.email, person.name = f"renamed-{person.email}", "Renamed"

        _, after = await _change(async_engine, subject, rename)
    assert (after.usr.email.startswith("renamed-"), after.usr.name) == (True, "Renamed")


async def test_home_organization(async_engine: AsyncEngine) -> None:
    async with committed_world(async_engine) as world:
        # Created first, so cleanup removes the moved user before it.
        destination = await world.organization()
        subject, _, user, _ = await _workflow_run(world)
        await world.session.commit()

        async def move(session: AsyncSession) -> None:
            await session.execute(update(User).where(User.id == user.id).values(organization_id=destination.id))

        _, after = await _change(async_engine, subject, move)
    assert after.usr.org == destination.id


async def test_base_role(async_engine: AsyncEngine) -> None:
    async with committed_world(async_engine) as world:
        base = await world.role("tables.read")
        subject, _, user, _ = await _workflow_run(world)
        await world.session.commit()

        async def rebase(session: AsyncSession) -> None:
            person = await session.get(User, user.id)
            assert person is not None
            await set_user_base_role(session, person, base.id)

        _, after = await _change(async_engine, subject, rebase)
    assert (after.usr.base.id, after.usr.base.perm) == (base.id, ["tables.read"])


async def test_external_flag(async_engine: AsyncEngine) -> None:
    async with committed_world(async_engine) as world:
        subject, _, user, _ = await _workflow_run(world)
        await world.session.commit()

        async def externalize(session: AsyncSession) -> None:
            await session.execute(update(User).where(User.id == user.id).values(is_external=True))

        _, after = await _change(async_engine, subject, externalize)
    assert after.usr.ext is True


async def test_deactivating_the_run_user_refreshes_and_continues(async_engine: AsyncEngine) -> None:
    async with committed_world(async_engine) as world:
        subject, _, user, _ = await _workflow_run(world)
        await world.session.commit()

        async def deactivate(session: AsyncSession) -> None:
            await session.execute(update(User).where(User.id == user.id).values(is_active=False))

        _, after = await _change(async_engine, subject, deactivate)
    assert after.usr.active is False


async def test_deleting_the_acting_user_ends_the_runs_authority(async_engine: AsyncEngine) -> None:
    async with committed_world(async_engine) as world:
        organization = await world.organization()
        run_user = await world.person(organization)
        actor = await world.person(organization)
        execution = await world.execution(run_user=run_user, actor=actor, organization=organization)
        await world.session.commit()
        subject = RuntimeSubject("workflow_execution", execution.id)
        before = (await issue_access_token(subject)).claims

        async with AsyncSession(async_engine) as session:
            await session.execute(delete(User).where(User.id == actor.id))
            await session.commit()

        assert not await token_accepted(async_engine, before)
        with pytest.raises(TokenSubjectUnavailable):
            await issue_access_token(subject)


async def test_provider_flag(async_engine: AsyncEngine) -> None:
    async with committed_world(async_engine) as world:
        subject, organization, _, _ = await _workflow_run(world)
        await world.session.commit()

        async def promote(session: AsyncSession) -> None:
            await session.execute(update(Organization).where(Organization.id == organization.id).values(is_provider=True))

        before, after = await _change(async_engine, subject, promote)
    assert (before.usr.prov, after.usr.prov) == (False, True)


# --- Roles ---------------------------------------------------------------------


async def test_role_assignment(async_engine: AsyncEngine) -> None:
    async with committed_world(async_engine) as world:
        role = await world.role("tables.read")
        subject, organization, user, _ = await _workflow_run(world)
        await world.session.commit()

        async def assign(session: AsyncSession) -> None:
            await insert_assignment(
                session, user_id=user.id, role_id=role.id,
                boundaries=[Boundary(BoundaryKind.ORGANIZATION, organization.id)], assigned_by="matrix",
            )

        _, after = await _change(async_engine, subject, assign)
    assert [(r.id, r.bnd) for r in after.usr.roles] == [(role.id, [(BoundaryKind.ORGANIZATION, organization.id)])]


async def test_role_boundary(async_engine: AsyncEngine) -> None:
    async with committed_world(async_engine) as world:
        role = await world.role("tables.read")
        subject, organization, user, _ = await _workflow_run(world)
        await world.assign(user, role.id, ("organization", organization.id))
        await world.session.commit()

        async def widen(session: AsyncSession) -> None:
            session.add(UserRoleBoundary(user_id=user.id, role_id=role.id, kind="managed_organizations"))

        _, after = await _change(async_engine, subject, widen)
    assert sorted(after.usr.roles[0].bnd, key=str) == sorted(
        [(BoundaryKind.ORGANIZATION, organization.id), (BoundaryKind.MANAGED_ORGANIZATIONS, None)], key=str
    )


async def test_role_permissions(async_engine: AsyncEngine) -> None:
    async with committed_world(async_engine) as world:
        role = await world.role("tables.read")
        subject, organization, user, _ = await _workflow_run(world)
        await world.assign(user, role.id, ("organization", organization.id))
        await world.session.commit()

        async def regrant(session: AsyncSession) -> None:
            await set_role_permissions(session, role_id=role.id, permissions=frozenset({"tables.read", "workflows.read"}))

        _, after = await _change(async_engine, subject, regrant)
    assert after.usr.roles[0].perm == ["tables.read", "workflows.read"]


async def test_role_name(async_engine: AsyncEngine) -> None:
    async with committed_world(async_engine) as world:
        role = await world.role("tables.read")
        subject, organization, user, _ = await _workflow_run(world)
        await world.assign(user, role.id, ("organization", organization.id))
        await world.session.commit()

        async def rename(session: AsyncSession) -> None:
            stored = await session.get(Role, role.id)
            assert stored is not None
            stored.name = f"{stored.name} Renamed"

        _, after = await _change(async_engine, subject, rename)
    assert after.usr.roles[0].name.endswith(" Renamed")


# --- Workflow powers and ownership --------------------------------------------


async def test_workflow_mode(async_engine: AsyncEngine) -> None:
    async with committed_world(async_engine) as world:
        subject, _, _, workflow = await _workflow_run(world, permission_mode="full")
        await world.session.commit()

        async def restrict(session: AsyncSession) -> None:
            stored = await session.get(Workflow, workflow.id)
            assert stored is not None
            stored.permission_mode = WorkflowPermissionMode.RESTRICTED.value

        _, after = await _change(async_engine, subject, restrict)
    assert after.run.powers is not None and after.run.powers.mode is WorkflowPermissionMode.RESTRICTED


async def test_workflow_grants(async_engine: AsyncEngine) -> None:
    async with committed_world(async_engine) as world:
        subject, _, _, workflow = await _workflow_run(world, permission_mode="restricted")
        await world.session.commit()

        async def grant(session: AsyncSession) -> None:
            session.add(WorkflowPermissionGrant(workflow_id=workflow.id, permission="tables.read"))

        before, after = await _change(async_engine, subject, grant)
    assert before.run.powers is not None and before.run.powers.grants == []
    assert after.run.powers is not None and after.run.powers.grants == ["tables.read"]


async def test_workflow_ownership(async_engine: AsyncEngine) -> None:
    async with committed_world(async_engine) as world:
        subject, organization, _, workflow = await _workflow_run(world)
        solution = await world.solution(organization)
        await world.session.commit()

        async def adopt(session: AsyncSession) -> None:
            # Deploy writes ownership with Core statements.
            await session.execute(update(Workflow).where(Workflow.id == workflow.id).values(solution_id=solution.id))

        _, after = await _change(async_engine, subject, adopt)
    assert (after.run.sol, after.run.sol_active) == (solution.id, True)


async def test_solution_permission_approval(async_engine: AsyncEngine) -> None:
    requested = RequestedWorkflowPermissions(
        mode=WorkflowPermissionMode.RESTRICTED, grants=[WorkflowPermissionGrantSpec(permission="tables.read")]
    )
    async with committed_world(async_engine) as world:
        organization = await world.organization()
        solution = await world.solution(organization)
        subject, _, _, workflow = await _workflow_run(world, solution_id=solution.id)
        world.session.add(
            SolutionWorkflowPermissionRequest(
                solution_id=solution.id, workflow_id=workflow.id, requested_mode=requested.mode.value,
                requested_grants=[{"permission": "tables.read"}], request_digest=request_digest(requested),
                status="pending",
            )
        )
        await world.session.commit()

        async def approve(session: AsyncSession) -> None:
            stored = (
                await session.execute(
                    select(SolutionWorkflowPermissionRequest).where(
                        SolutionWorkflowPermissionRequest.workflow_id == workflow.id
                    )
                )
            ).scalar_one()
            stored.status, stored.approved_digest = "approved", stored.request_digest

        before, after = await _change(async_engine, subject, approve)
    assert before.run.powers is not None and before.run.powers.grants == []
    assert after.run.powers is not None and after.run.powers.grants == ["tables.read"]


async def test_platform_default_workflow_mode(async_engine: AsyncEngine) -> None:
    default_row = (
        SystemConfig.category == "workflow_permissions",
        SystemConfig.key == "default_mode",
        SystemConfig.organization_id.is_(None),
    )
    async with AsyncSession(async_engine) as session:
        previous = (await session.execute(select(SystemConfig.value_json).where(*default_row))).one_or_none()
    try:
        async with committed_world(async_engine) as world:
            subject, _, _, _ = await _workflow_run(world)
            await world.session.commit()

            async def restrict_default(session: AsyncSession) -> None:
                await session.execute(delete(SystemConfig).where(*default_row))
                session.add(
                    SystemConfig(
                        category="workflow_permissions", key="default_mode",
                        value_json={"mode": WorkflowPermissionMode.RESTRICTED.value}, organization_id=None,
                    )
                )

            _, after = await _change(async_engine, subject, restrict_default)
        assert after.run.powers is not None and after.run.powers.mode is WorkflowPermissionMode.RESTRICTED
    finally:
        async with AsyncSession(async_engine) as session:
            await session.execute(delete(SystemConfig).where(*default_row))
            if previous is not None:
                session.add(
                    SystemConfig(
                        category="workflow_permissions", key="default_mode", value_json=previous.value_json,
                        organization_id=None,
                    )
                )
            await session.commit()


async def test_solution_uninstall(async_engine: AsyncEngine) -> None:
    async with committed_world(async_engine) as world:
        organization = await world.organization()
        solution = await world.solution(organization)
        subject, _, _, _ = await _workflow_run(world, solution_id=solution.id)
        await world.session.commit()

        async def uninstall(session: AsyncSession) -> None:
            await session.execute(update(Solution).where(Solution.id == solution.id).values(status="inactive"))

        before, after = await _change(async_engine, subject, uninstall)
    assert (before.run.sol_active, after.run.sol_active) == (True, False)


async def test_agent_solution_binding(async_engine: AsyncEngine) -> None:
    async with committed_world(async_engine) as world:
        organization = await world.organization()
        user = await world.person(organization)
        solution = await world.solution(organization)
        agent = await world.agent(organization)
        run = await world.agent_run(run_user=user, actor=user, organization=organization, agent=agent)
        await world.session.commit()

        async def bind(session: AsyncSession) -> None:
            await session.execute(update(Agent).where(Agent.id == agent.id).values(solution_id=solution.id))

        before, after = await _change(async_engine, RuntimeSubject("agent_run", run.id), bind)
    assert (before.run.sol, after.run.sol) == (None, solution.id)


async def test_service_definition_organization(async_engine: AsyncEngine) -> None:
    async with committed_world(async_engine) as world:
        organization = await world.organization()
        destination = await world.organization()
        user = await world.person(organization)
        workflow = await world.workflow(organization)
        attempt = await world.service_attempt(run_user=user, workflow=workflow, organization=organization)
        await world.session.commit()

        async def move(session: AsyncSession) -> None:
            stored = await session.get(ServiceDefinition, attempt.service_id)
            assert stored is not None
            stored.organization_id = destination.id

        _, after = await _change(
            async_engine, RuntimeSubject("service_attempt", attempt.id, attempt.service_id), move
        )
    assert after.run.org == destination.id


# --- Run ends: refused by liveness, no refresh ---------------------------------


async def _ended(engine: AsyncEngine, subject: RuntimeSubject, end: Mutation) -> None:
    before = (await issue_access_token(subject)).claims
    epoch = int(before.av)
    async with AsyncSession(engine) as session:
        await end(session)
        await session.commit()

    assert not await token_accepted(engine, before)
    # Ending a run changes no authority fact.
    async with AsyncSession(engine) as session:
        assert await read_epoch(session) == epoch
    with pytest.raises(TokenSubjectUnavailable):
        await issue_access_token(subject)


async def test_a_finished_workflow_run(async_engine: AsyncEngine) -> None:
    async with committed_world(async_engine) as world:
        subject, _, _, _ = await _workflow_run(world)
        await world.session.commit()

        async def finish(session: AsyncSession) -> None:
            await session.execute(
                update(Execution).where(Execution.id == subject.credential_id).values(status=ExecutionStatus.SUCCESS)
            )

        await _ended(async_engine, subject, finish)


async def test_a_lapsed_service_lease(async_engine: AsyncEngine) -> None:
    async with committed_world(async_engine) as world:
        organization = await world.organization()
        user = await world.person(organization)
        workflow = await world.workflow(organization)
        attempt = await world.service_attempt(run_user=user, workflow=workflow, organization=organization)
        await world.session.commit()

        async def lapse(session: AsyncSession) -> None:
            await session.execute(
                update(ServiceAttempt)
                .where(ServiceAttempt.id == attempt.id)
                .values(lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
            )

        await _ended(async_engine, RuntimeSubject("service_attempt", attempt.id, attempt.service_id), lapse)


async def test_a_finished_agent_run(async_engine: AsyncEngine) -> None:
    async with committed_world(async_engine) as world:
        organization = await world.organization()
        user = await world.person(organization)
        run = await world.agent_run(run_user=user, actor=user, organization=organization)
        await world.session.commit()

        async def finish(session: AsyncSession) -> None:
            await session.execute(update(AgentRun).where(AgentRun.id == run.id).values(status="completed"))

        await _ended(async_engine, RuntimeSubject("agent_run", run.id), finish)
