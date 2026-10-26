"""Enforced policy decisions retain the principal and outcome used live."""

from __future__ import annotations

from uuid import uuid4

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from shared import access_checks
from shared.identities import ensure_default_identity
from shared.policies.probe import evaluate_action
from src.core.principal import UserPrincipal
from src.models.contracts.policies import TablePolicies
from src.models.contracts.workflow_permissions import WorkflowPermissionMode
from src.models.orm.organizations import Organization
from src.models.orm.tables import Table
from src.models.orm.users import Role, User, UserRole
from src.services.access_check_policies import (
    enforced_policy_principal,
    record_table_policy,
)
from src.services.authorization.context import AuthorizationContext
from src.services.authorization.explain import Powers, RunUser
from src.services.authorization.runtime import RuntimeAuthority


HR_RULE = {
    "name": "hr",
    "actions": ["create"],
    "when": {"call": "has_role", "args": ["HR"]},
}
ADMIN_RULE = {
    "name": "admins",
    "actions": ["create"],
    "when": {"eq": [{"user": "is_platform_admin"}, True]},
}
AUTHORITY_RULE = {
    "name": "full-workflow-authority",
    "actions": ["create"],
    "when": {"authority": "platform_admin"},
}


def _policies(*rules: dict) -> TablePolicies:
    return TablePolicies.model_validate({"policies": list(rules)})


@pytest_asyncio.fixture
async def db_session(async_engine):
    async with async_engine.connect() as connection:
        transaction = await connection.begin()
        async with AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        ) as session:
            try:
                yield session
            finally:
                await session.rollback()
                await transaction.rollback()


@pytest_asyncio.fixture
async def world(db_session: AsyncSession):
    org = Organization(name=f"Policy Org {uuid4().hex[:8]}", created_by="policy-test")
    db_session.add(org)
    await db_session.flush()
    await ensure_default_identity(db_session, org)
    original = User(email=f"{uuid4()}@policy.example", name="Original", organization_id=org.id)
    actor = User(email=f"{uuid4()}@policy.example", name="Actor", organization_id=org.id)
    hr = Role(name=f"HR-{uuid4().hex[:8]}", created_by="policy-test")
    table = Table(name=f"policy_{uuid4().hex[:8]}", organization_id=org.id)
    db_session.add_all([original, actor, hr, table])
    await db_session.flush()
    db_session.add(UserRole(user_id=actor.id, role_id=hr.id, assigned_by="policy-test"))
    await db_session.flush()
    return {"org": org, "original": original, "actor": actor, "table": table, "hr": hr}


async def test_actual_policy_denial_is_recorded_as_enforced(db_session, world) -> None:
    policies = _policies(HR_RULE)
    principal = UserPrincipal(
        user_id=world["original"].id,
        email=world["original"].email,
        organization_id=world["org"].id,
        name="Original",
    )
    token = access_checks.collect_run(uuid4(), principal.user_id, None)
    try:
        await record_table_policy(
            db_session,
            world["table"],
            policies,
            principal,
            action="create",
            allowed=False,
        )
        collector = access_checks.current()
        assert collector is not None
        [note] = collector.notes
    finally:
        access_checks.stop_collecting(token)

    assert note.facts["enforced"] is True
    assert note.facts["today"] is note.facts["model"] is False
    assert note.facts["missing"] == ["role:HR"]
    assert note.decision is not None
    assert note.decision[1].outcome == "failure"


async def test_full_policy_authority_admits_admin_rules_without_a_role_or_actor_role(
    db_session, world
) -> None:
    context = AuthorizationContext(
        user_id=world["original"].id,
        home_organization_id=world["org"].id,
        base_role_id=uuid4(),
        is_external=False,
        base_permissions=frozenset(),
    )
    principal = UserPrincipal(
        user_id=world["actor"].id,
        email=world["actor"].email,
        organization_id=world["org"].id,
        name="Actor",
    )
    principal.runtime_authority = RuntimeAuthority(
        kind="workflow_execution",
        credential_id=uuid4(),
        run_user=RunUser(world["original"].id, context, None, is_active=True),
        powers=Powers(WorkflowPermissionMode.FULL, ()),
        acting_user_id=world["actor"].id,
        organization_id=world["org"].id,
        workflow_id=uuid4(),
        started_by_user_id=world["original"].id,
        root_execution_id=uuid4(),
    )

    policy_principal = await enforced_policy_principal(db_session, principal)

    assert policy_principal.user_id == world["original"].id
    assert policy_principal.role_ids == []
    assert policy_principal.is_superuser is False
    assert evaluate_action("create", _policies(AUTHORITY_RULE), {}, policy_principal)
    assert evaluate_action("create", _policies(ADMIN_RULE), {}, policy_principal)
    assert not evaluate_action("create", _policies(HR_RULE), {}, policy_principal)


async def test_direct_policy_success_does_not_create_an_access_check(db_session, world) -> None:
    principal = UserPrincipal(
        user_id=world["original"].id,
        email=world["original"].email,
        organization_id=world["org"].id,
        name="Original",
    )
    token = access_checks.collect_person(principal.user_id)
    assert token is not None
    try:
        await record_table_policy(
            db_session,
            world["table"],
            _policies(HR_RULE),
            principal,
            action="create",
            allowed=True,
        )
        collector = access_checks.current()
        assert collector is not None
        assert collector.notes == []
    finally:
        access_checks.stop_collecting(token)


async def test_bulk_decisions_load_the_run_user_once_per_request(
    db_session, world, monkeypatch
) -> None:
    from src.services import access_check_policies

    loads: list[object] = []
    real_load = access_check_policies.load_run_user

    async def counting_load(db, user_id):
        loads.append(user_id)
        return await real_load(db, user_id)

    monkeypatch.setattr(access_check_policies, "load_run_user", counting_load)
    principal = UserPrincipal(
        user_id=world["original"].id,
        email=world["original"].email,
        organization_id=world["org"].id,
        name="Original",
    )
    token = access_checks.collect_run(uuid4(), principal.user_id, None)
    try:
        for allowed in (False, True, False):
            await record_table_policy(
                db_session,
                world["table"],
                _policies(HR_RULE),
                principal,
                action="create",
                allowed=allowed,
            )
        collector = access_checks.current()
        assert collector is not None
        notes = list(collector.notes)
    finally:
        access_checks.stop_collecting(token)

    assert loads == [principal.user_id]
    assert [n.facts["today"] for n in notes] == [False, True, False]


async def test_verified_runtime_recording_reuses_its_verified_policy_subject(
    db_session, world, monkeypatch
) -> None:
    """Recording keeps the enforcement trace without reloading runtime facts."""
    from src.services import access_check_policies

    context = AuthorizationContext(
        user_id=world["original"].id,
        home_organization_id=world["org"].id,
        base_role_id=uuid4(),
        is_external=False,
        base_permissions=frozenset(),
    )
    runtime = UserPrincipal(
        user_id=world["original"].id,
        email=world["original"].email,
        organization_id=world["org"].id,
        name="Original",
    )
    runtime.runtime_authority = RuntimeAuthority(
        kind="workflow_execution",
        credential_id=uuid4(),
        run_user=RunUser(world["original"].id, context, None, is_active=True),
        powers=Powers(WorkflowPermissionMode.FULL, ()),
        acting_user_id=world["original"].id,
        organization_id=world["org"].id,
        workflow_id=uuid4(),
        started_by_user_id=world["original"].id,
        root_execution_id=uuid4(),
    )
    current_policy_principal = await enforced_policy_principal(db_session, runtime)

    async def unexpected_run_user_load(*_args, **_kwargs):
        raise AssertionError("recording reloaded the verified run user")

    async def unexpected_powers_load(*_args, **_kwargs):
        raise AssertionError("recording reloaded the verified workflow powers")

    monkeypatch.setattr(access_check_policies, "load_run_user", unexpected_run_user_load)
    monkeypatch.setattr(access_check_policies, "load_powers", unexpected_powers_load)
    token = access_checks.start_collecting(
        {"credential_kind": "workflow_execution", "sub": str(runtime.runtime_authority.credential_id)},
        principal=runtime,
    )
    try:
        await record_table_policy(
            db_session,
            world["table"],
            _policies(AUTHORITY_RULE),
            current_policy_principal,
            action="create",
            allowed=True,
        )
        collector = access_checks.current()
        assert collector is not None
        [note] = collector.notes
    finally:
        access_checks.stop_collecting(token)

    assert note.decision is not None
    assert note.decision[1].outcome == "success"
    assert note.facts["today"] is note.facts["model"] is True
