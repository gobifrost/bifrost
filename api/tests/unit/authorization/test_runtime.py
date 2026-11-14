"""Database-backed runtime authority resolution."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, event, inspect, update
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncSession

from shared.sdk_users import set_platform_admin
from shared.identities import ensure_default_identity
from src.core.constants import SYSTEM_USER_UUID
from src.core.security import create_access_token, decode_token
from src.models.contracts.workflow_permissions import WorkflowPermissionMode
from src.models.enums import AgentAccessLevel, ExecutionStatus
from src.models.orm.agent_runs import AgentRun
from src.models.orm.agents import Agent
from src.models.orm.executions import Execution
from src.models.orm.organizations import Organization
from src.models.orm.services import ServiceAttempt, ServiceDefinition
from src.models.orm.solutions import Solution
from src.models.orm.users import User
from src.models.orm.workflow_permissions import WorkflowPermissionGrant
from src.models.orm.workflows import Workflow
from src.services.authorization.runtime import RuntimeIdentityError, load_runtime_authority
from src.services.authorization.runtime_auth import load_runtime_principal
from tests.helpers.runtime_tokens import legacy_engine_token


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


def _previous_stable_engine_token(execution_id: UUID, **overrides: object) -> str:
    """The v1.4.1 ``mint_engine_token`` wire format, not PR1's typed format."""
    claims: dict[str, object] = {
        "sub": str(SYSTEM_USER_UUID),
        "email": "engine@bifrost.internal",
        "name": "Bifrost Engine",
        "is_superuser": True,
        "engine_execution_id": str(execution_id),
        "engine_solution_id": None,
        "engine_global_repo_access": True,
    }
    claims.update(overrides)
    return create_access_token(claims)


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
async def test_workflow_runtime_retains_agent_origin_for_nested_workflows(
    db_session: AsyncSession,
) -> None:
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
    execution.execution_origin = "agent"
    execution.agent_id = uuid4()
    await db_session.flush()

    authority = await load_runtime_authority(db_session, "workflow_execution", execution.id)

    from src.services.authorization.runtime import load_execution_provenance
    provenance = await load_execution_provenance(db_session, authority)
    assert (provenance.origin, provenance.agent_id) == ("agent", execution.agent_id)


@pytest.mark.asyncio
async def test_workflow_runtime_treats_malformed_durable_agent_provenance_as_unknown(
    db_session: AsyncSession,
) -> None:
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
    execution.execution_origin = "agent"
    execution.agent_id = None
    await db_session.flush()

    authority = await load_runtime_authority(db_session, "workflow_execution", execution.id)

    from src.services.authorization.runtime import load_execution_provenance
    provenance = await load_execution_provenance(db_session, authority)
    assert (provenance.origin, provenance.agent_id) == ("unknown", None)


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
async def test_previous_stable_engine_token_uses_only_its_live_durable_execution(
    db_session: AsyncSession,
) -> None:
    """v1.4.1's engine token is resolved from the run, never its admin flags."""
    organization = await _organization(db_session, "Contoso")
    user = await _person(db_session, organization, "runtime-user")
    workflow = await _workflow(db_session, organization.id, permission_mode="restricted")
    execution = await _execution(
        db_session,
        workflow=workflow,
        run_user=user,
        actor=user,
        organization_id=organization.id,
        started_by_user_id=user.id,
        root_execution_id=uuid4(),
    )

    token = _previous_stable_engine_token(
        execution.id,
        engine_solution_id=str(uuid4()),
        engine_global_repo_access=False,
        is_superuser=True,
    )
    payload = decode_token(token, expected_type="access")
    assert payload is not None

    principal = await load_runtime_principal(db_session, payload)

    assert principal is not None
    assert principal.user_id == user.id
    assert principal.runtime_authority.credential_id == execution.id
    assert principal.runtime_authority.run_user.user_id == user.id
    assert principal.runtime_authority.powers is not None
    assert principal.runtime_authority.powers.mode is WorkflowPermissionMode.RESTRICTED
    assert principal.is_superuser is False


@pytest.mark.asyncio
async def test_pr1_typed_legacy_engine_token_keeps_its_distinct_runtime_contract(
    db_session: AsyncSession,
) -> None:
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
    token, _ = legacy_engine_token(execution_id=str(execution.id), timeout_seconds=300)
    payload = decode_token(token, expected_type="access")
    assert payload is not None
    assert payload["credential_kind"] == "workflow_execution"
    assert payload["sub"] == str(execution.id)

    principal = await load_runtime_principal(db_session, payload)

    assert principal is not None
    assert principal.user_id == user.id
    assert principal.runtime_authority.credential_id == execution.id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"sub": str(uuid4())},
        {"credential_kind": "workflow_execution"},
        {"service_id": str(uuid4())},
        {"engine_agent_run_id": str(uuid4())},
        {"ver": 1},
        {"engine_global_repo_access": "true"},
    ],
    ids=["non-system-subject", "typed-mix", "service-mix", "agent-mix", "versioned", "wrong-shape"],
)
async def test_previous_stable_engine_token_rejects_ambiguous_or_malformed_claim_shapes(
    db_session: AsyncSession, overrides: dict[str, object]
) -> None:
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
    payload = decode_token(_previous_stable_engine_token(execution.id), expected_type="access")
    assert payload is not None
    payload.update(overrides)

    assert await load_runtime_principal(db_session, payload) is None


@pytest.mark.asyncio
async def test_previous_stable_engine_token_recovers_old_live_run_and_propagates_lineage(
    db_session: AsyncSession,
) -> None:
    """An old worker may change Pending to Running after the migration passed."""
    organization = await _organization(db_session, "Contoso")
    user = await _person(db_session, organization, "runtime-user")
    execution = await _execution(
        db_session,
        workflow=None,
        run_user=None,
        actor=user,
        organization_id=organization.id,
    )
    payload = decode_token(_previous_stable_engine_token(execution.id), expected_type="access")
    assert payload is not None

    principal = await load_runtime_principal(db_session, payload)

    assert principal is not None
    assert principal.user_id == user.id
    assert principal.run_user_id == user.id
    assert principal.started_by_user_id == user.id
    assert principal.root_execution_id == execution.id
    from shared.run_lineage import RunLineage, principal_lineage

    assert await principal_lineage(db_session, principal) == RunLineage(
        run_user_id=user.id,
        started_by_user_id=user.id,
        root_execution_id=execution.id,
    )


@pytest.mark.asyncio
async def test_previous_stable_unattended_run_uses_its_scoped_default_identity(
    db_session: AsyncSession,
) -> None:
    organization = await _organization(db_session, "Contoso")
    identity = await ensure_default_identity(db_session, organization)
    workflow = await _workflow(db_session, organization.id, permission_mode="restricted")
    execution = await _execution(
        db_session,
        workflow=workflow,
        run_user=None,
        actor=None,
        organization_id=organization.id,
    )
    execution.executed_by = SYSTEM_USER_UUID
    await db_session.flush()
    payload = decode_token(_previous_stable_engine_token(execution.id), expected_type="access")
    assert payload is not None

    principal = await load_runtime_principal(db_session, payload)

    assert principal is not None
    assert principal.user_id == principal.run_user_id == identity.id
    assert principal.organization_id == organization.id
    assert principal.is_superuser is False
    assert principal.runtime_authority.powers is not None
    assert principal.runtime_authority.powers.mode is WorkflowPermissionMode.RESTRICTED
    assert execution.executed_by == SYSTEM_USER_UUID
    assert execution.run_user_id is None


@pytest.mark.asyncio
async def test_previous_stable_engine_token_accepts_filled_lineage_with_historic_system_actor_only(
    db_session: AsyncSession,
) -> None:
    organization = await _organization(db_session, "Contoso")
    user = await _person(db_session, organization, "runtime-user")
    execution = await _execution(
        db_session,
        workflow=None,
        run_user=user,
        actor=None,
        organization_id=organization.id,
        started_by_user_id=user.id,
        root_execution_id=uuid4(),
    )
    execution.executed_by = SYSTEM_USER_UUID
    await db_session.flush()
    payload = decode_token(_previous_stable_engine_token(execution.id), expected_type="access")
    assert payload is not None

    stable = await load_runtime_principal(db_session, payload)
    typed = await load_runtime_principal(
        db_session,
        {"credential_kind": "workflow_execution", "sub": str(execution.id)},
    )

    assert stable is not None
    assert stable.user_id == user.id
    assert stable.runtime_authority.acting_user_id == user.id
    assert typed is None


@pytest.mark.asyncio
async def test_previous_stable_engine_token_rejects_ended_missing_and_unresolvable_runs(
    db_session: AsyncSession,
) -> None:
    organization = await _organization(db_session, "Contoso")
    user = await _person(db_session, organization, "runtime-user")
    ended = await _execution(
        db_session,
        workflow=None,
        run_user=user,
        actor=user,
        organization_id=organization.id,
        status=ExecutionStatus.SUCCESS,
        started_by_user_id=user.id,
        root_execution_id=uuid4(),
    )
    unresolvable = await _execution(
        db_session,
        workflow=None,
        run_user=None,
        actor=None,
        organization_id=organization.id,
    )

    for execution_id in (ended.id, unresolvable.id, uuid4()):
        payload = decode_token(_previous_stable_engine_token(execution_id), expected_type="access")
        assert payload is not None
        assert await load_runtime_principal(db_session, payload) is None


@pytest.mark.asyncio
async def test_previous_stable_engine_token_rejects_partial_lineage_and_missing_default_identity(
    db_session: AsyncSession,
) -> None:
    organization = await _organization(db_session, "Contoso")
    user = await _person(db_session, organization, "runtime-user")
    partial = await _execution(
        db_session,
        workflow=None,
        run_user=user,
        actor=user,
        organization_id=organization.id,
    )
    no_identity = await _execution(
        db_session,
        workflow=None,
        run_user=None,
        actor=None,
        organization_id=organization.id,
    )
    no_identity.executed_by = SYSTEM_USER_UUID
    await db_session.flush()

    for execution_id in (partial.id, no_identity.id):
        payload = decode_token(_previous_stable_engine_token(execution_id), expected_type="access")
        assert payload is not None
        assert await load_runtime_principal(db_session, payload) is None


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
async def test_direct_agent_runtime_uses_pinned_actor_without_workflow_powers(
    db_session: AsyncSession,
) -> None:
    contoso = await _organization(db_session, "Contoso")
    run_user = await _person(db_session, contoso, "agent-run-user")
    caller = await _person(db_session, contoso, "agent-caller")
    run_as = await _person(db_session, contoso, "agent-run-as")
    agent = Agent(
        name=f"runtime-agent-{uuid4().hex[:8]}",
        system_prompt="test",
        organization_id=contoso.id,
        created_by="runtime-test",
    )
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
        agent_id=agent.id,
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
    db_session.add(agent)
    await db_session.flush()
    caller_run.agent_id = agent.id
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
    from src.services.authorization.runtime import load_execution_provenance
    provenance = await load_execution_provenance(db_session, caller_authority)
    assert (provenance.origin, provenance.agent_id) == ("agent", agent.id)
    unattended = await load_execution_provenance(db_session, unattended_authority)
    assert unattended.origin == "unknown"


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


async def _solution(session: AsyncSession, organization: Organization, status: str) -> Solution:
    solution = Solution(
        slug=f"runtime-{uuid4().hex[:8]}", name="Runtime", organization_id=organization.id, status=status
    )
    session.add(solution)
    await session.flush()
    return solution


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "active"), [("active", True), ("inactive", False)])
async def test_workflow_runtime_reports_whether_its_own_solution_is_active(
    db_session: AsyncSession, status: str, active: bool
) -> None:
    contoso = await _organization(db_session, "Contoso")
    user = await _person(db_session, contoso, "solution-user")
    solution = await _solution(db_session, contoso, status)
    workflow = await _workflow(db_session, contoso.id, solution_id=solution.id)
    execution = await _execution(
        db_session, workflow=workflow, run_user=user, actor=user, organization_id=contoso.id,
        started_by_user_id=user.id, root_execution_id=uuid4(),
    )

    authority = await load_runtime_authority(db_session, "workflow_execution", execution.id)

    assert authority.solution_id == solution.id
    assert authority.solution_active is active


@pytest.mark.asyncio
async def test_a_run_outside_any_solution_has_no_solution_state(db_session: AsyncSession) -> None:
    contoso = await _organization(db_session, "Contoso")
    user = await _person(db_session, contoso, "plain-user")
    workflow = await _workflow(db_session, contoso.id)
    execution = await _execution(
        db_session, workflow=workflow, run_user=user, actor=user, organization_id=contoso.id,
        started_by_user_id=user.id, root_execution_id=uuid4(),
    )

    authority = await load_runtime_authority(db_session, "workflow_execution", execution.id)

    assert authority.solution_id is None
    assert authority.solution_active is None


@pytest.mark.asyncio
async def test_service_and_agent_runtimes_report_their_solution_state(db_session: AsyncSession) -> None:
    contoso = await _organization(db_session, "Contoso")
    user = await _person(db_session, contoso, "solution-user")
    inactive = await _solution(db_session, contoso, "inactive")
    active = await _solution(db_session, contoso, "active")
    workflow = await _workflow(db_session, contoso.id, solution_id=inactive.id)
    definition = ServiceDefinition(workflow_id=workflow.id, organization_id=contoso.id, created_by="runtime-test")
    agent = Agent(
        name=f"runtime_agent_{uuid4().hex[:8]}", description="Runtime", system_prompt="Runtime", channels=["chat"],
        access_level=AgentAccessLevel.ROLE_BASED, organization_id=contoso.id, is_active=True,
        knowledge_sources=[], system_tools=[], created_by="runtime-test", solution_id=active.id,
    )
    db_session.add_all([definition, agent])
    await db_session.flush()
    attempt = ServiceAttempt(
        service_id=definition.id, run_user_id=user.id, lease_token="solution-lease",
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1), state="running",
    )
    run = AgentRun(
        trigger_type="chat", status="running", org_id=contoso.id, run_user_id=user.id,
        acting_user_id=user.id, agent_id=agent.id,
    )
    db_session.add_all([attempt, run])
    await db_session.flush()

    service = await load_runtime_authority(db_session, "service_attempt", attempt.id, service_id=definition.id)
    agent_run = await load_runtime_authority(db_session, "agent_run", run.id)

    assert (service.solution_id, service.solution_active) == (inactive.id, False)
    assert (agent_run.solution_id, agent_run.solution_active) == (active.id, True)


@pytest.mark.asyncio
async def test_service_and_agent_authority_reads_leave_the_callers_objects_usable(
    db_session: AsyncSession,
) -> None:
    """The service claim and agent-run consumers hold these objects in the
    session they pass in and keep reading them afterwards."""
    contoso = await _organization(db_session, "Contoso")
    user = await _person(db_session, contoso, "holder")
    workflow = await _workflow(db_session, contoso.id)
    definition = ServiceDefinition(
        workflow_id=workflow.id, organization_id=contoso.id, created_by="runtime-test", graceful_shutdown_seconds=17
    )
    db_session.add(definition)
    await db_session.flush()
    attempt = ServiceAttempt(
        service_id=definition.id, run_user_id=user.id, lease_token="held-lease", revision="r1",
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1), state="running",
    )
    run = AgentRun(
        trigger_type="chat", status="running", org_id=contoso.id, run_user_id=user.id,
        acting_user_id=user.id, input={"message": "held"},
    )
    db_session.add_all([attempt, run])
    await db_session.flush()

    await load_runtime_authority(db_session, "service_attempt", attempt.id, service_id=definition.id)
    await load_runtime_authority(db_session, "agent_run", run.id)

    assert (attempt.lease_token, attempt.revision) == ("held-lease", "r1")
    assert definition.graceful_shutdown_seconds == 17
    assert workflow.name.startswith("runtime_")
    assert (run.trigger_type, run.input) == ("chat", {"message": "held"})


@pytest.mark.asyncio
async def test_service_and_agent_authority_read_only_authority_columns(db_session: AsyncSession) -> None:
    contoso = await _organization(db_session, "Contoso")
    user = await _person(db_session, contoso, "narrow")
    workflow = await _workflow(db_session, contoso.id)
    definition = ServiceDefinition(workflow_id=workflow.id, organization_id=contoso.id, created_by="runtime-test")
    db_session.add(definition)
    await db_session.flush()
    attempt = ServiceAttempt(
        service_id=definition.id, run_user_id=user.id, lease_token="narrow-lease",
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1), state="running",
    )
    run = AgentRun(trigger_type="chat", status="running", org_id=contoso.id, run_user_id=user.id, acting_user_id=user.id)
    db_session.add_all([attempt, run])
    await db_session.flush()
    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany) -> None:
        statements.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        await load_runtime_authority(db_session, "service_attempt", attempt.id, service_id=definition.id)
        await load_runtime_authority(db_session, "agent_run", run.id)
    finally:
        event.remove(engine, "before_cursor_execute", record)

    sql = "\n".join(statements)
    for unread in (
        "service_attempts.lease_token",
        "service_attempts.revision",
        "service_definitions.graceful_shutdown_seconds",
        "service_definitions.current_revision",
        "workflows.name",
        "workflows.description",
        "agent_runs.input",
        "agent_runs.output",
        "agents.system_prompt",
        "workflow_permission_grants.created_by",
    ):
        assert unread not in sql


@pytest.fixture
async def workflow_agent(db_session):
    from src.models.orm.users import Role
    org = await _organization(db_session, "Workflow agent")
    role = Role(name=f"app-only-{uuid4().hex[:8]}", created_by="test")
    db_session.add(role)
    await db_session.flush()
    user = await _person(db_session, org, "app-only")
    user.base_role_id = role.id
    workflow = await _workflow(db_session, org.id, permission_mode="restricted")
    agent = Agent(name=f"worker-{uuid4().hex[:8]}", system_prompt="test", organization_id=org.id,
                  access_level="authenticated", created_by="test")
    db_session.add(agent)
    await db_session.flush()
    grants = [WorkflowPermissionGrant(workflow_id=workflow.id, permission=permission)
              for permission in ("agents.readbasic", "agents.execute")]
    run = AgentRun(agent_id=agent.id, trigger_type="api", status="running", org_id=org.id,
                   run_user_id=user.id, acting_user_id=user.id, permission_workflow_id=workflow.id)
    db_session.add_all([*grants, run])
    await db_session.flush()
    return org, user, workflow, agent, grants, run


@pytest.mark.asyncio
async def test_workflow_agent_authority_survives_refresh_and_revokes(db_session, workflow_agent):
    from src.services.authorization.agent_runtime import authorize_agent_execution, AgentRuntimeAuthorizationError
    from src.services.authorization.callers import load_human_caller
    from src.services.authorization.enforce import load_caller
    org, user, workflow, agent, grants, run = workflow_agent
    direct = await load_human_caller(db_session, user.id)
    with pytest.raises(AgentRuntimeAuthorizationError):
        await authorize_agent_execution(db_session, direct, agent.id)
    principal = await load_runtime_principal(db_session, {"credential_kind": "agent_run", "sub": str(run.id)})
    assert principal is not None
    caller = await load_caller(db_session, principal)
    refreshed, _ = await authorize_agent_execution(db_session, caller, agent.id)
    assert refreshed.principal.runtime_authority.workflow_id == workflow.id
    foreign = await _organization(db_session, "Foreign")
    agent.organization_id = foreign.id
    await db_session.flush()
    with pytest.raises(AgentRuntimeAuthorizationError, match="organization reach"):
        await authorize_agent_execution(db_session, caller, agent.id)
    agent.organization_id = org.id
    await db_session.delete(grants[1])
    await db_session.flush()
    with pytest.raises(AgentRuntimeAuthorizationError):
        await authorize_agent_execution(db_session, caller, agent.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("soft_delete", [False, True])
async def test_workflow_agent_refuses_deleted_permission_source(db_session, workflow_agent, soft_delete):
    from src.services.authorization.callers import CallerUnavailable, refresh_caller
    from src.services.authorization.enforce import load_caller
    _, _, workflow, _, grants, run = workflow_agent
    payload = {"credential_kind": "agent_run", "sub": str(run.id)}
    principal = await load_runtime_principal(db_session, payload)
    assert principal is not None
    caller = await load_caller(db_session, principal)
    if soft_delete:
        workflow.is_active = False
    else:
        await db_session.delete(workflow)
    await db_session.flush()
    if soft_delete:
        # Production deletion retains both the row and its grants.
        assert await db_session.get(Workflow, workflow.id) is workflow
        assert await db_session.get(WorkflowPermissionGrant, grants[1].id) is not None
    with pytest.raises(RuntimeIdentityError, match="missing workflow"):
        await load_runtime_authority(db_session, "agent_run", run.id)
    assert await load_runtime_principal(db_session, payload) is None
    with pytest.raises(CallerUnavailable):
        await refresh_caller(db_session, caller)


@pytest.mark.asyncio
async def test_workflow_agent_refuses_inactive_source_solution(db_session, workflow_agent):
    from src.services.solutions.guard import install_solution_write_guard
    install_solution_write_guard()
    org, _, workflow, _, _, run = workflow_agent
    solution = await _solution(db_session, org, "inactive")
    # Deployment uses Core writes; exercise the production guard as well.
    await db_session.execute(update(Workflow).where(Workflow.id == workflow.id).values(solution_id=solution.id))
    with pytest.raises(RuntimeIdentityError, match="Solution is inactive"):
        await load_runtime_authority(db_session, "agent_run", run.id)
