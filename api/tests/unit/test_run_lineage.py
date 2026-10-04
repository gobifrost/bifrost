"""Run lineage: who each run is for, decided where it starts.

DB-backed: the migrated test database already holds the global identity and
a default identity for the provider organization.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.identities import ensure_default_identity
from shared.run_lineage import (
    RunLineage,
    child_lineage,
    identity_lineage,
    lineage_columns,
    person_lineage,
    principal_lineage,
    unattended_lineage,
)
from src.models.enums import IdentityKind
from src.core.constants import PROVIDER_ORG_ID, SYSTEM_USER_ID
from src.core.principal import UserPrincipal
from src.models.orm.executions import Execution
from src.models.orm.organizations import Organization
from src.models.orm.users import User
from src.models.orm.workflows import Workflow


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


async def _identity_id(session: AsyncSession, organization_id: UUID | None) -> UUID:
    query = (
        select(User.id).where(User.identity_kind == IdentityKind.GLOBAL_DEFAULT)
        if organization_id is None
        else select(User.id).where(
            User.identity_kind == IdentityKind.ORG_DEFAULT, User.organization_id == organization_id
        )
    )
    return (await session.execute(query)).scalar_one()


async def _org(session: AsyncSession) -> Organization:
    org = Organization(name=f"Lineage Org {uuid4().hex[:8]}", created_by="lineage-test")
    session.add(org)
    await session.flush()
    await ensure_default_identity(session, org)
    return org


async def _person(session: AsyncSession, org: Organization) -> User:
    person = User(email=f"{uuid4()}@lineage.example", name="Person", organization_id=org.id)
    session.add(person)
    await session.flush()
    return person


async def _workflow(session: AsyncSession, organization_id: UUID | None, run_identity_id: UUID | None = None) -> Workflow:
    name = f"lineage_{uuid4().hex[:8]}"
    workflow = Workflow(
        name=name,
        function_name=name,
        path=f"workflows/{name}.py",
        organization_id=organization_id,
        run_identity_id=run_identity_id,
    )
    session.add(workflow)
    await session.flush()
    return workflow


async def _execution(session: AsyncSession, **lineage: UUID | None) -> Execution:
    execution = Execution(workflow_name="parent", executed_by_name="Parent", **lineage)
    session.add(execution)
    await session.flush()
    return execution


def test_person_lineage_is_rooted_at_its_own_execution() -> None:
    person_id = uuid4()
    execution_id = uuid4()

    lineage = person_lineage(str(person_id))

    assert lineage == RunLineage(run_user_id=person_id, started_by_user_id=person_id, root_execution_id=None)
    assert lineage.bound(execution_id) == {
        "run_user_id": str(person_id),
        "started_by_user_id": str(person_id),
        "root_execution_id": str(execution_id),
    }


def test_bound_keeps_an_inherited_root() -> None:
    root = uuid4()
    lineage = RunLineage(run_user_id=uuid4(), started_by_user_id=uuid4(), root_execution_id=root)

    assert lineage.bound(uuid4())["root_execution_id"] == str(root)


def test_lineage_columns_maps_a_bound_lineage_to_row_values() -> None:
    person_id, root = uuid4(), uuid4()
    bound = {"run_user_id": str(person_id), "started_by_user_id": str(person_id), "root_execution_id": str(root)}

    assert lineage_columns(bound) == {
        "run_user_id": person_id,
        "started_by_user_id": person_id,
        "root_execution_id": root,
    }
    assert lineage_columns(None) == {"run_user_id": None, "started_by_user_id": None, "root_execution_id": None}


@pytest.mark.asyncio
async def test_identity_lineage_uses_the_organization_default_or_the_global_identity(db_session: AsyncSession) -> None:
    global_identity = await _identity_id(db_session, None)
    provider_identity = await _identity_id(db_session, PROVIDER_ORG_ID)

    assert await identity_lineage(db_session, None) == RunLineage(global_identity, global_identity, None)
    assert await identity_lineage(db_session, PROVIDER_ORG_ID) == RunLineage(provider_identity, provider_identity, None)


@pytest.mark.asyncio
async def test_unattended_lineage_follows_the_workflow_identity(db_session: AsyncSession) -> None:
    org = await _org(db_session)
    org_identity = await _identity_id(db_session, org.id)
    global_identity = await _identity_id(db_session, None)
    provider_identity = await _identity_id(db_session, PROVIDER_ORG_ID)

    org_workflow = await _workflow(db_session, org.id)
    global_workflow = await _workflow(db_session, None)
    assigned_workflow = await _workflow(db_session, None, run_identity_id=provider_identity)

    assert await unattended_lineage(db_session, org_workflow.id) == RunLineage(org_identity, org_identity, None)
    assert await unattended_lineage(db_session, global_workflow.id) == RunLineage(global_identity, global_identity, None)
    assert await unattended_lineage(db_session, str(assigned_workflow.id)) == RunLineage(
        provider_identity, provider_identity, None
    )


@pytest.mark.asyncio
async def test_child_lineage_copies_the_parent_row(db_session: AsyncSession) -> None:
    org = await _org(db_session)
    person = await _person(db_session, org)
    root = uuid4()
    parent = await _execution(
        db_session, run_user_id=person.id, started_by_user_id=person.id, root_execution_id=root
    )

    assert await child_lineage(db_session, str(parent.id)) == RunLineage(person.id, person.id, root)


@pytest.mark.asyncio
async def test_child_lineage_is_unknown_when_the_parent_recorded_none(db_session: AsyncSession) -> None:
    parent = await _execution(db_session)

    assert await child_lineage(db_session, parent.id) is None
    assert await child_lineage(db_session, uuid4()) is None


@pytest.mark.asyncio
async def test_principal_lineage_by_principal_shape(db_session: AsyncSession) -> None:
    org = await _org(db_session)
    person = await _person(db_session, org)
    org_identity = await _identity_id(db_session, org.id)
    global_identity = await _identity_id(db_session, None)
    root = uuid4()
    parent = await _execution(
        db_session, run_user_id=person.id, started_by_user_id=person.id, root_execution_id=root
    )

    def principal(**fields) -> UserPrincipal:
        base = {"user_id": person.id, "email": "p@lineage.example", "organization_id": org.id}
        return UserPrincipal(**{**base, **fields})

    engine_child = principal(
        user_id=UUID(SYSTEM_USER_ID), organization_id=None, is_superuser=True, engine_execution_id=str(parent.id)
    )
    service = principal(
        user_id=UUID(SYSTEM_USER_ID), is_superuser=True, engine_execution_id=str(parent.id), service_id=str(uuid4())
    )
    embed = principal(user_id=uuid4(), embed=True, embed_kind="form", grant="public")
    global_embed = principal(user_id=uuid4(), organization_id=None, embed=True, embed_kind="form", grant="public")
    system = principal(user_id=UUID(SYSTEM_USER_ID), organization_id=None, is_superuser=True)

    assert await principal_lineage(db_session, engine_child) == RunLineage(person.id, person.id, root)
    assert await principal_lineage(db_session, service) == RunLineage(org_identity, org_identity, None)
    assert await principal_lineage(db_session, embed) == RunLineage(org_identity, org_identity, None)
    assert await principal_lineage(db_session, global_embed) == RunLineage(global_identity, global_identity, None)
    assert await principal_lineage(db_session, principal()) == RunLineage(person.id, person.id, None)
    assert await principal_lineage(db_session, system) == person_lineage(SYSTEM_USER_ID)


async def test_principal_lineage_reads_the_run_user_a_bridge_token_carries(db_session: AsyncSession) -> None:
    org = await _org(db_session)
    org_identity = await _identity_id(db_session, org.id)
    bridge = UserPrincipal(
        user_id=UUID(SYSTEM_USER_ID),
        email="system@lineage.example",
        organization_id=org.id,
        run_user_id=org_identity,
        started_by_user_id=org_identity,
    )

    assert await principal_lineage(db_session, bridge) == RunLineage(org_identity, org_identity, None)
