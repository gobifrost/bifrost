"""Loading the run user and the workflow's powers for report-only checks."""

from __future__ import annotations

from uuid import uuid4

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.identities import ensure_default_identity
from shared.sdk_users import set_platform_admin
from src.core.constants import PROVIDER_ORG_ID, SYSTEM_USER_UUID
from src.models.contracts.workflow_permissions import WorkflowPermissionMode
from src.models.enums import IdentityKind
from src.models.orm.organizations import Organization
from src.models.orm.users import User
from src.models.orm.workflow_permissions import WorkflowPermissionGrant
from src.models.orm.workflows import Workflow
from src.services.authorization.explain import load_powers, load_run_as_target, load_run_user


@pytest_asyncio.fixture
async def db_session(async_engine):
    async with async_engine.connect() as connection:
        transaction = await connection.begin()
        async with AsyncSession(
            bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        ) as session:
            try:
                yield session
            finally:
                await session.rollback()
                await transaction.rollback()


async def _workflow(session: AsyncSession, **fields) -> Workflow:
    name = f"explain_{uuid4().hex[:8]}"
    workflow = Workflow(name=name, function_name=name, path=f"workflows/{name}.py", **fields)
    session.add(workflow)
    await session.flush()
    return workflow


async def test_a_run_user_that_no_longer_exists_loads_as_none(db_session: AsyncSession) -> None:
    assert await load_run_user(db_session, uuid4()) is None


async def test_an_org_identity_loads_with_its_kind_and_home(db_session: AsyncSession) -> None:
    org = Organization(name=f"Explain Org {uuid4().hex[:8]}", created_by="explain-test")
    db_session.add(org)
    await db_session.flush()
    await ensure_default_identity(db_session, org)
    identity_id = (
        await db_session.execute(
            select(User.id).where(User.identity_kind == IdentityKind.ORG_DEFAULT, User.organization_id == org.id)
        )
    ).scalar_one()

    run_user = await load_run_user(db_session, identity_id)

    assert run_user is not None
    assert run_user.identity_kind == "org_default"
    assert run_user.home == org.id
    assert run_user.is_platform_admin is False


async def test_powers_follow_the_workflow_mode_and_grants(db_session: AsyncSession) -> None:
    full = await _workflow(db_session)
    restricted = await _workflow(db_session, permission_mode="restricted")
    db_session.add(WorkflowPermissionGrant(workflow_id=restricted.id, permission="secrets.read"))
    await db_session.flush()

    assert (await load_powers(db_session, full.id)).mode is WorkflowPermissionMode.FULL
    powers = await load_powers(db_session, restricted.id)
    assert powers.mode is WorkflowPermissionMode.RESTRICTED
    assert [grant.permission for grant in powers.grants] == ["secrets.read"]
    assert (await load_powers(db_session, None)).mode is WorkflowPermissionMode.FULL


async def test_a_run_as_user_that_does_not_exist_loads_as_none(db_session: AsyncSession) -> None:
    target = await load_run_as_target(db_session, uuid4())

    assert target is None


async def test_a_run_as_user_loads_with_home_and_privilege(db_session: AsyncSession) -> None:
    org = Organization(name=f"Contoso {uuid4().hex[:8]}", created_by="explain-test")
    db_session.add(org)
    await db_session.flush()
    person = User(email=f"{uuid4()}@contoso.example", name="Contoso Person", organization_id=org.id)
    admin = User(email=f"{uuid4()}@provider.example", name="Admin", organization_id=PROVIDER_ORG_ID)
    db_session.add_all([person, admin])
    await db_session.flush()
    await set_platform_admin(db_session, admin, True, assigned_by="explain-test")

    target = await load_run_as_target(db_session, person.id)
    assert target is not None
    assert (target.organization_id, target.is_active, target.identity_kind) == (org.id, True, None)
    assert (target.is_system, target.is_provider_org, target.privileged) == (False, False, False)
    assert (target.email, target.name) == (person.email, "Contoso Person")
    admin_target = await load_run_as_target(db_session, admin.id)
    assert admin_target is not None
    assert (admin_target.is_provider_org, admin_target.privileged) == (True, True)


async def test_the_system_account_loads_as_the_system_account(db_session: AsyncSession) -> None:
    target = await load_run_as_target(db_session, SYSTEM_USER_UUID)

    assert target is not None and target.is_system
