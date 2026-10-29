"""Database-backed runtime authority resolution."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, event, inspect
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncSession

from shared.sdk_users import set_platform_admin
from src.core.constants import SYSTEM_USER_UUID
from src.models.contracts.workflow_permissions import WorkflowPermissionMode
from src.models.enums import ExecutionStatus
from src.models.orm.agent_runs import AgentRun
from src.models.orm.executions import Execution
from src.models.orm.organizations import Organization
from src.models.orm.services import ServiceAttempt, ServiceDefinition
from src.models.orm.users import User
from src.models.orm.workflow_permissions import WorkflowPermissionGrant
from src.models.orm.workflows import Workflow
from src.services.authorization.runtime import RuntimeIdentityError, load_runtime_authority
from src.services.authorization.runtime_auth import load_runtime_principal


async def _organization(session: AsyncSession, name: str) -> Organization:
    organization = Organization(name=f"{name} {uuid4().hex[:8]}", created_by="runtime-test")
    session.add(organization)
    await session.flush()
    return organization


async def _person(session: AsyncSession, organization: Organization, name: str) -> User:
    person = User(
        email=f"{name}-{uuid4().hex[:8]}@example.test",
        name=name,
        organization_id=organization.id,
    )
    session.add(person)
    await session.flush()
    return person


async def _workflow(session: AsyncSession, organization_id: UUID | None, **fields: object) -> Workflow:
    name = f"runtime_{uuid4().hex[:8]}"
    workflow = Workflow(
        name=name,
        function_name=name,
        path=f"workflows/{name}.py",
        organization_id=organization_id,
        **fields,
    )
    session.add(workflow)
    await session.flush()
    return workflow


async def _execution(
    session: AsyncSession,
    *,
    workflow: Workflow | None,
    run_user: User | None,
    actor: User | None,
    organization_id: UUID | None,
    status: ExecutionStatus = ExecutionStatus.RUNNING,
    started_by_user_id: UUID | None = None,
    root_execution_id: UUID | None = None,
) -> Execution:
    execution = Execution(
        workflow_name="runtime",
        executed_by_name="Runtime",
        workflow_id=None if workflow is None else workflow.id,
        run_user_id=None if run_user is None else run_user.id,
        executed_by=None if actor is None else actor.id,
        organization_id=organization_id,
        status=status,
        started_by_user_id=started_by_user_id,
        root_execution_id=root_execution_id,
    )
    session.add(execution)
    await session.flush()
    return execution


@pytest.mark.asyncio
async def test_workflow_runtime_loads_separate_actor_and_restricted_powers(db_session: AsyncSession) -> None:
    contoso = await _organization(db_session, "Contoso")
    fabrikam = await _organization(db_session, "Fabrikam")
    run_user = await _person(db_session, contoso, "run-user")
    actor = await _person(db_session, fabrikam, "actor")
    workflow = await _workflow(db_session, contoso.id, permission_mode="restricted")
    db_session.add(WorkflowPermissionGrant(workflow_id=workflow.id, permission="secrets.read"))
    await db_session.flush()
    execution = await _execution(
        db_session,
        workflow=workflow,
        run_user=run_user,
        actor=actor,
        organization_id=contoso.id,
        started_by_user_id=actor.id,
        root_execution_id=uuid4(),
    )

    authority = await load_runtime_authority(db_session, "workflow_execution", execution.id)

    assert authority.kind == "workflow_execution"
    assert authority.credential_id == execution.id
    assert authority.run_user.user_id == run_user.id
    assert authority.acting_user_id == actor.id
    assert authority.organization_id == contoso.id
    assert authority.workflow_id == workflow.id
    assert authority.started_by_user_id == actor.id
    assert authority.root_execution_id == execution.root_execution_id
    assert authority.powers is not None
    assert authority.powers.mode is WorkflowPermissionMode.RESTRICTED
    assert [grant.permission for grant in authority.powers.grants] == ["secrets.read"]


@pytest.mark.asyncio
async def test_workflow_runtime_retains_durable_impersonation_provenance(db_session: AsyncSession) -> None:
    organization = await _organization(db_session, "Contoso")
    user = await _person(db_session, organization, "runtime-user")
    execution = await _execution(
        db_session,
        workflow=None,
        run_user=user,
        actor=user,
        organization_id=organization.id,
        started_by_user_id=user.id,
        root_execution_id=uuid4(),
    )
    execution.impersonated_by_user_id = uuid4()
    execution.impersonation_session_id = uuid4()
    await db_session.flush()

    authority = await load_runtime_authority(db_session, "workflow_execution", execution.id)

    assert authority.impersonated_by_user_id == execution.impersonated_by_user_id
    assert authority.impersonation_session_id == execution.impersonation_session_id


@pytest.mark.asyncio
async def test_workflow_runtime_does_not_give_inline_execution_default_powers(db_session: AsyncSession) -> None:
    contoso = await _organization(db_session, "Contoso")
    user = await _person(db_session, contoso, "inline-user")
    execution = await _execution(
        db_session,
        workflow=None,
        run_user=user,
        actor=user,
        organization_id=contoso.id,
        started_by_user_id=user.id,
        root_execution_id=uuid4(),
    )

    authority = await load_runtime_authority(db_session, "workflow_execution", execution.id)

    assert authority.workflow_id is None
    assert authority.powers is None


@pytest.mark.asyncio
async def test_workflow_runtime_loads_full_powers_from_its_exact_workflow(db_session: AsyncSession) -> None:
    contoso = await _organization(db_session, "Contoso")
    user = await _person(db_session, contoso, "full-user")
    workflow = await _workflow(db_session, contoso.id, permission_mode="full")
    execution = await _execution(
        db_session,
        workflow=workflow,
        run_user=user,
        actor=user,
        organization_id=contoso.id,
        started_by_user_id=user.id,
        root_execution_id=uuid4(),
    )

    authority = await load_runtime_authority(db_session, "workflow_execution", execution.id)

    assert authority.powers is not None
    assert authority.powers.mode is WorkflowPermissionMode.FULL
    assert authority.powers.grants == ()


@pytest.mark.asyncio
async def test_workflow_runtime_refuses_terminal_missing_lineage_and_sentinel_actor(db_session: AsyncSession) -> None:
    contoso = await _organization(db_session, "Contoso")
    user = await _person(db_session, contoso, "runtime-user")
    workflow = await _workflow(db_session, contoso.id)
    terminal = await _execution(
        db_session,
        workflow=workflow,
        run_user=user,
        actor=user,
        organization_id=contoso.id,
        status=ExecutionStatus.SUCCESS,
        started_by_user_id=user.id,
        root_execution_id=uuid4(),
    )
    missing_lineage = await _execution(
        db_session,
        workflow=workflow,
        run_user=user,
        actor=user,
        organization_id=contoso.id,
    )
    sentinel = await _execution(
        db_session,
        workflow=workflow,
        run_user=user,
        actor=None,
        organization_id=contoso.id,
        started_by_user_id=user.id,
        root_execution_id=uuid4(),
    )
    sentinel.executed_by = SYSTEM_USER_UUID
    await db_session.flush()

    with pytest.raises(RuntimeIdentityError):
        await load_runtime_authority(db_session, "workflow_execution", terminal.id)
    with pytest.raises(RuntimeIdentityError):
        await load_runtime_authority(db_session, "workflow_execution", missing_lineage.id)
    with pytest.raises(RuntimeIdentityError):
        await load_runtime_authority(db_session, "workflow_execution", sentinel.id)


@pytest.mark.asyncio
async def test_runtime_reloads_run_user_roles_on_each_call(db_session: AsyncSession) -> None:
    contoso = await _organization(db_session, "Contoso")
    user = await _person(db_session, contoso, "role-user")
    workflow = await _workflow(db_session, contoso.id)
    execution = await _execution(
        db_session,
        workflow=workflow,
        run_user=user,
        actor=user,
        organization_id=contoso.id,
        started_by_user_id=user.id,
        root_execution_id=uuid4(),
    )
    await set_platform_admin(db_session, user, True, assigned_by="runtime-test")
    # Keep a second administrator so the lockout guard permits this revocation.
    other_admin = await _person(db_session, contoso, "remaining-admin")
    await set_platform_admin(db_session, other_admin, True, assigned_by="runtime-test")

    elevated = await load_runtime_authority(db_session, "workflow_execution", execution.id)

    await set_platform_admin(db_session, user, False, assigned_by="runtime-test")
    revoked = await load_runtime_authority(db_session, "workflow_execution", execution.id)

    assert elevated.run_user.is_platform_admin is True
    assert revoked.run_user.is_platform_admin is False


@pytest.mark.asyncio
async def test_same_user_runtime_principal_reuses_current_authority_and_refreshes_next_call(
    db_session: AsyncSession,
) -> None:
    """A same-user execution checks current authority once per request."""
    organization = await _organization(db_session, "Contoso")
    user = await _person(db_session, organization, "runtime-user")
    workflow = await _workflow(db_session, organization.id, permission_mode="full")
    execution = await _execution(
        db_session,
        workflow=workflow,
        run_user=user,
        actor=user,
        organization_id=organization.id,
        started_by_user_id=user.id,
        root_execution_id=uuid4(),
    )
    await set_platform_admin(db_session, user, True, assigned_by="runtime-test")

    payload = {"credential_kind": "workflow_execution", "sub": str(execution.id)}
    initial = await load_runtime_principal(db_session, payload)
    assert initial is not None
    assert initial.is_superuser is True

    statements: list[str] = []

    def count_statement(conn, cursor, statement, parameters, context, executemany) -> None:
        statements.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", count_statement)
    try:
        warmed = await load_runtime_principal(db_session, payload)
    finally:
        event.remove(engine, "before_cursor_execute", count_statement)

    assert warmed is not None
    assert warmed.is_superuser is True
    assert len(statements) <= 8

    # The reuse is request-scoped: the next call must still observe revocation
    # and deletion from durable state.
    other_admin = await _person(db_session, organization, "remaining-admin")
    await set_platform_admin(db_session, other_admin, True, assigned_by="runtime-test")
    await set_platform_admin(db_session, user, False, assigned_by="runtime-test")
    revoked = await load_runtime_principal(db_session, payload)
    assert revoked is not None
    assert revoked.is_superuser is False

    await db_session.delete(user)
    await db_session.flush()
    assert await load_runtime_principal(db_session, payload) is None


@pytest.mark.asyncio
async def test_workflow_runtime_authority_defers_unneeded_execution_payloads(
    db_session: AsyncSession,
) -> None:
    """Authority reads only current authority fields, not historical run payloads."""
    organization = await _organization(db_session, "Contoso")
    user = await _person(db_session, organization, "runtime-user")
    workflow = await _workflow(db_session, organization.id, permission_mode="full")
    execution = await _execution(
        db_session,
        workflow=workflow,
        run_user=user,
        actor=user,
        organization_id=organization.id,
        started_by_user_id=user.id,
        root_execution_id=uuid4(),
    )
    execution.parameters = {"payload": "x" * 250_000}
    execution.result = {"result": "y" * 250_000}
    execution.variables = {"variables": "z" * 250_000}
    execution.execution_context = {"context": "a" * 250_000}
    execution.error_message = "b" * 250_000
    await db_session.flush()

    execution_id = execution.id
    root_execution_id = execution.root_execution_id
    db_session.expire(execution)

    authority = await load_runtime_authority(db_session, "workflow_execution", execution_id)

    assert (authority.credential_id, authority.acting_user_id, authority.run_user.user_id) == (
        execution_id,
        user.id,
        user.id,
    )
    assert (authority.workflow_id, authority.organization_id, authority.root_execution_id) == (
        workflow.id,
        organization.id,
        root_execution_id,
    )
    assert authority.powers is not None and authority.powers.mode is WorkflowPermissionMode.FULL
    assert {"parameters", "result", "variables", "execution_context", "error_message"} <= inspect(
        execution
    ).unloaded
    with pytest.raises(InvalidRequestError):
        _ = execution.parameters


@pytest.mark.asyncio
async def test_service_runtime_uses_pinned_attempt_identity_after_workflow_reassignment(db_session: AsyncSession) -> None:
    contoso = await _organization(db_session, "Contoso")
    pinned_user = await _person(db_session, contoso, "pinned-user")
    reassigned_identity = await _person(db_session, contoso, "new-identity")
    workflow = await _workflow(db_session, contoso.id, permission_mode="restricted")
    definition = ServiceDefinition(workflow_id=workflow.id, organization_id=contoso.id, created_by="runtime-test")
    db_session.add(definition)
    await db_session.flush()
    attempt = ServiceAttempt(
        service_id=definition.id,
        run_user_id=pinned_user.id,
        lease_token="runtime-lease",
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        state="stopping",
    )
    db_session.add(attempt)
    workflow.run_identity_id = reassigned_identity.id
    await db_session.flush()

    authority = await load_runtime_authority(
        db_session, "service_attempt", attempt.id, service_id=definition.id
    )

    assert authority.run_user.user_id == pinned_user.id
    assert authority.acting_user_id == pinned_user.id
    assert authority.workflow_id == workflow.id
    assert authority.organization_id == contoso.id
    assert authority.started_by_user_id is None
    assert authority.root_execution_id is None
    assert authority.service_id == definition.id
    assert authority.powers is not None
    assert authority.powers.mode is WorkflowPermissionMode.RESTRICTED


@pytest.mark.asyncio
async def test_service_runtime_refuses_mismatched_service_and_expired_lease(db_session: AsyncSession) -> None:
    contoso = await _organization(db_session, "Contoso")
    user = await _person(db_session, contoso, "service-user")
    workflow = await _workflow(db_session, contoso.id)
    definition = ServiceDefinition(workflow_id=workflow.id, organization_id=contoso.id, created_by="runtime-test")
    db_session.add(definition)
    await db_session.flush()
    attempt = ServiceAttempt(
        service_id=definition.id,
        run_user_id=user.id,
        lease_token="expired-lease",
        lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        state="running",
    )
    db_session.add(attempt)
    await db_session.flush()

    with pytest.raises(RuntimeIdentityError):
        await load_runtime_authority(db_session, "service_attempt", attempt.id, service_id=uuid4())
    with pytest.raises(RuntimeIdentityError):
        await load_runtime_authority(db_session, "service_attempt", attempt.id, service_id=definition.id)


@pytest.mark.asyncio
async def test_agent_runtime_uses_pinned_actor_and_never_workflow_powers(
    db_session: AsyncSession,
) -> None:
    contoso = await _organization(db_session, "Contoso")
    run_user = await _person(db_session, contoso, "agent-run-user")
    caller = await _person(db_session, contoso, "agent-caller")
    run_as = await _person(db_session, contoso, "agent-run-as")
    run_as_run = AgentRun(
        trigger_type="chat",
        status="running",
        org_id=contoso.id,
        run_user_id=run_user.id,
        caller_user_id=str(caller.id),
        run_as_user_id=run_as.id,
        acting_user_id=run_as.id,
    )
    caller_run = AgentRun(
        trigger_type="chat",
        status="cancelling",
        org_id=contoso.id,
        run_user_id=run_user.id,
        caller_user_id=str(caller.id),
        acting_user_id=caller.id,
    )
    unattended_run = AgentRun(
        trigger_type="event",
        status="running",
        org_id=contoso.id,
        run_user_id=run_user.id,
        acting_user_id=run_user.id,
    )
    db_session.add_all([run_as_run, caller_run, unattended_run])
    await db_session.flush()

    run_as_authority = await load_runtime_authority(db_session, "agent_run", run_as_run.id)
    caller_authority = await load_runtime_authority(db_session, "agent_run", caller_run.id)
    unattended_authority = await load_runtime_authority(db_session, "agent_run", unattended_run.id)

    assert run_as_authority.acting_user_id == run_as.id
    assert caller_authority.acting_user_id == caller.id
    assert unattended_authority.acting_user_id == run_user.id
    assert run_as_authority.run_user.user_id == run_user.id
    assert run_as_authority.powers is None
    assert run_as_authority.workflow_id is None
    assert run_as_authority.service_id is None


@pytest.mark.asyncio
async def test_agent_runtime_refuses_terminal_or_unbound_actor(db_session: AsyncSession) -> None:
    contoso = await _organization(db_session, "Contoso")
    user = await _person(db_session, contoso, "agent-user")
    terminal = AgentRun(
        trigger_type="chat",
        status="finished",
        org_id=contoso.id,
        run_user_id=user.id,
        acting_user_id=user.id,
    )
    unbound_actor = AgentRun(
        trigger_type="chat",
        status="running",
        org_id=contoso.id,
        run_user_id=user.id,
    )
    db_session.add_all([terminal, unbound_actor])
    await db_session.flush()

    with pytest.raises(RuntimeIdentityError):
        await load_runtime_authority(db_session, "agent_run", terminal.id)
    with pytest.raises(RuntimeIdentityError):
        await load_runtime_authority(db_session, "agent_run", unbound_actor.id)


@pytest.mark.asyncio
async def test_agent_runtime_refuses_a_deleted_pinned_actor(db_session: AsyncSession) -> None:
    contoso = await _organization(db_session, "Contoso")
    run_user = await _person(db_session, contoso, "agent-run-user")
    deleted_actor = await _person(db_session, contoso, "deleted-agent-actor")
    run = AgentRun(
        trigger_type="event",
        status="running",
        org_id=contoso.id,
        run_user_id=run_user.id,
        acting_user_id=deleted_actor.id,
    )
    db_session.add(run)
    await db_session.flush()
    await db_session.execute(delete(User).where(User.id == deleted_actor.id))
    await db_session.flush()
    db_session.expunge_all()

    with pytest.raises(RuntimeIdentityError):
        await load_runtime_authority(db_session, "agent_run", run.id)
